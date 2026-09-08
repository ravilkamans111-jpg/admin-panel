"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useSchema } from "@/lib/schema-context";
import {
  AuthExpiredError,
  ApiError,
  fetchModelDetail,
  fetchCommissionContext,
  updateTransaction,
  updateSettlement,
  updatePaymentMethodCompany,
  updateMerchantPaymentMethod,
  type CommissionContext,
} from "@/lib/api";
import { useAuth } from "@/lib/auth-context";
import { formatCellValue, formatDateMaybe, humanizeFieldName } from "@/lib/format";
import { StatusBadge } from "@/components/StatusBadge";

const ENHANCED_KEYS = new Set(["transactions", "merchant-balances", "settlements"]);

// Only these two have a real write endpoint today — each its own dedicated
// backend router (`app.api.transaction_writes` / `app.api.settlement_writes`),
// not the generic admin engine, since both run real balance-mutation side
// effects on save. `config.is_writable` also gates this, but the actual
// PATCH call is model-specific, so it stays keyed here explicitly rather
// than being fully generic.
const WRITABLE_KEYS_WITH_DEDICATED_ENDPOINT = new Set([
  "transactions",
  "settlements",
  "payment-method-companies",
  "merchant-payment-methods",
]);

// Dispatch table for the model-specific PATCH call — each of these runs
// real side effects on save (balance math, Redis cache invalidation), so
// there's no single generic "update" call to fall back to.
const UPDATE_FN: Record<string, (id: string | number, values: Record<string, string | null>) => Promise<Record<string, unknown>>> = {
  transactions: updateTransaction,
  "payment-method-companies": updatePaymentMethodCompany,
  "merchant-payment-methods": updateMerchantPaymentMethod,
};

// NOT_FOUND isn't in the source `StatusChoices` enum (`models_choices.py`)
// but shows up as real legacy data in production-like DBs (confirmed:
// 258/464 rows in a real AmPay test DB) — included here so such a row is
// still editable/displayable rather than silently unselectable.
const STATUS_CHOICES = ["ACCEPTED", "SUCCESS", "DECLINED", "APPEAL", "NOT_FOUND"];

