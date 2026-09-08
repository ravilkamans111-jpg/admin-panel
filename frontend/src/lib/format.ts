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
