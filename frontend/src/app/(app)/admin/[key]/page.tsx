"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useSchema } from "@/lib/schema-context";
import {
  AuthExpiredError,
  ApiError,
  downloadCsv,
  fetchFilterDescriptors,
  fetchModelList,
  refreshMerchantBalances,
  sendTransactionCallbacks,
  sendSettlementCallbacks,
  updateRecordGeneric,
} from "@/lib/api";
import { useAuth } from "@/lib/auth-context";
import { useDebouncedValue } from "@/hooks/useDebouncedValue";
import { DataTable, type CellEdit } from "@/components/DataTable";
import { ListFilters } from "@/components/ListFilters";
import { fieldCaption, nameEnrichedFields } from "@/lib/format";
import type { FilterDescriptor } from "@/lib/types";

const ENHANCED_KEYS = new Set(["transactions", "merchant-balances", "settlements"]);

// Models with a dedicated multi-select bulk-actions page at
// `/admin/{key}/bulk-actions` — see `app.services.merchant_bulk_actions_service`
// / `app.services.cache_clear_actions_service`.
const BULK_ACTIONS_KEYS = new Set(["merchants", "payment-method-companies", "merchant-payment-methods"]);

// The only list action implemented so far — "send callbacks" (see
// `app.services.callback_service`); a model offers it by listing it in its
// backend `actions` (transactions, settlements).
const ACTION_SEND_CALLBACKS = "send_callbacks";

