import { localStore, STORAGE_KEYS } from "./storage";
import type {
  AdminModelConfig,
  DashboardSummary,
  ListResponse,
  LoginResponse,
  MeResponse,
  SelectBrandResponse,
} from "./types";

export const API_BASE_URL =
  process.env.NEXT_PUBLIC_API_BASE_URL || "http://localhost:8000";

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
    this.name = "ApiError";
  }
}

/** Thrown when an authenticated request 401s and the refresh attempt also fails. */
export class AuthExpiredError extends Error {
  constructor() {
    super("Сессия истекла");
    this.name = "AuthExpiredError";
  }
}

async function parseErrorBody(res: Response): Promise<string> {
  try {
    const body = await res.json();
    if (body && typeof body.detail === "string") return body.detail;
    return JSON.stringify(body);
  } catch {
    return res.statusText || `HTTP ${res.status}`;
  }
}

interface RequestOptions {
  method?: string;
  body?: unknown;
  token?: string | null;
  /** Skip the automatic refresh-and-retry-once behavior (used by the refresh call itself). */
  noAuthRetry?: boolean;
  signal?: AbortSignal;
}

async function rawFetch(path: string, opts: RequestOptions = {}): Promise<Response> {
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  if (opts.token) headers["Authorization"] = `Bearer ${opts.token}`;
  return fetch(`${API_BASE_URL}${path}`, {
    method: opts.method || "GET",
    headers,
    body: opts.body !== undefined ? JSON.stringify(opts.body) : undefined,
    signal: opts.signal,
  });
}

/**
 * Perform a request that requires the brand-scoped access token.
 * On 401, attempts a single `/auth/refresh` using the stored refresh_token,
 * updates localStorage, and retries the request once. If refresh also fails,
 * throws AuthExpiredError so callers can redirect to /login.
 */
