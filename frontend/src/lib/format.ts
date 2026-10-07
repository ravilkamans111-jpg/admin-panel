/** Brand names are shown in capitals everywhere in the UI (AMPAY, RAJAPAY, QUIET-FOREST). */
export function brandName(name: string): string {
  return name.toUpperCase();
}

const ROLE_LABELS_RU: Record<string, string> = {
  viewer: "наблюдатель",
  operator: "оператор",
  brand_admin: "администратор бренда",
  superadmin: "суперадминистратор",
};

export function roleLabel(role: string): string {
  return ROLE_LABELS_RU[role] ?? role;
}

const STATUS_LABELS_RU: Record<string, string> = {
  SUCCESS: "Успешно",
  ACCEPTED: "Принято",
  DECLINED: "Отклонено",
  APPEAL: "Апелляция",
  // Not in the current source StatusChoices enum — real legacy data meaning
  // the transaction never actually went through. Shown as a plain dash
  // rather than a label, per product decision.
  NOT_FOUND: "—",
};

export function statusLabel(status: string): string {
  return STATUS_LABELS_RU[status.toUpperCase()] ?? status;
}

const DIRECTION_LABELS_RU: Record<string, string> = {
  IN: "Входящая",
  OUT: "Исходящая",
};

export function directionLabel(direction: string): string {
  return DIRECTION_LABELS_RU[direction.toUpperCase()] ?? direction;
}

export function humanizeFieldName(field: string): string {
  return field
    .split("_")
    .map((w) => w.charAt(0).toUpperCase() + w.slice(1))
    .join(" ");
}

/**
 * FK columns whose displayed value is a real name/label, not a bare id —
 * `config.fk_fields` (backend: `AdminModelConfig.fk_fields`) plus two cases
 * the backend enriches without a `fk_fields` entry: `currency_id` (any
 * model, via `_enrich_currency_codes`) and transactions'
 * `payment_method_company_id` (via `_enrich_transaction_labels`'s two-hop
 * join — see app.repositories.admin_repository). A column in this set
 * should be headed by the thing's name, not "... Id".
 */
export function nameEnrichedFields(fkFields: Record<string, string>, modelKey: string, columns: string[]): Set<string> {
  const fields = new Set<string>(Object.keys(fkFields));
  if (columns.includes("currency_id")) fields.add("currency_id");
  if (modelKey === "transactions" && columns.includes("payment_method_company_id")) {
    fields.add("payment_method_company_id");
  }
  return fields;
}

/** Column/field header for `field` — drops the trailing "_id" when the
 * displayed value is a real name (see `nameEnrichedFields`), so e.g.
 * "merchant_id" heads as "Merchant" rather than the misleading "Merchant Id". */
export function fieldHeaderLabel(field: string, nameFields: Set<string>): string {
  if (nameFields.has(field) && field.endsWith("_id")) {
    return humanizeFieldName(field.slice(0, -3));
  }
  return humanizeFieldName(field);
}

export function formatCellValue(value: unknown): string {
  if (value === null || value === undefined) return "—";
  if (typeof value === "boolean") return value ? "Да" : "Нет";
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

const MONEY_FIELD_HINTS = [
  "amount",
  "balance",
  "commission",
  "rate",
  "income",
  "funds",
  "limit",
];

export function looksLikeMoneyField(field: string): boolean {
  const lower = field.toLowerCase();
  return MONEY_FIELD_HINTS.some((hint) => lower.includes(hint));
}

export function formatDateMaybe(value: unknown): string {
  if (typeof value !== "string") return formatCellValue(value);
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return value;
  // Only reformat things that look like ISO datetimes.
  if (!/^\d{4}-\d{2}-\d{2}T/.test(value)) return value;
  return d.toLocaleString("ru-RU");
}

/** Column/field caption: the source model's Russian `verbose_name` when the
 * backend has one, otherwise the humanised field name (see `fieldHeaderLabel`). */
export function fieldCaption(
  labels: Record<string, string> | undefined,
  field: string,
  nameFields: Set<string>
): string {
  return labels?.[field] ?? fieldHeaderLabel(field, nameFields);
}
