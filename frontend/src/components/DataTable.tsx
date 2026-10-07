"use client";

import Link from "next/link";
import { fieldCaption, formatCellValue, formatDateMaybe, looksLikeMoneyField } from "@/lib/format";
import { StatusBadge } from "./StatusBadge";

export type CellEdit = string | boolean;

interface DataTableProps {
  modelKey: string;
  columns: string[];
  rows: Array<Record<string, unknown>>;
  ordering: string | null;
  onSort: (field: string) => void;
  /** Enable small model-specific formatting touches (status badges, money alignment). */
  enhanced?: boolean;
  /** Row ids currently checked — renders a checkbox column when set. */
  selected?: Set<number>;
  onToggleSelected?: (id: number) => void;
  onToggleSelectAll?: () => void;
  /** FK columns whose header should read as a name, not "... Id" — see `nameEnrichedFields`. */
  nameFields?: Set<string>;
  /** Russian captions from the backend (`field_labels`). */
  labels?: Record<string, string>;
  /** Columns edited in place (Django's `list_editable`) and the pending edits per row id. */
  editable?: Set<string>;
  edits?: Record<number, Record<string, CellEdit>>;
  onEdit?: (rowId: number, field: string, value: CellEdit) => void;
  /** Input kind per field (bool / int / decimal / ...), from the backend. */
  kinds?: Record<string, string>;
}

const inputClass =
  "w-full min-w-[4.5rem] rounded-md border border-[var(--border)] bg-transparent px-2 py-1 text-sm outline-none focus:ring-2 focus:ring-accent";

export function DataTable({
  modelKey,
  columns,
  rows,
  ordering,
  onSort,
  enhanced,
  selected,
  onToggleSelected,
  onToggleSelectAll,
  nameFields,
  labels,
  editable,
  edits,
  onEdit,
  kinds,
}: DataTableProps) {
  const sortField = ordering?.startsWith("-") ? ordering.slice(1) : ordering;
  const sortDesc = ordering?.startsWith("-") ?? false;
  const selectable = selected !== undefined && onToggleSelected !== undefined;
  // The record link sits on `id`, or on the first column when the model doesn't show one (e.g. antifraud blocks).
  const linkField = columns.includes("id") ? "id" : columns[0];

  return (
    <div className="card overflow-x-auto">
      <table className="data-table w-full text-sm">
        <thead>
          <tr className="text-left">
            {selectable && (
              <th className="w-8 px-3 py-2">
                <input
                  type="checkbox"
                  checked={rows.length > 0 && rows.every((r) => selected!.has(Number(r["id"])))}
                  onChange={() => onToggleSelectAll?.()}
                />
              </th>
            )}
            {columns.map((col) => {
              const active = sortField === col;
              return (
                <th
                  key={col}
                  className="cursor-pointer select-none whitespace-nowrap px-3 py-2 text-xs font-semibold uppercase tracking-wide hover:text-accent"
                  onClick={() => onSort(col)}
                >
                  <span className="inline-flex items-center gap-1">
                    {fieldCaption(labels, col, nameFields ?? new Set())}
                    {active && <span>{sortDesc ? "↓" : "↑"}</span>}
                  </span>
                </th>
              );
            })}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, idx) => {
            const id = row["id"];
            const rowId = id !== undefined ? Number(id) : undefined;
            return (
              <tr key={id !== undefined ? String(id) : idx} className="hover:bg-black/[0.02] dark:hover:bg-white/[0.03]">
                {selectable && (
                  <td className="px-3 py-2">
                    <input
                      type="checkbox"
                      checked={id !== undefined && selected!.has(Number(id))}
                      onChange={() => id !== undefined && onToggleSelected?.(Number(id))}
                    />
                  </td>
                )}
                {columns.map((col) => (
                  <td key={col} className="whitespace-nowrap px-3 py-2">
                    {editable?.has(col) && rowId !== undefined && onEdit ? (
                      <EditableCell
                        kind={kinds?.[col] ?? "text"}
                        original={row[col]}
                        pending={edits?.[rowId]?.[col]}
                        onChange={(value) => onEdit(rowId, col, value)}
                        pickerLabel={row[`${col}_label`]}
                      />
                    ) : (
                      <Cell modelKey={modelKey} field={col} value={row[col]} row={row} rowId={id} linkField={linkField} enhanced={enhanced} />
                    )}
                  </td>
                ))}
              </tr>
            );
          })}
          {rows.length === 0 && (
            <tr>
              <td colSpan={columns.length + (selectable ? 1 : 0)} className="px-3 py-6 text-center text-[var(--text-muted)]">
                Записи не найдены.
              </td>
            </tr>
          )}
        </tbody>
      </table>
    </div>
  );
}