export default function AdminModelListPage() {
  const params = useParams<{ key: string }>();
  const modelKey = params.key;
  const { getConfig, loading: schemaLoading, error: schemaError } = useSchema();
  const { logout } = useAuth();
  const router = useRouter();

  const config = getConfig(modelKey);

  const [page, setPage] = useState(1);
  const [searchInput, setSearchInput] = useState("");
  const [search, setSearch] = useState("");
  const [action, setAction] = useState("");
  const [edits, setEdits] = useState<Record<number, Record<string, CellEdit>>>({});
  const [savingEdits, setSavingEdits] = useState(false);
  const [editMessage, setEditMessage] = useState<string | null>(null);
  const [editError, setEditError] = useState<string | null>(null);
  const [filters, setFilters] = useState<Record<string, string>>({});
  const [ordering, setOrdering] = useState<string | null>(null);
  const [items, setItems] = useState<Array<Record<string, unknown>>>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const [refreshMessage, setRefreshMessage] = useState<string | null>(null);
  const [refreshError, setRefreshError] = useState<string | null>(null);
  const [reloadToken, setReloadToken] = useState(0);
  const [descriptors, setDescriptors] = useState<FilterDescriptor[]>([]);
  const [exporting, setExporting] = useState(false);
  const [exportError, setExportError] = useState<string | null>(null);
  const [selectedIds, setSelectedIds] = useState<Set<number>>(new Set());
  const [sendingCallbacks, setSendingCallbacks] = useState(false);
  const [callbackMessage, setCallbackMessage] = useState<string | null>(null);
  const [callbackError, setCallbackError] = useState<string | null>(null);

  const pageSize = config?.list_per_page ?? 20;
  const debouncedSearch = search;
  const debouncedFilters = useDebouncedValue(filters, 350);

  // Reset paging/sorting state whenever the model changes.
  useEffect(() => {
    setPage(1);
    setSearch("");
    setSearchInput("");
    setAction("");
    setEdits({});
    setEditMessage(null);
    setEditError(null);
    setFilters({});
    // A single default sort key shows its arrow; several keys are left to the server's default order.
    setOrdering(config?.default_ordering?.length === 1 ? config.default_ordering[0] : null);
    setSelectedIds(new Set());
    setCallbackMessage(null);
    setCallbackError(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [modelKey]);

  useEffect(() => {
    if (!config) return;
    let cancelled = false;
    setDescriptors([]);
    fetchFilterDescriptors(modelKey)
      .then((d) => !cancelled && setDescriptors(d))
      .catch(() => !cancelled && setDescriptors([]));
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [modelKey, config]);

  useEffect(() => {
    if (!config) return;
    let cancelled = false;
    setLoading(true);
    setError(null);
    fetchModelList(modelKey, {
      page,
      page_size: pageSize,
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

  const totalPages = useMemo(() => Math.max(1, Math.ceil(total / pageSize)), [total, pageSize]);

  const listEditable = useMemo(() => new Set(config?.list_editable ?? []), [config]);
  const hasActions = (config?.actions.length ?? 0) > 0;

  function handleEdit(rowId: number, field: string, value: CellEdit) {
    setEditMessage(null);
    setEditError(null);
    setEdits((prev) => ({ ...prev, [rowId]: { ...prev[rowId], [field]: value } }));
  }

  async function handleSaveEdits() {
    setSavingEdits(true);
    setEditMessage(null);
    setEditError(null);
    const failures: string[] = [];
    let saved = 0;
    for (const [rowId, fields] of Object.entries(edits)) {
      const body: Record<string, string | boolean | null> = {};
      for (const [field, value] of Object.entries(fields)) {
        body[field] = typeof value === "boolean" ? value : value === "" ? null : value;
      }
      try {
        await updateRecordGeneric(modelKey, rowId, body);
        saved += 1;
      } catch (err) {
        if (err instanceof AuthExpiredError) {
          logout();
          router.replace("/login");
          return;
        }
        failures.push(`#${rowId}: ${err instanceof Error ? err.message : "ошибка"}`);
      }
    }
    if (saved) setEditMessage(`Изменено записей: ${saved}.`);
    if (failures.length) setEditError(failures.join("; "));
    setEdits({});
    setReloadToken((t) => t + 1);
    setSavingEdits(false);
  }

  function handleRunAction() {
    if (action === ACTION_SEND_CALLBACKS) void handleSendCallbacks();
  }

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

  async function handleExport() {
    setExporting(true);
    setExportError(null);
    try {
      await downloadCsv(modelKey, {
        search: debouncedSearch || undefined,
        ordering: ordering || undefined,
        ...debouncedFilters,
      });
    } catch (err) {
      if (err instanceof AuthExpiredError) {
        logout();
        router.replace("/login");
        return;
      }
      setExportError(err instanceof Error ? err.message : "Не удалось выгрузить данные.");
    } finally {
      setExporting(false);
    }
  }

  function toggleSelected(id: number) {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  function toggleSelectAll() {
    setSelectedIds((prev) => {
      const allSelected = items.length > 0 && items.every((r) => prev.has(Number(r["id"])));
      if (allSelected) return new Set();
      return new Set(items.map((r) => Number(r["id"])));
    });
  }

  async function handleSendCallbacks() {
    if (selectedIds.size === 0) return;
    setSendingCallbacks(true);
    setCallbackMessage(null);
    setCallbackError(null);
    try {
      const ids = Array.from(selectedIds);
      const { results } =
        modelKey === "settlements" ? await sendSettlementCallbacks(ids) : await sendTransactionCallbacks(ids);
      const counts = Object.values(results).reduce<Record<string, number>>((acc, outcome) => {
        const bucket = outcome.startsWith("error") ? "error" : outcome.startsWith("skipped") ? "skipped" : outcome;
        acc[bucket] = (acc[bucket] ?? 0) + 1;
        return acc;
      }, {});
      setCallbackMessage(
        Object.entries(counts)
          .map(([bucket, count]) => `${bucket}: ${count}`)
          .join(", ")
      );
      setSelectedIds(new Set());
    } catch (err) {
      if (err instanceof AuthExpiredError) {
        logout();
        router.replace("/login");
        return;
      }
      setCallbackError(err instanceof Error ? err.message : "Не удалось отправить коллбэки.");
    } finally {
      setSendingCallbacks(false);
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

  const nameFields = nameEnrichedFields(config.fk_fields, modelKey, config.list_display);
  const selectable = hasActions;
  const banner = (tone: "ok" | "err", text: string | null) =>
    text ? (
      <div
        className={`rounded-md border px-3 py-2 text-sm ${
          tone === "ok"
            ? "border-green-300 bg-green-50 text-green-800 dark:border-green-900 dark:bg-green-950 dark:text-green-300"
            : "border-red-300 bg-red-50 text-red-700 dark:border-red-900 dark:bg-red-950 dark:text-red-300"
        }`}
      >
        {text}
      </div>
    ) : null;
  const pendingCount = Object.keys(edits).length;

  return (
    <div className="space-y-4">
      <div className="flex items-start justify-between gap-4">
        <h1 className="text-lg font-semibold">{config.verbose_name_plural}</h1>
        <div className="flex shrink-0 gap-2">
          <button
            onClick={handleExport}
            disabled={exporting}
            className="rounded-md border border-[var(--border)] px-3 py-1.5 text-sm hover:border-accent disabled:opacity-60"
          >
            {exporting ? "Выгрузка…" : "Экспорт CSV"}
          </button>
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
              Добавить +
            </Link>
          )}
        </div>
      </div>

      {banner("ok", refreshMessage)}
      {banner("err", refreshError)}
      {banner("err", exportError)}
      {banner("ok", callbackMessage ? `Коллбэки обработаны: ${callbackMessage}` : null)}
      {banner("err", callbackError)}
      {banner("ok", editMessage)}
      {banner("err", editError)}
      {banner("err", error)}

      <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_16rem]">
        <div className="min-w-0 space-y-3">
          {config.search_fields.length > 0 && (
            <form
              className="flex items-center gap-2 rounded-md border border-[var(--border)] px-3 py-2"
              onSubmit={(e) => {
                e.preventDefault();
                setPage(1);
                setSearch(searchInput.trim());
              }}
            >
              <span className="text-[var(--text-muted)]">⌕</span>
              <input
                value={searchInput}
                onChange={(e) => setSearchInput(e.target.value)}
                aria-label="Поиск"
                className="w-full max-w-md rounded-md border border-[var(--border)] bg-transparent px-3 py-1.5 text-sm outline-none focus:ring-2 focus:ring-accent"
              />
              <button type="submit" className="rounded-md border border-[var(--border)] px-3 py-1.5 text-sm hover:border-accent">
                Search
              </button>
            </form>
          )}

          {hasActions && (
            <div className="flex flex-wrap items-center gap-2 text-sm">
              <label htmlFor="list-action">Action:</label>
              <select
                id="list-action"
                value={action}
                onChange={(e) => setAction(e.target.value)}
                className="rounded-md border border-[var(--border)] bg-transparent px-3 py-1.5 outline-none focus:ring-2 focus:ring-accent"
              >
                <option value="">---------</option>
                {config.actions.map((a) => (
                  <option key={a.key} value={a.key}>
                    {a.label}
                  </option>
                ))}
              </select>
              <button
                onClick={handleRunAction}
                disabled={!action || selectedIds.size === 0 || sendingCallbacks}
                className="rounded-md border border-[var(--border)] px-3 py-1.5 hover:border-accent disabled:opacity-50"
              >
                {sendingCallbacks ? "Отправка…" : "Go"}
              </button>
              <span className="text-[var(--text-muted)]">
                {selectedIds.size} of {items.length} selected
              </span>
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
              selected={selectable ? selectedIds : undefined}
              onToggleSelected={selectable ? toggleSelected : undefined}
              onToggleSelectAll={selectable ? toggleSelectAll : undefined}
              nameFields={nameFields}
              labels={config.field_labels}
              editable={listEditable}
              edits={edits}
              onEdit={handleEdit}
              kinds={config.field_kinds}
            />
          </div>

          <div className="flex flex-wrap items-center gap-3 text-sm">
            <span className="flex items-center gap-1">
              <button
                onClick={() => setPage((p) => Math.max(1, p - 1))}
                disabled={page <= 1}
                className="rounded-md border border-[var(--border)] px-2.5 py-1 disabled:opacity-40"
              >
                ‹
              </button>
              <span className="px-2">
                {page} / {totalPages}
              </span>
              <button
                onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
                disabled={page >= totalPages}
                className="rounded-md border border-[var(--border)] px-2.5 py-1 disabled:opacity-40"
              >
                ›
              </button>
            </span>
            <span className="text-[var(--text-muted)]">
              {total.toLocaleString("ru-RU")} {config.verbose_name_plural}
            </span>
            {listEditable.size > 0 && (
              <button
                onClick={handleSaveEdits}
                disabled={savingEdits || pendingCount === 0}
                className="rounded-md bg-accent px-4 py-1.5 font-medium text-white hover:bg-accent-dark disabled:opacity-50"
              >
                {savingEdits ? "Сохранение…" : "Save"}
              </button>
            )}
          </div>
        </div>

        {descriptors.length > 0 && (
          <aside className="card h-fit space-y-4 p-3">
            <div className="flex items-center justify-between">
              <h2 className="text-xs font-semibold uppercase tracking-wide">Фильтр</h2>
              {Object.keys(filters).length > 0 && (
                <button
                  onClick={() => {
                    setPage(1);
                    setFilters({});
                  }}
                  className="text-xs text-accent hover:underline"
                >
                  Сбросить
                </button>
              )}
            </div>
            <ListFilters
              descriptors={descriptors}
              filters={filters}
              onChange={handleFilterChange}
              labels={config.field_labels}
            />
          </aside>
        )}
      </div>
    </div>
  );
}
