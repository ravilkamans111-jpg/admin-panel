"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useSchema } from "@/lib/schema-context";
import { AuthExpiredError, ApiError, fetchModelList, refreshMerchantBalances } from "@/lib/api";
import { useAuth } from "@/lib/auth-context";
import { useDebouncedValue } from "@/hooks/useDebouncedValue";
import { DataTable } from "@/components/DataTable";
import { humanizeFieldName } from "@/lib/format";

const PAGE_SIZE = 25;
const ENHANCED_KEYS = new Set(["transactions", "merchant-balances", "settlements"]);

// Models with a dedicated multi-select bulk-actions page at
// `/admin/{key}/bulk-actions` — see `app.services.merchant_bulk_actions_service`
// / `app.services.cache_clear_actions_service`.
const BULK_ACTIONS_KEYS = new Set(["merchants", "payment-method-companies", "merchant-payment-methods"]);

export default function AdminModelListPage() {
  const params = useParams<{ key: string }>();
  const modelKey = params.key;
  const { getConfig, loading: schemaLoading, error: schemaError } = useSchema();
  const { logout } = useAuth();
  const router = useRouter();

  const config = getConfig(modelKey);

  const [page, setPage] = useState(1);
  const [search, setSearch] = useState("");
  const [filters, setFilters] = useState<Record<string, string>>({});
  const [ordering, setOrdering] = useState<string | null>(null);
  const [items, setItems] = useState<Array<Record<string, unknown>>>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [noteDismissed, setNoteDismissed] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [refreshMessage, setRefreshMessage] = useState<string | null>(null);
  const [refreshError, setRefreshError] = useState<string | null>(null);
  const [reloadToken, setReloadToken] = useState(0);

  const debouncedSearch = useDebouncedValue(search, 350);
  const debouncedFilters = useDebouncedValue(filters, 350);

  // Reset paging/sorting state whenever the model changes.
  useEffect(() => {
    setPage(1);
    setSearch("");
    setFilters({});
    setOrdering(config?.default_ordering?.[0] ?? null);
    setNoteDismissed(false);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [modelKey]);

  useEffect(() => {
    if (!config) return;
    let cancelled = false;
    setLoading(true);
    setError(null);
    fetchModelList(modelKey, {
      page,
      page_size: PAGE_SIZE,
      search: debouncedSearch || undefined,
      ordering: ordering || undefined,
      ...debouncedFilters,
    })
      .then((data) => {
        if (cancelled) return;
        setItems(data.items);
        setTotal(data.total);
      })
      .catch((err) => {
        if (cancelled) return;
        if (err instanceof AuthExpiredError) {
          logout();
          router.replace("/login");
          return;
        }
        if (err instanceof ApiError) {
          setError(err.message);
        } else {
          setError("Не удалось загрузить записи.");
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [modelKey, config, page, debouncedSearch, debouncedFilters, ordering, reloadToken]);

  const totalPages = useMemo(() => Math.max(1, Math.ceil(total / PAGE_SIZE)), [total]);

  async function handleRefreshBalances() {
    setRefreshing(true);
    setRefreshMessage(null);
    setRefreshError(null);
    try {
      const result = await refreshMerchantBalances();
      setRefreshMessage(`Балансы обновлены: ${result.updated_count} запис(ей).`);
      setReloadToken((t) => t + 1);
    } catch (err) {
      if (err instanceof AuthExpiredError) {
        logout();
        router.replace("/login");
        return;
      }
      setRefreshError(err instanceof Error ? err.message : "Не удалось обновить балансы.");
    } finally {
      setRefreshing(false);
    }
  }

  function handleSort(field: string) {
    setPage(1);
    setOrdering((prev) => {
      if (prev === field) return `-${field}`;
      if (prev === `-${field}`) return null;
      return field;
    });
  }

  function handleFilterChange(field: string, value: string) {
    setPage(1);
    setFilters((prev) => {
      const next = { ...prev };
      if (value) next[field] = value;
      else delete next[field];
      return next;
    });
  }

  if (schemaLoading) {
    return <p className="text-sm text-[var(--text-muted)]">Загрузка…</p>;
  }

  if (schemaError) {
    return <p className="text-sm text-red-600">{schemaError}</p>;
  }

  if (!config) {
    return (
      <div>
        <p className="text-sm text-red-600">Неизвестная модель «{modelKey}».</p>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h1 className="text-lg font-semibold">{config.verbose_name_plural}</h1>
          <p className="text-xs text-[var(--text-muted)]">{total.toLocaleString("ru-RU")} записей всего</p>
        </div>
        <div className="flex shrink-0 gap-2">
          {BULK_ACTIONS_KEYS.has(modelKey) && (
            <Link
              href={`/admin/${modelKey}/bulk-actions`}
              className="rounded-md border border-[var(--border)] px-3 py-1.5 text-sm hover:border-accent"
            >
              Массовые действия
            </Link>
          )}
          {modelKey === "merchant-balances" && (
            <button
              onClick={handleRefreshBalances}
              disabled={refreshing}
              className="rounded-md border border-[var(--border)] px-3 py-1.5 text-sm hover:border-accent disabled:opacity-60"
            >
              {refreshing ? "Обновление…" : "Обновить балансы"}
            </button>
          )}
          {config.creatable && (
            <Link
              href={`/admin/${modelKey}/new`}
              className="rounded-md bg-accent px-3 py-1.5 text-sm font-medium text-white hover:bg-accent-dark"
            >
              Создать
            </Link>
          )}
        </div>
      </div>

      {refreshMessage && (
        <div className="rounded-md border border-green-300 bg-green-50 px-3 py-2 text-sm text-green-800 dark:border-green-900 dark:bg-green-950 dark:text-green-300">
          {refreshMessage}
        </div>
      )}
      {refreshError && (
        <div className="rounded-md border border-red-300 bg-red-50 px-3 py-2 text-sm text-red-700 dark:border-red-900 dark:bg-red-950 dark:text-red-300">
          {refreshError}
        </div>
      )}

      {config.notes && !noteDismissed && (
        <div className="flex items-start justify-between gap-3 rounded-md border border-amber-300 bg-amber-50 px-3 py-2 text-sm text-amber-900 dark:border-amber-900 dark:bg-amber-950 dark:text-amber-200">
          <span>{config.notes}</span>
          <button
            onClick={() => setNoteDismissed(true)}
            className="shrink-0 text-xs font-medium text-amber-700 hover:underline dark:text-amber-300"
          >
            Скрыть
          </button>
        </div>
      )}

      <div className="flex flex-wrap items-end gap-3">
        {config.search_fields.length > 0 && (
          <div>
            <label className="mb-1 block text-xs font-medium text-[var(--text-muted)]">Поиск</label>
            <input
              value={search}
              onChange={(e) => {
                setPage(1);
                setSearch(e.target.value);
              }}
              placeholder={config.search_fields.map(humanizeFieldName).join(", ")}
              className="w-64 rounded-md border border-[var(--border)] bg-transparent px-3 py-1.5 text-sm outline-none focus:ring-2 focus:ring-accent"
            />
          </div>
        )}
        {config.list_filter.map((field) => (
          <div key={field}>
            <label className="mb-1 block text-xs font-medium text-[var(--text-muted)]">
              {humanizeFieldName(field)}
            </label>
            <input
              value={filters[field] ?? ""}
              onChange={(e) => handleFilterChange(field, e.target.value)}
              className="w-36 rounded-md border border-[var(--border)] bg-transparent px-3 py-1.5 text-sm outline-none focus:ring-2 focus:ring-accent"
            />
          </div>
        ))}
      </div>

      {error && (
        <div className="rounded-md border border-red-300 bg-red-50 px-3 py-2 text-sm text-red-700 dark:border-red-900 dark:bg-red-950 dark:text-red-300">
          {error}
        </div>
      )}

      <div className="relative">
        {loading && (
          <div className="absolute inset-0 z-10 flex items-center justify-center bg-[var(--bg)]/60 text-sm text-[var(--text-muted)]">
            Загрузка…
          </div>
        )}
        <DataTable
          modelKey={modelKey}
          columns={config.list_display}
          rows={items}
          ordering={ordering}
          onSort={handleSort}
          enhanced={ENHANCED_KEYS.has(modelKey)}
        />
      </div>

      <div className="flex items-center justify-between text-sm">
        <span className="text-[var(--text-muted)]">
          Страница {page} из {totalPages}
        </span>
        <div className="flex gap-2">
          <button
            onClick={() => setPage((p) => Math.max(1, p - 1))}
            disabled={page <= 1}
            className="rounded-md border border-[var(--border)] px-3 py-1.5 disabled:opacity-40"
          >
            Назад
          </button>
          <button
            onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
            disabled={page >= totalPages}
            className="rounded-md border border-[var(--border)] px-3 py-1.5 disabled:opacity-40"
          >
            Вперёд
          </button>
        </div>
      </div>
    </div>
  );
}
