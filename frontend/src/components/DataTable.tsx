"use client";

import Link from "next/link";
import { fieldHeaderLabel, formatCellValue, formatDateMaybe, looksLikeMoneyField } from "@/lib/format";
import { StatusBadge } from "./StatusBadge";

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
}

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
}: DataTableProps) {
  const sortField = ordering?.startsWith("-") ? ordering.slice(1) : ordering;
  const sortDesc = ordering?.startsWith("-") ?? false;
  const selectable = selected !== undefined && onToggleSelected !== undefined;

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
                  className="cursor-pointer select-none whitespace-nowrap px-3 py-2 font-medium hover:text-accent"
                  onClick={() => onSort(col)}
                >
                  <span className="inline-flex items-center gap-1">
                    {fieldHeaderLabel(col, nameFields ?? new Set())}
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
                    <Cell modelKey={modelKey} field={col} value={row[col]} row={row} rowId={id} enhanced={enhanced} />
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

function Cell({
  modelKey,
  field,
  value,
  row,
  rowId,
  enhanced,
}: {
  modelKey: string;
  field: string;
  value: unknown;
  row: Record<string, unknown>;
  rowId: unknown;
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
  } else if (enhanced && field === "status" && value !== null && value !== undefined) {
    content = <StatusBadge value={value} />;
  } else if (enhanced && looksLikeMoneyField(field) && typeof value === "string" && !Number.isNaN(Number(value))) {
    content = <span className="font-mono tabular-nums">{value}</span>;
  } else if (field.startsWith("date_") || field.endsWith("_at") || field === "date") {
    content = formatDateMaybe(value);
  } else {
    content = formatCellValue(value);
  }

  if (field === "id" && rowId !== undefined) {
    return (
      <Link href={`/admin/${modelKey}/${String(rowId)}`} className="text-accent hover:underline">
        {content}
      </Link>
    );
  }

  return <>{content}</>;
}
