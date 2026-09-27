"use client";

import { useEffect, useState } from "react";
import { fetchModelList } from "@/lib/api";
import { useSchema } from "@/lib/schema-context";

/** Fills a `config.str_template` (e.g. "{name} — {default_personal_rate}%")
 * from a row's own fields, falling back to a bare id if a placeholder is
 * missing from the row (shouldn't happen for a well-formed template). */
function renderTemplate(template: string, row: Record<string, unknown>): string {
  return template.replace(/\{(\w+)\}/g, (_, key) => {
    const value = row[key];
    return value === null || value === undefined ? "" : String(value);
  });
}

interface Option {
  id: number | string;
  label: string;
}

/**
 * Dropdown for a foreign-key field declared in `config.fk_fields` (backend:
 * `AdminModelConfig.fk_fields` in `app/registry/admin_models.py`). Loads the
 * target model's rows once and renders each as `str_template` text instead
 * of forcing the operator to type a raw id.
 */
export function FkSelect({
  targetKey,
  value,
  onChange,
  required,
}: {
  targetKey: string;
  value: string;
  onChange: (value: string) => void;
  required?: boolean;
}) {
  const { getConfig } = useSchema();
  const targetConfig = getConfig(targetKey);
  const [options, setOptions] = useState<Option[] | null>(null);
  const [error, setError] = useState(false);

  useEffect(() => {
    let cancelled = false;
    setOptions(null);
    setError(false);
    // Server caps page_size at 200 (see app.api.admin) — good enough for a
    // picker dropdown; a target with more rows than that is an edge case
    // this simple client-side picker doesn't handle (no search/pagination).
    fetchModelList(targetKey, { page_size: 200 })
      .then((res) => {
        if (cancelled) return;
        const template = targetConfig?.str_template ?? "[{id}]";
        setOptions(
          res.items.map((row) => ({
            id: row.id as number | string,
            label: renderTemplate(template, row),
          }))
        );
      })
      .catch(() => {
        if (!cancelled) setError(true);
      });
    return () => {
      cancelled = true;
    };
  }, [targetKey, targetConfig?.str_template]);

  if (error) {
    // Fall back to a plain id input rather than blocking the form entirely.
    return (
      <input
        required={required}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder="ID"
        className="w-full rounded-md border border-[var(--border)] bg-transparent px-3 py-1.5 text-sm outline-none focus:ring-2 focus:ring-accent"
      />
    );
  }

  return (
    <select
      required={required}
      value={value}
      onChange={(e) => onChange(e.target.value)}
      disabled={!options}
      className="w-full rounded-md border border-[var(--border)] bg-transparent px-3 py-1.5 text-sm outline-none focus:ring-2 focus:ring-accent disabled:opacity-60"
    >
      <option value="">{options ? "Не выбрано" : "Загрузка…"}</option>
      {options?.map((opt) => (
        <option key={opt.id} value={String(opt.id)}>
          {opt.label}
        </option>
      ))}
    </select>
  );
}
