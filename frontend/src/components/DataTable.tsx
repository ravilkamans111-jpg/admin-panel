"use client";

import Link from "next/link";
import { formatCellValue, formatDateMaybe, humanizeFieldName, looksLikeMoneyField } from "@/lib/format";
import { StatusBadge } from "./StatusBadge";

interface DataTableProps {
  modelKey: string;
  columns: string[];
  rows: Array<Record<string, unknown>>;
  ordering: string | null;
  onSort: (field: string) => void;
  /** Enable small model-specific formatting touches (status badges, money alignment). */
  enhanced?: boolean;
}

export function DataTable({ modelKey, columns, rows, ordering, onSort, enhanced }: DataTableProps) {
  const sortField = ordering?.startsWith("-") ? ordering.slice(1) : ordering;
  const sortDesc = ordering?.startsWith("-") ?? false;

  return (
    <div className="card overflow-x-auto">
      <table className="data-table w-full text-sm">
        <thead>
          <tr className="text-left">
            {columns.map((col) => {
              const active = sortField === col;
              return (
                <th
                  key={col}
                  className="cursor-pointer select-none whitespace-nowrap px-3 py-2 font-medium hover:text-accent"
                  onClick={() => onSort(col)}
                >
                  <span className="inline-flex items-center gap-1">
                    {humanizeFieldName(col)}
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
              <td colSpan={columns.length} className="px-3 py-6 text-center text-[var(--text-muted)]">
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
