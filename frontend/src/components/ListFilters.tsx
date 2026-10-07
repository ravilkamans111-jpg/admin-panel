"use client";

import { useEffect, useRef, useState } from "react";
import { fetchOptions } from "@/lib/api";
import { useDebouncedValue } from "@/hooks/useDebouncedValue";
import { fieldHeaderLabel } from "@/lib/format";
import type { FilterDescriptor, OptionItem } from "@/lib/types";

const control =
  "rounded-md border border-[var(--border)] bg-transparent px-3 py-1.5 text-sm outline-none focus:ring-2 focus:ring-accent";

function useOutsideClose(ref: React.RefObject<HTMLElement | null>, close: () => void) {
  useEffect(() => {
    function onDown(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) close();
    }
    document.addEventListener("mousedown", onDown);
    return () => document.removeEventListener("mousedown", onDown);
  }, [ref, close]);
}

const split = (value: string | undefined) => (value ? value.split(",").filter(Boolean) : []);

function Chip({ label, onRemove }: { label: string; onRemove: () => void }) {
  return (
    <span className="inline-flex items-center gap-1 rounded bg-accent/15 px-1.5 py-0.5 text-xs text-accent">
      {label}
      <button type="button" onClick={onRemove} aria-label="Убрать" className="hover:opacity-70">
        ✕
      </button>
    </span>
  );
}

/** Checkbox dropdown over a fixed list (statuses, directions, Да/Нет …). */
function ChoiceFilter({
  options,
  value,
  onChange,
}: {
  options: { value: string; label: string }[];
  value: string;
  onChange: (value: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  useOutsideClose(ref, () => setOpen(false));
  const selected = split(value);
  const toggle = (v: string) =>
    onChange((selected.includes(v) ? selected.filter((s) => s !== v) : [...selected, v]).join(","));

  return (
    <div ref={ref} className="relative">
      <button type="button" onClick={() => setOpen((o) => !o)} className={`${control} min-w-36 text-left`}>
        {selected.length === 0 ? "Все" : selected.map((v) => options.find((o) => o.value === v)?.label ?? v).join(", ")}
      </button>
      {open && (
        <div className="card absolute z-20 mt-1 max-h-64 min-w-full overflow-y-auto py-1 text-sm shadow-lg">
          {options.map((o) => (
            <label key={o.value} className="flex cursor-pointer items-center gap-2 px-3 py-1.5 hover:bg-black/5 dark:hover:bg-white/5">
              <input type="checkbox" checked={selected.includes(o.value)} onChange={() => toggle(o.value)} />
              {o.label}
            </label>
          ))}
        </div>
      )}
    </div>
  );
}

/** Searchable multi-select over another model's records (merchants, partners …). */
function FkFilter({ target, value, onChange }: { target: string; value: string; onChange: (value: string) => void }) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [options, setOptions] = useState<OptionItem[]>([]);
  const [labels, setLabels] = useState<Record<string, string>>({});
  const debounced = useDebouncedValue(query, 250);
  const ref = useRef<HTMLDivElement>(null);
  useOutsideClose(ref, () => setOpen(false));
  const selected = split(value);

  useEffect(() => {
    const missing = selected.filter((id) => !(id in labels));
    if (missing.length === 0) return;
    fetchOptions(target, { ids: missing })
      .then((found) => setLabels((prev) => ({ ...prev, ...Object.fromEntries(found.map((o) => [String(o.id), o.label])) })))
      .catch(() => undefined);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [value, target]);

  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    fetchOptions(target, { search: debounced, limit: 30 })
      .then((found) => {
        if (cancelled) return;
        setOptions(found);
        setLabels((prev) => ({ ...prev, ...Object.fromEntries(found.map((o) => [String(o.id), o.label])) }));
      })
      .catch(() => !cancelled && setOptions([]));
    return () => {
      cancelled = true;
    };
  }, [open, debounced, target]);

  const toggle = (id: string) =>
    onChange((selected.includes(id) ? selected.filter((s) => s !== id) : [...selected, id]).join(","));

  return (
    <div ref={ref} className="relative">
      <div className={`${control} flex min-h-[34px] min-w-44 max-w-xs flex-wrap items-center gap-1`} onClick={() => setOpen(true)}>
        {selected.map((id) => (
          <Chip key={id} label={labels[id] ?? `#${id}`} onRemove={() => toggle(id)} />
        ))}
        <input
          value={query}
          onChange={(e) => {
            setQuery(e.target.value);
            setOpen(true);
          }}
          placeholder={selected.length ? "" : "Все"}
          className="min-w-16 flex-1 bg-transparent outline-none"
        />
      </div>
      {open && (
        <ul className="card absolute z-20 mt-1 max-h-64 w-72 overflow-y-auto py-1 text-sm shadow-lg">
          {options.length === 0 && <li className="px-3 py-1.5 text-[var(--text-muted)]">Ничего не найдено</li>}
          {options.map((o) => (
            <li key={o.id}>
              <label className="flex cursor-pointer items-center gap-2 px-3 py-1.5 hover:bg-black/5 dark:hover:bg-white/5">
                <input type="checkbox" checked={selected.includes(String(o.id))} onChange={() => toggle(String(o.id))} />
                {o.label}
              </label>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

/**
 * Filter bar rendered from the backend's descriptors
 * (`GET /admin/{key}/filter-options`): choice lists, searchable FK
 * multi-selects, date ranges and plain text — the same filter kinds the
 * source Django admin's sidebar offers. State is a flat `{param: value}`
 * map (`status: "A,B"`, `date_create__gte: "2026-08-01"`) sent as query params.
 */
export function ListFilters({
  descriptors,
  filters,
  onChange,
}: {
  descriptors: FilterDescriptor[];
  filters: Record<string, string>;
  onChange: (param: string, value: string) => void;
}) {
  return (
    <>
      {descriptors.map((d) => {
        const label = fieldHeaderLabel(d.field, new Set([d.field]));
        return (
          <div key={d.field}>
            <label className="mb-1 block text-xs font-medium text-[var(--text-muted)]">{label}</label>
            {d.kind === "choice" && (
              <ChoiceFilter options={d.options} value={filters[d.field] ?? ""} onChange={(v) => onChange(d.field, v)} />
            )}
            {d.kind === "fk" && (
              <FkFilter target={d.target} value={filters[d.field] ?? ""} onChange={(v) => onChange(d.field, v)} />
            )}
            {d.kind === "date" && (
              <div className="flex items-center gap-1">
                <input
                  type="date"
                  value={filters[`${d.field}__gte`] ?? ""}
                  onChange={(e) => onChange(`${d.field}__gte`, e.target.value)}
                  className={control}
                />
                <span className="text-[var(--text-muted)]">—</span>
                <input
                  type="date"
                  value={filters[`${d.field}__lte`] ?? ""}
                  onChange={(e) => onChange(`${d.field}__lte`, e.target.value)}
                  className={control}
                />
              </div>
            )}
            {d.kind === "text" && (
              <input value={filters[d.field] ?? ""} onChange={(e) => onChange(d.field, e.target.value)} className={`${control} w-36`} />
            )}
          </div>
        );
      })}
    </>
  );
}
