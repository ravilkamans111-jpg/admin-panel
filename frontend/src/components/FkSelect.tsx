"use client";

import { useEffect, useRef, useState } from "react";
import { fetchOptions } from "@/lib/api";
import { useDebouncedValue } from "@/hooks/useDebouncedValue";
import type { OptionItem } from "@/lib/types";

const inputClass =
  "w-full rounded-md border border-[var(--border)] bg-transparent px-3 py-1.5 text-sm outline-none focus:ring-2 focus:ring-accent";

/**
 * Searchable single-value picker for a foreign-key field (backend:
 * `AdminModelConfig.fk_fields`). Types-to-search over the target's rendered
 * labels (`GET /admin/{target}/options`), so it works for any table size
 * and finds records by what the operator sees ("ampayadmin", "We4Pay"),
 * not by id. The value is the record id as a string ("" = nothing chosen).
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
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [options, setOptions] = useState<OptionItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [selectedLabel, setSelectedLabel] = useState<string>("");
  const debouncedQuery = useDebouncedValue(query, 250);
  const rootRef = useRef<HTMLDivElement>(null);

  // Resolve the current value's label (initial edit value, or after a pick).
  useEffect(() => {
    if (!value) {
      setSelectedLabel("");
      return;
    }
    let cancelled = false;
    fetchOptions(targetKey, { ids: [value] })
      .then((found) => {
        if (!cancelled) setSelectedLabel(found[0]?.label ?? `#${value}`);
      })
      .catch(() => {
        if (!cancelled) setSelectedLabel(`#${value}`);
      });
    return () => {
      cancelled = true;
    };
  }, [targetKey, value]);

  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    setLoading(true);
    fetchOptions(targetKey, { search: debouncedQuery, limit: 30 })
      .then((found) => {
        if (!cancelled) setOptions(found);
      })
      .catch(() => {
        if (!cancelled) setOptions([]);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [open, debouncedQuery, targetKey]);

  useEffect(() => {
    function onDocClick(e: MouseEvent) {
      if (rootRef.current && !rootRef.current.contains(e.target as Node)) setOpen(false);
    }
    document.addEventListener("mousedown", onDocClick);
    return () => document.removeEventListener("mousedown", onDocClick);
  }, []);

  return (
    <div ref={rootRef} className="relative">
      <input
        value={open ? query : selectedLabel}
        onChange={(e) => {
          setQuery(e.target.value);
          setOpen(true);
        }}
        onFocus={() => {
          setQuery("");
          setOpen(true);
        }}
        placeholder="Начните вводить для поиска…"
        required={required && !value}
        className={inputClass}
        autoComplete="off"
      />
      {value && !open && (
        <button
          type="button"
          onClick={() => onChange("")}
          aria-label="Очистить"
          className="absolute right-2 top-1/2 -translate-y-1/2 text-xs text-[var(--text-muted)] hover:text-accent"
        >
          ✕
        </button>
      )}
      {open && (
        <ul className="card absolute z-20 mt-1 max-h-64 w-full overflow-y-auto py-1 text-sm shadow-lg">
          {loading && <li className="px-3 py-1.5 text-[var(--text-muted)]">Поиск…</li>}
          {!loading && options.length === 0 && <li className="px-3 py-1.5 text-[var(--text-muted)]">Ничего не найдено</li>}
          {options.map((o) => (
            <li key={o.id}>
              <button
                type="button"
                onClick={() => {
                  onChange(String(o.id));
                  setSelectedLabel(o.label);
                  setOpen(false);
                }}
                className={`block w-full px-3 py-1.5 text-left hover:bg-black/5 dark:hover:bg-white/5 ${
                  String(o.id) === value ? "text-accent" : ""
                }`}
              >
                {o.label}
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