/** One in-place editable cell: checkbox for booleans, number input for numbers. */
function EditableCell({
  kind,
  original,
  pending,
  onChange,
  pickerLabel,
}: {
  kind: string;
  original: unknown;
  pending: CellEdit | undefined;
  onChange: (value: CellEdit) => void;
  pickerLabel?: unknown;
}) {
  if (kind === "bool") {
    return (
      <input
        type="checkbox"
        checked={pending !== undefined ? Boolean(pending) : Boolean(original)}
        onChange={(e) => onChange(e.target.checked)}
      />
    );
  }
  if (kind === "text" && pickerLabel != null) {
    // FK columns edited in a list (e.g. a merchant method's cascade) need a picker — shown read-only here,
    // changed on the record's own page.
    return <>{String(pickerLabel)}</>;
  }
  const shown = pending !== undefined ? String(pending) : original === null || original === undefined ? "" : String(original);
  return (
    <input
      type={kind === "int" || kind === "decimal" ? "number" : "text"}
      step={kind === "decimal" ? "0.01" : undefined}
      value={shown}
      onChange={(e) => onChange(e.target.value)}
      className={inputClass}
    />
  );
}

function Cell({
  modelKey,
  field,
  value,
  row,
  rowId,
  linkField,
  enhanced,
}: {
  modelKey: string;
  field: string;
  value: unknown;
  row: Record<string, unknown>;
  rowId: unknown;
  linkField: string;
  enhanced?: boolean;
}) {
  let content: React.ReactNode;

  if (field === "currency_id" && row["currency_code"] != null) {
    // Server enriches any row with a `currency_id` column with a sibling
    // `currency_code` (see app.repositories.admin_repository) — show the
    // real ISO code instead of the bare numeric id.
    content = String(row["currency_code"]);
  } else if (row[`${field}_label`] != null) {
    // Server enriches any FK column declared in `config.fk_fields` (plus a
    // couple of hand-written two-hop cases, e.g. transactions' "partner")
    // with a sibling `<field>_label` — see app.repositories.admin_repository.
    content = String(row[`${field}_label`]);
  } else if (typeof value === "boolean") {
    // Django admin's yes/no icons.
    content = value ? (
      <span className="text-green-500" title="Да">✓</span>
    ) : (
      <span className="text-red-500" title="Нет">⊗</span>
    );
  } else if (enhanced && field === "status" && value !== null && value !== undefined) {
    content = <StatusBadge value={value} />;
  } else if (enhanced && looksLikeMoneyField(field) && typeof value === "string" && !Number.isNaN(Number(value))) {
    content = <span className="font-mono tabular-nums">{value}</span>;
  } else if (field.startsWith("date_") || field.endsWith("_at") || field === "date" || field.endsWith("_date")) {
    content = formatDateMaybe(value);
  } else {
    content = formatCellValue(value);
  }

  if (field === linkField && rowId !== undefined) {
    return (
      <Link href={`/admin/${modelKey}/${String(rowId)}`} className="text-accent hover:underline">
        {content}
      </Link>
    );
  }

  return <>{content}</>;
}
