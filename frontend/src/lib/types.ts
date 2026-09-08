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
  admin_user_id: string;
  email: string;
  full_name: string;
}

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
  notes?: string | null;
  editable_fields: string[];
  creatable_fields: string[];
  creatable: boolean;
  deletable: boolean;
  is_writable: boolean;
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
    total_balance: string;
    total_blocked_in: string;
    total_blocked_out: string;
  }>;
}

export interface ApiErrorBody {
  detail: string;
}