export default function AdminModelDetailPage() {
  const params = useParams<{ key: string; id: string }>();
  const { key: modelKey, id } = params;
  const { getConfig, loading: schemaLoading } = useSchema();
  const { logout } = useAuth();
  const router = useRouter();

  const config = getConfig(modelKey);
  const canEdit = Boolean(config?.is_writable) && WRITABLE_KEYS_WITH_DEDICATED_ENDPOINT.has(modelKey);

  const [record, setRecord] = useState<Record<string, unknown> | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [notFound, setNotFound] = useState(false);

  const [editing, setEditing] = useState(false);
  const [form, setForm] = useState<Record<string, string>>({});
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [savedNotice, setSavedNotice] = useState(false);
  const [commissionContext, setCommissionContext] = useState<CommissionContext | null>(null);

  function load() {
    let cancelled = false;
    setLoading(true);
    setError(null);
    setNotFound(false);
    fetchModelDetail(modelKey, id)
      .then((data) => {
        if (!cancelled) setRecord(data);
      })
      .catch((err) => {
        if (cancelled) return;
        if (err instanceof AuthExpiredError) {
          logout();
          router.replace("/login");
          return;
        }
        if (err instanceof ApiError && err.status === 404) {
          setNotFound(true);
        } else if (err instanceof ApiError) {
          setError(err.message);
        } else {
          setError("Не удалось загрузить запись.");
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }

  useEffect(load, [modelKey, id]); // eslint-disable-line react-hooks/exhaustive-deps

  async function startEditing() {
    if (!record || !config) return;
    const initial: Record<string, string> = {};
    for (const field of config.editable_fields) {
      const value = record[field];
      initial[field] = value === null || value === undefined ? "" : String(value);
    }
    setForm(initial);
    setSaveError(null);
    setSavedNotice(false);
    setCommissionContext(null);
    setEditing(true);

    // Port of `TransactionAdminForm`'s live commission preview
    // (`static/admin/js/transaction_changes.js`) — only meaningful for
    // transactions, and only if the source's own MerchantPaymentMethod
    // lookup would have found a rate (see fetchCommissionContext docs).
    if (modelKey === "transactions") {
      try {
        const ctx = await fetchCommissionContext(id);
        setCommissionContext(ctx);
      } catch {
        // Live recalculation is a convenience, not a hard requirement —
        // if the lookup fails, the fields just stay plain manual inputs.
        setCommissionContext({ available: false });
      }
    }
  }

  /**
   * Port of `transaction_changes.js`'s `calculateCommission` — recomputes
   * commission/partner_income/pure_our_income/amount_after_commission
   * live as the operator edits `amount`, using the SAME formula and the
   * SAME rates (`merchant_personal_rate`, `partner_rate`) the source pulled
   * via `data-*` attributes on the amount field. The operator can still
   * manually override any of the four fields afterward — this only sets a
   * starting value, exactly like the source's live preview.
   */
  function handleAmountChange(rawAmount: string) {
    setForm((f) => ({ ...f, amount: rawAmount }));
    if (!commissionContext?.available) return;

    const amount = parseFloat(rawAmount);
    if (Number.isNaN(amount)) return;

    const merchantRate = parseFloat(commissionContext.merchant_personal_rate ?? "0") || 0;
    const partnerRate = parseFloat(commissionContext.partner_rate ?? "0") || 0;
    const direction = commissionContext.direction;

    const commission = (amount * merchantRate) / 100;
    const partnerIncome = (amount * partnerRate) / 100;
    const pureOurIncome = commission - partnerIncome;
    const amountAfterCommission = direction === "IN" ? amount - commission : amount + commission;

    setForm((f) => ({
      ...f,
      commission: commission.toFixed(2),
      partner_income: partnerIncome.toFixed(2),
      pure_our_income: pureOurIncome.toFixed(2),
      amount_after_commission: amountAfterCommission.toFixed(2),
    }));
  }

  function cancelEditing() {
    setEditing(false);
    setSaveError(null);
  }

  async function handleSave() {
    if (!config) return;
    setSaving(true);
    setSaveError(null);
    try {
      const values: Record<string, string | null> = {};
      for (const field of config.editable_fields) {
        values[field] = form[field] === "" ? null : form[field];
      }
      const updated =
        modelKey === "settlements"
          ? (await updateSettlement(id, values)).settlement
          : await UPDATE_FN[modelKey](id, values);
      setRecord(updated);
      setEditing(false);
      setSavedNotice(true);
    } catch (err) {
      if (err instanceof AuthExpiredError) {
        logout();
        router.replace("/login");
        return;
      }
      setSaveError(err instanceof Error ? err.message : "Не удалось сохранить изменения.");
    } finally {
      setSaving(false);
    }
  }

  if (schemaLoading || loading) {
    return <p className="text-sm text-[var(--text-muted)]">Загрузка…</p>;
  }

  if (notFound) {
    return <p className="text-sm text-red-600">Запись не найдена.</p>;
  }

  if (error) {
    return <p className="text-sm text-red-600">{error}</p>;
  }

  if (!record || !config) return null;

  const enhanced = ENHANCED_KEYS.has(modelKey);
  const fields = Object.keys(record);
  const editableSet = new Set(config.editable_fields);

  return (
    <div className="space-y-4">
      <div className="flex items-start justify-between gap-4">
        <div>
          <Link href={`/admin/${modelKey}`} className="text-sm text-accent hover:underline">
            ← {config.verbose_name_plural}
          </Link>
          <h1 className="mt-1 text-lg font-semibold">
            {config.verbose_name} #{id}
          </h1>
        </div>
        {canEdit && !editing && (
          <button
            onClick={startEditing}
            className="shrink-0 rounded-md bg-accent px-3 py-1.5 text-sm font-medium text-white hover:bg-accent-dark"
          >
            Редактировать
          </button>
        )}
      </div>

      {savedNotice && !editing && (
        <div className="rounded-md border border-green-300 bg-green-50 px-3 py-2 text-sm text-green-800 dark:border-green-900 dark:bg-green-950 dark:text-green-300">
          Изменения сохранены.
        </div>
      )}

      {editing && (modelKey === "transactions" || modelKey === "settlements") && (
        <div className="rounded-md border border-amber-300 bg-amber-50 px-3 py-2 text-sm text-amber-900 dark:border-amber-900 dark:bg-amber-950 dark:text-amber-200">
          Изменение статуса/суммы запускает реальный пересчёт балансов мерчанта и партнёра — как в оригинальной
          Django-админке. Действие необратимо без встречной корректирующей записи.
        </div>
      )}

      {editing && (modelKey === "payment-method-companies" || modelKey === "merchant-payment-methods") && (
        <div className="rounded-md border border-amber-300 bg-amber-50 px-3 py-2 text-sm text-amber-900 dark:border-amber-900 dark:bg-amber-950 dark:text-amber-200">
          Сохранение сбрасывает Redis-кэш доступных методов оплаты — как в оригинальной Django-админке.
        </div>
      )}

      {saveError && (
        <div className="rounded-md border border-red-300 bg-red-50 px-3 py-2 text-sm text-red-700 dark:border-red-900 dark:bg-red-950 dark:text-red-300">
          {saveError}
        </div>
      )}

      <div className="card divide-y divide-[var(--border)]">
        {fields.map((field) => {
          const value = record[field];
          const isEditableField = editing && editableSet.has(field);

          if (isEditableField) {
            return (
              <div key={field} className="flex flex-col gap-1 px-4 py-3 sm:flex-row sm:items-center sm:gap-4">
                <div className="w-full shrink-0 text-xs font-medium text-[var(--text-muted)] sm:w-56">
                  {humanizeFieldName(field)}
                </div>
                {field === "status" ? (
                  <select
                    value={form[field] ?? ""}
                    onChange={(e) => setForm((f) => ({ ...f, [field]: e.target.value }))}
                    className="w-full max-w-xs rounded-md border border-[var(--border)] bg-transparent px-3 py-1.5 text-sm outline-none focus:ring-2 focus:ring-accent"
                  >
                    {STATUS_CHOICES.map((s) => (
                      <option key={s} value={s}>
                        {s}
                      </option>
                    ))}
                  </select>
                ) : field === "amount" && modelKey === "transactions" ? (
                  <div className="flex w-full max-w-xs flex-col gap-1">
                    <input
                      value={form[field] ?? ""}
                      onChange={(e) => handleAmountChange(e.target.value)}
                      className="w-full rounded-md border border-[var(--border)] bg-transparent px-3 py-1.5 text-sm outline-none focus:ring-2 focus:ring-accent"
                    />
                    {commissionContext?.available && (
                      <span className="text-[10px] text-[var(--text-muted)]">
                        Комиссия и суммы ниже пересчитываются автоматически (можно поправить вручную)
                      </span>
                    )}
                  </div>
                ) : (
                  <input
                    value={form[field] ?? ""}
                    onChange={(e) => setForm((f) => ({ ...f, [field]: e.target.value }))}
                    className="w-full max-w-xs rounded-md border border-[var(--border)] bg-transparent px-3 py-1.5 text-sm outline-none focus:ring-2 focus:ring-accent"
                  />
                )}
              </div>
            );
          }

          let display: React.ReactNode;
          if (enhanced && field === "status" && value !== null && value !== undefined) {
            display = <StatusBadge value={value} />;
          } else if (field.startsWith("date_") || field.endsWith("_at") || field === "date") {
            display = formatDateMaybe(value);
          } else {
            display = formatCellValue(value);
          }
          return (
            <div key={field} className="flex flex-col gap-1 px-4 py-3 sm:flex-row sm:items-baseline sm:gap-4">
              <div className="w-full shrink-0 text-xs font-medium text-[var(--text-muted)] sm:w-56">
                {humanizeFieldName(field)}
                {editing && editableSet.has(field) === false && canEdit && (
                  <span className="ml-1 text-[10px] text-[var(--text-muted)]">(нередактируемо)</span>
                )}
              </div>
              <div className="break-all text-sm">{display}</div>
            </div>
          );
        })}
      </div>

      {editing && (
        <div className="flex gap-2">
          <button
            onClick={handleSave}
            disabled={saving}
            className="rounded-md bg-accent px-4 py-2 text-sm font-medium text-white hover:bg-accent-dark disabled:opacity-60"
          >
            {saving ? "Сохранение…" : "Сохранить"}
          </button>
          <button
            onClick={cancelEditing}
            disabled={saving}
            className="rounded-md border border-[var(--border)] px-4 py-2 text-sm hover:border-accent disabled:opacity-60"
          >
            Отмена
          </button>
        </div>
      )}
    </div>
  );
}
