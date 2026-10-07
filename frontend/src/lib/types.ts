export interface AvailableBrand {
  brand_id: string;
  display_name: string;
  role: string;
}

export interface LoginResponse {
  pre_auth_token: string;
  available_brands: AvailableBrand[];
}

export interface SelectBrandResponse {
  access_token: string;
  refresh_token: string;
  brand_id: string;
  role: string;
}

export interface MeResponse {
  username: string;
  brands: string[];
}

// DISABLED — staff management (see backend/disabled/control_plane/README.md):
// export interface StaffUser {
//   id: number;
//   email: string;
//   full_name: string;
//   is_active: boolean;
//   is_superuser: boolean;
//   locked: boolean;
//   last_login_at: string | null;
//   brand_access: Record<string, string>;
// }

export interface AdminModelConfig {
  key: string;
  app: string;
  app_label: string;
  verbose_name: string;
  verbose_name_plural: string;
  list_display: string[];
  list_filter: string[];
  search_fields: string[];
  default_ordering: string[];
  editable_fields: string[];
  creatable_fields: string[];
  creatable: boolean;
  deletable: boolean;
  is_writable: boolean;
  str_template?: string | null;
  fk_fields: Record<string, string>;
  field_labels: Record<string, string>;
  list_editable: string[];
  actions: { key: string; label: string }[];
  hidden: boolean;
  list_per_page: number;
  /** Input kind per editable/creatable field: bool | int | decimal | datetime | json | text */
  field_kinds: Record<string, string>;
}

export interface ListResponse<T = Record<string, unknown>> {
  items: T[];
  total: number;
  page: number;
  page_size: number;
}

export interface DashboardSummary {
  brand_id: string;
  total_merchants: number;
  total_transactions: number;
  transactions_by_status: Record<string, number>;
  balances_by_currency: Array<{
    currency_id: number;
    currency_code: string;
    total_balance: string;
    total_blocked_in: string;
    total_blocked_out: string;
  }>;
}

export interface ApiErrorBody {
  detail: string;
}

export interface OptionItem {
  id: number;
  label: string;
}

export type FilterDescriptor =
  | { field: string; kind: "text" }
  | { field: string; kind: "date" }
  | { field: string; kind: "choice"; options: { value: string; label: string }[] }
  | { field: string; kind: "fk"; target: string };