export async function authedFetch<T>(path: string, opts: RequestOptions = {}): Promise<T> {
  const token = localStore.get(STORAGE_KEYS.accessToken);
  let res = await rawFetch(path, { ...opts, token });

  if (res.status === 401 && !opts.noAuthRetry) {
    const refreshed = await tryRefresh();
    if (!refreshed) {
      throw new AuthExpiredError();
    }
    res = await rawFetch(path, { ...opts, token: refreshed.access_token });
  }

  if (!res.ok) {
    const message = await parseErrorBody(res);
    if (res.status === 401) throw new AuthExpiredError();
    throw new ApiError(res.status, message);
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

export async function tryRefresh(): Promise<SelectBrandResponse | null> {
  const refreshToken = localStore.get(STORAGE_KEYS.refreshToken);
  if (!refreshToken) return null;
  try {
    const res = await rawFetch("/auth/refresh", {
      method: "POST",
      body: { refresh_token: refreshToken },
      noAuthRetry: true,
    });
    if (!res.ok) return null;
    const data = (await res.json()) as SelectBrandResponse;
    localStore.set(STORAGE_KEYS.accessToken, data.access_token);
    localStore.set(STORAGE_KEYS.refreshToken, data.refresh_token);
    localStore.set(STORAGE_KEYS.brandId, data.brand_id);
    localStore.set(STORAGE_KEYS.role, data.role);
    return data;
  } catch {
    return null;
  }
}

// ---------------------------------------------------------------------------
// Auth endpoints (unauthenticated / pre-auth token based)
// ---------------------------------------------------------------------------

export async function login(email: string, password: string): Promise<LoginResponse> {
  const res = await rawFetch("/auth/login", { method: "POST", body: { email, password } });
  if (!res.ok) {
    const message = await parseErrorBody(res);
    throw new ApiError(res.status, message);
  }
  return (await res.json()) as LoginResponse;
}

export async function selectBrand(
  preAuthToken: string,
  brandId: string
): Promise<SelectBrandResponse> {
  const res = await rawFetch("/auth/select-brand", {
    method: "POST",
    token: preAuthToken,
    body: { brand_id: brandId },
  });
  if (!res.ok) {
    const message = await parseErrorBody(res);
    throw new ApiError(res.status, message);
  }
  return (await res.json()) as SelectBrandResponse;
}

export async function fetchMe(preAuthToken: string): Promise<MeResponse> {
  const res = await rawFetch("/auth/me", { token: preAuthToken });
  if (!res.ok) {
    const message = await parseErrorBody(res);
    throw new ApiError(res.status, message);
  }
  return (await res.json()) as MeResponse;
}

// ---------------------------------------------------------------------------
// Authenticated (brand-scoped) endpoints
// ---------------------------------------------------------------------------

export function fetchSchema(): Promise<AdminModelConfig[]> {
  return authedFetch<AdminModelConfig[]>("/admin/schema");
}

export function fetchDashboardSummary(): Promise<DashboardSummary> {
  return authedFetch<DashboardSummary>("/dashboard/summary");
}

export function fetchModelList(
  key: string,
  params: Record<string, string | number | undefined>
): Promise<ListResponse> {
  const query = new URLSearchParams();
  Object.entries(params).forEach(([k, v]) => {
    if (v !== undefined && v !== "") query.set(k, String(v));
  });
  const qs = query.toString();
  return authedFetch<ListResponse>(`/admin/${key}${qs ? `?${qs}` : ""}`);
}

export function fetchModelDetail(
  key: string,
  id: string | number
): Promise<Record<string, unknown>> {
  return authedFetch<Record<string, unknown>>(`/admin/${key}/${id}`);
}

/**
 * PATCH a Transaction. Its own dedicated endpoint (not the generic
 * `/admin/{key}/{id}`) because editing one runs real balance-mutation side
 * effects (`app.services.transaction_write_service`), not a plain column
 * update — see the backend for the full write-path story. Only fields the
 * backend registry marks `editable_fields` for "transactions" are accepted;
 * sending anything else 400s.
 */
export function updateTransaction(
  id: string | number,
  values: Record<string, string | null>
): Promise<Record<string, unknown>> {
  return authedFetch<Record<string, unknown>>(`/admin/transactions/${id}`, {
    method: "PATCH",
    body: values,
  });
}

export interface CommissionContext {
  available: boolean;
  merchant_personal_rate?: string;
  partner_rate?: string;
  direction?: string;
}

/**
 * Rates needed to live-recompute commission/partner_income/pure_our_income/
 * amount_after_commission as the operator edits `amount` — port of the
 * source's `static/admin/js/transaction_changes.js`. `available: false`
 * means source wouldn't have shown a live preview either (no matching
 * MerchantPaymentMethod) — those fields stay plain manual inputs.
 */
export function fetchCommissionContext(id: string | number): Promise<CommissionContext> {
  return authedFetch<CommissionContext>(`/admin/transactions/${id}/commission-context`);
}

export interface SettlementWriteResponse {
  settlement: Record<string, unknown>;
  transaction: Record<string, unknown>;
}

/**
 * Create a Settlement. Creating one also creates its linked Transaction
 * and runs the same balance side effects a Transaction edit does — see
 * `app.services.settlement_write_service`. `settl_type`/`balance_merchant_id`/
 * `balance_partner_id` are required and only settable here, never via PATCH.
 */
export function createSettlement(
  values: Record<string, string | number | null>
): Promise<SettlementWriteResponse> {
  return authedFetch<SettlementWriteResponse>("/admin/settlements", {
    method: "POST",
    body: values,
  });
}

/** PATCH an existing Settlement. Editing one updates its linked Transaction
 * in place and re-runs the balance side effects — not a plain column update. */
export function updateSettlement(
  id: string | number,
  values: Record<string, string | null>
): Promise<SettlementWriteResponse> {
  return authedFetch<SettlementWriteResponse>(`/admin/settlements/${id}`, {
    method: "PATCH",
    body: values,
  });
}

/**
 * PATCH a PaymentMethodCompany. Its own endpoint because saving one busts a
 * Redis-backed cache (`app.services.payment_method_write_service` /
 * `app.services.cache_invalidation`) whenever is_active/priority/limit
 * fields change — plain column edits (e.g. partner_rate alone) do NOT bust
 * the cache, matching source's `cache_invalidation_fields` set exactly.
 */
export function updatePaymentMethodCompany(
  id: string | number,
  values: Record<string, string | null>
): Promise<Record<string, unknown>> {
  return authedFetch<Record<string, unknown>>(`/admin/payment-method-companies/${id}`, {
    method: "PATCH",
    body: values,
  });
}

/**
 * PATCH a MerchantPaymentMethod. Its own endpoint because EVERY save busts
 * the merchant's Redis method-lookup cache unconditionally — no diff check
 * in source, preserved here (see `app.services.payment_method_write_service`).
 */
export function updateMerchantPaymentMethod(
  id: string | number,
  values: Record<string, string | null>
): Promise<Record<string, unknown>> {
  return authedFetch<Record<string, unknown>>(`/admin/merchant-payment-methods/${id}`, {
    method: "PATCH",
    body: values,
  });
}

// ---------------------------------------------------------------------------
// PaymentMethodCascade / PaymentMethodCascadeItem — the validation state
// machine ported in `app.services.cascade_write_service`. Own endpoints
// (not the generic admin engine) since create/update/delete here run real
// cross-record validation (payment_method matching, priority uniqueness).
// ---------------------------------------------------------------------------

export function createCascade(values: {
  name: string;
  payment_method_id: number;
  description?: string | null;
  is_active?: boolean;
}): Promise<Record<string, unknown>> {
  return authedFetch<Record<string, unknown>>("/admin/payment-method-cascades", {
    method: "POST",
    body: values,
  });
}

/** payment_method_id is deliberately not accepted here — immutable after
 * creation, matching source locking it as readonly on the edit form. */
export function updateCascade(
  id: string | number,
  values: { name?: string; description?: string | null; is_active?: boolean }
): Promise<Record<string, unknown>> {
  return authedFetch<Record<string, unknown>>(`/admin/payment-method-cascades/${id}`, {
    method: "PATCH",
    body: values,
  });
}

export function createCascadeItem(
  cascadeId: string | number,
  values: { payment_method_company_id: number; priority: number; is_active?: boolean }
): Promise<Record<string, unknown>> {
  return authedFetch<Record<string, unknown>>(`/admin/payment-method-cascades/${cascadeId}/items`, {
    method: "POST",
    body: values,
  });
}

export function updateCascadeItem(
  id: string | number,
  values: { payment_method_company_id?: number; priority?: number; is_active?: boolean }
): Promise<Record<string, unknown>> {
  return authedFetch<Record<string, unknown>>(`/admin/payment-method-cascade-items/${id}`, {
    method: "PATCH",
    body: values,
  });
}

export function deleteCascadeItem(id: string | number): Promise<void> {
  return authedFetch<void>(`/admin/payment-method-cascade-items/${id}`, { method: "DELETE" });
}

// ---------------------------------------------------------------------------
// Merchant bulk actions — port of `MerchantAdmin.apply_template_to_merchants`
// / `apply_selected_methods_to_merchants` (`app.services.merchant_bulk_actions_service`).
// Both provision MerchantPaymentMethod rows for many merchants at once.
// ---------------------------------------------------------------------------

export interface BulkApplyResult {
  created_count: number;
  skipped_existing_count: number;
}

export function applyTemplateToMerchants(values: {
  merchant_ids: number[];
  template_id: number;
}): Promise<BulkApplyResult> {
  return authedFetch<BulkApplyResult>("/admin/merchants/bulk-actions/apply-template", {
    method: "POST",
    body: values,
  });
}

export function applySelectedMethodsToMerchants(values: {
  merchant_ids: number[];
  currency_id: number;
  direction: string;
  method_ids: number[];
  personal_rate: string;
  transaction_min_limit?: string | null;
  transaction_max_limit?: string | null;
  test_mode?: boolean;
  only_admin_configure?: boolean;
}): Promise<BulkApplyResult> {
  return authedFetch<BulkApplyResult>("/admin/merchants/bulk-actions/apply-selected-methods", {
    method: "POST",
    body: values,
  });
}

// ---------------------------------------------------------------------------
// MerchantBalance raw-SQL recompute — port of `MerchantBalanceAdmin.refresh_balances`
// (`app.services.merchant_balance_service`). Superadmin-only, matching
// source's `request.user.is_superuser` gate.
// ---------------------------------------------------------------------------

export function refreshMerchantBalances(): Promise<{ updated_count: number }> {
  return authedFetch<{ updated_count: number }>("/admin/merchant-balances/refresh", { method: "POST" });
}

// ---------------------------------------------------------------------------
// Explicit "nuke the cache" admin actions — port of `clear_cache_by_currency`
// / `clear_cache_by_merchant` / `clear_all_payment_methods_cache`
// (`app.services.cache_clear_actions_service`). Distinct from the automatic
// invalidation a normal PATCH already triggers.
// ---------------------------------------------------------------------------

export function clearCacheByCurrency(paymentMethodCompanyIds: number[]): Promise<{ currencies: string[] }> {
  return authedFetch<{ currencies: string[] }>("/admin/payment-methods-cache/clear-by-currency", {
    method: "POST",
    body: { payment_method_company_ids: paymentMethodCompanyIds },
  });
}

export function clearCacheByMerchant(
  merchantPaymentMethodIds: number[]
): Promise<{ merchants: { id: number; name: string }[] }> {
  return authedFetch<{ merchants: { id: number; name: string }[] }>("/admin/payment-methods-cache/clear-by-merchant", {
    method: "POST",
    body: { merchant_payment_method_ids: merchantPaymentMethodIds },
  });
}

export function clearAllPaymentMethodsCache(): Promise<{ status: string }> {
  return authedFetch<{ status: string }>("/admin/payment-methods-cache/clear-all", { method: "POST" });
}

// ---------------------------------------------------------------------------
// Generic create/update/delete — `app.services.generic_write_service`.
// For "plain" models (no dedicated write endpoint, e.g. no real business
// logic on save) — config-driven off `creatable_fields`/`editable_fields`/
// `creatable`/`deletable` from `/admin/schema`. Models with their own
// dedicated endpoint (transactions, settlements, ...) never reach this —
// the backend's dedicated routers are matched first regardless.
// ---------------------------------------------------------------------------

export function createRecord(
  modelKey: string,
  values: Record<string, string | null>
): Promise<Record<string, unknown>> {
  return authedFetch<Record<string, unknown>>(`/admin/${modelKey}`, {
    method: "POST",
    body: values,
  });
}

export function updateRecordGeneric(
  modelKey: string,
  id: string | number,
  values: Record<string, string | null>
): Promise<Record<string, unknown>> {
  return authedFetch<Record<string, unknown>>(`/admin/${modelKey}/${id}`, {
    method: "PATCH",
    body: values,
  });
}

export function deleteRecord(modelKey: string, id: string | number): Promise<void> {
  return authedFetch<void>(`/admin/${modelKey}/${id}`, { method: "DELETE" });
}
