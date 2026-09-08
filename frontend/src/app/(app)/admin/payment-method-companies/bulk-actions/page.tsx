"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import {
  AuthExpiredError,
  fetchModelList,
  clearCacheByCurrency,
  clearAllPaymentMethodsCache,
} from "@/lib/api";
import { useAuth } from "@/lib/auth-context";

// Port of the "🗑️ Сбросить кеш по валютам" / "🗑️ ПОЛНЫЙ сброс кеша" admin
// actions on `PaymentMethodCompanyAdmin` (`app.services.cache_clear_actions_service`).
// Own page (not the generic list/detail views) — needs multi-select, which
// the generic DataTable doesn't support.

type Row = Record<string, unknown>;

export default function PaymentMethodCompanyBulkActionsPage() {
  const { logout } = useAuth();
  const router = useRouter();

  const [rows, setRows] = useState<Row[]>([]);
  const [search, setSearch] = useState("");
  const [selectedIds, setSelectedIds] = useState<Set<number>>(new Set());
  const [loading, setLoading] = useState(true);

  const [result, setResult] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  function handleAuthErr(err: unknown): boolean {
    if (err instanceof AuthExpiredError) {
      logout();
      router.replace("/login");
      return true;
    }
    return false;
  }

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    fetchModelList("payment-method-companies", { page: 1, page_size: 100, search: search || undefined })
      .then((data) => {
        if (!cancelled) setRows(data.items);
      })
      .catch((err) => {
        if (!cancelled) handleAuthErr(err);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [search]);

  function toggle(id: number) {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  async function handleClearByCurrency() {
    setSaving(true);
    setError(null);
    setResult(null);
    try {
      const { currencies } = await clearCacheByCurrency(Array.from(selectedIds));
      setResult(
        `⚠️ Сброшен кеш для ВСЕХ методов валют: ${currencies.join(", ")}! Это влияет не только на выбранные ${selectedIds.size} записей.`
      );
    } catch (err) {
      if (handleAuthErr(err)) return;
      setError(err instanceof Error ? err.message : "Не удалось сбросить кеш.");
    } finally {
      setSaving(false);
    }
  }

  async function handleClearAll() {
    setSaving(true);
    setError(null);
    setResult(null);
    try {
      await clearAllPaymentMethodsCache();
      setResult("ПОЛНЫЙ сброс кеша методов выполнен! Все кешированные методы для всех мерчантов удалены.");
    } catch (err) {
      if (handleAuthErr(err)) return;
      setError(err instanceof Error ? err.message : "Не удалось сбросить кеш.");
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="max-w-2xl space-y-4">
      <div>
        <Link href="/admin/payment-method-companies" className="text-sm text-accent hover:underline">
          ← Конфиги методов у партнёров
        </Link>
        <h1 className="mt-1 text-lg font-semibold">Сброс кеша методов оплаты</h1>
      </div>

      {result && (
        <div className="rounded-md border border-amber-300 bg-amber-50 px-3 py-2 text-sm text-amber-900 dark:border-amber-900 dark:bg-amber-950 dark:text-amber-200">
          {result}
        </div>
      )}
      {error && (
        <div className="rounded-md border border-red-300 bg-red-50 px-3 py-2 text-sm text-red-700 dark:border-red-900 dark:bg-red-950 dark:text-red-300">
          {error}
        </div>
      )}

      <div className="card space-y-3 p-4">
        <div className="flex items-center justify-between">
          <h2 className="text-sm font-semibold">Сбросить кеш по валютам выбранных записей</h2>
          <span className="text-xs text-[var(--text-muted)]">Выбрано: {selectedIds.size}</span>
        </div>
        <p className="text-xs text-[var(--text-muted)]">
          Сбрасывает кеш для ВСЕХ методов валют выбранных записей — не только для них самих.
        </p>
        <input
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder="Поиск…"
          className="w-full max-w-sm rounded-md border border-[var(--border)] bg-transparent px-3 py-1.5 text-sm outline-none focus:ring-2 focus:ring-accent"
        />
        <div className="max-h-64 divide-y divide-[var(--border)] overflow-y-auto rounded-md border border-[var(--border)]">
          {loading && <p className="px-3 py-2 text-sm text-[var(--text-muted)]">Загрузка…</p>}
          {!loading && rows.length === 0 && <p className="px-3 py-2 text-sm text-[var(--text-muted)]">Ничего не найдено.</p>}
          {rows.map((r) => {
            const id = Number(r.id);
            return (
              <label key={id} className="flex cursor-pointer items-center gap-2 px-3 py-1.5 text-sm hover:bg-black/5 dark:hover:bg-white/5">
                <input type="checkbox" checked={selectedIds.has(id)} onChange={() => toggle(id)} />
                <span className="text-[var(--text-muted)]">#{id}</span>
                <span>company_id={String(r.company_id ?? "")}, payment_method_id={String(r.payment_method_id ?? "")}</span>
              </label>
            );
          })}
        </div>
        <button
          onClick={handleClearByCurrency}
          disabled={saving || selectedIds.size === 0}
          className="rounded-md bg-accent px-4 py-2 text-sm font-medium text-white hover:bg-accent-dark disabled:opacity-60"
        >
          {saving ? "Сброс…" : `Сбросить кеш по валютам (${selectedIds.size})`}
        </button>
      </div>

      <div className="card space-y-2 p-4">
        <h2 className="text-sm font-semibold">Полный сброс кеша</h2>
        <p className="text-xs text-[var(--text-muted)]">
          Удаляет кешированные методы для ВСЕХ мерчантов, независимо от выбора выше.
        </p>
        <button
          onClick={handleClearAll}
          disabled={saving}
          className="rounded-md border border-red-300 px-4 py-2 text-sm text-red-700 hover:bg-red-50 disabled:opacity-60 dark:border-red-900 dark:text-red-300 dark:hover:bg-red-950"
        >
          {saving ? "Сброс…" : "ПОЛНЫЙ сброс кеша"}
        </button>
      </div>
    </div>
  );
}
