"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { AuthExpiredError, fetchDashboardSummary } from "@/lib/api";
import { useAuth } from "@/lib/auth-context";
import { statusLabel } from "@/lib/format";
import type { DashboardSummary } from "@/lib/types";

const STATUS_COLORS: Record<string, string> = {
  SUCCESS: "#16a34a",
  DECLINED: "#dc2626",
  ACCEPTED: "#2563eb",
  APPEAL: "#d97706",
};

function colorFor(status: string, index: number): string {
  if (STATUS_COLORS[status]) return STATUS_COLORS[status];
  const palette = ["#6366f1", "#ec4899", "#14b8a6", "#f59e0b", "#8b5cf6"];
  return palette[index % palette.length];
}

export default function DashboardPage() {
  const { brandId, logout } = useAuth();
  const router = useRouter();
  const [summary, setSummary] = useState<DashboardSummary | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    fetchDashboardSummary()
      .then((data) => {
        if (!cancelled) setSummary(data);
      })
      .catch((err) => {
        if (cancelled) return;
        if (err instanceof AuthExpiredError) {
          logout();
          router.replace("/login");
          return;
        }
        setError(err instanceof Error ? err.message : "Не удалось загрузить дашборд");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [brandId]);

  if (loading) {
    return <p className="text-sm text-[var(--text-muted)]">Загрузка дашборда…</p>;
  }

  if (error) {
    return (
      <div className="rounded-md border border-red-300 bg-red-50 px-3 py-2 text-sm text-red-700 dark:border-red-900 dark:bg-red-950 dark:text-red-300">
        {error}
      </div>
    );
  }

  if (!summary) return null;

  const statusEntries = Object.entries(summary.transactions_by_status);
  const statusTotal = statusEntries.reduce((sum, [, v]) => sum + v, 0) || 1;

  return (
    <div className="space-y-6">
      <h1 className="text-lg font-semibold">Дашборд</h1>

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
        <StatCard label="Всего мерчантов" value={summary.total_merchants.toLocaleString("ru-RU")} />
        <StatCard label="Всего транзакций" value={summary.total_transactions.toLocaleString("ru-RU")} />
        <StatCard label="Бренд" value={summary.brand_id} />
      </div>

      <div className="card p-5">
        <h2 className="mb-4 text-sm font-semibold">Транзакции по статусу</h2>
        {statusEntries.length === 0 ? (
          <p className="text-sm text-[var(--text-muted)]">Нет данных по транзакциям.</p>
        ) : (
          <div className="space-y-2">
            {statusEntries.map(([status, count], i) => {
              const pct = (count / statusTotal) * 100;
              return (
                <div key={status}>
                  <div className="mb-1 flex items-center justify-between text-xs">
                    <span className="font-medium">{statusLabel(status)}</span>
                    <span className="text-[var(--text-muted)]">
                      {count.toLocaleString("ru-RU")} ({pct.toFixed(1)}%)
                    </span>
                  </div>
                  <div className="h-2 w-full overflow-hidden rounded-full bg-black/5 dark:bg-white/10">
                    <div
                      className="h-2 rounded-full"
                      style={{ width: `${pct}%`, backgroundColor: colorFor(status, i) }}
                    />
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </div>

      <div className="card p-5">
        <h2 className="mb-4 text-sm font-semibold">Балансы по валютам</h2>
        {summary.balances_by_currency.length === 0 ? (
          <p className="text-sm text-[var(--text-muted)]">Нет данных по балансам.</p>
        ) : (
          <div className="overflow-x-auto">
            <table className="data-table w-full text-sm">
              <thead>
                <tr className="text-left">
                  <th className="pb-2 pr-4 font-medium">ID валюты</th>
                  <th className="pb-2 pr-4 font-medium">Общий баланс</th>
                  <th className="pb-2 pr-4 font-medium">Заблокировано (вход)</th>
                  <th className="pb-2 pr-4 font-medium">Заблокировано (выход)</th>
                </tr>
              </thead>
              <tbody>
                {summary.balances_by_currency.map((row) => (
                  <tr key={row.currency_id}>
                    <td className="py-1.5 pr-4">{row.currency_id}</td>
                    <td className="py-1.5 pr-4 font-mono">{row.total_balance}</td>
                    <td className="py-1.5 pr-4 font-mono">{row.total_blocked_in}</td>
                    <td className="py-1.5 pr-4 font-mono">{row.total_blocked_out}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}

function StatCard({ label, value }: { label: string; value: string }) {
  return (
    <div className="card p-5">
      <div className="text-xs text-[var(--text-muted)]">{label}</div>
      <div className="mt-1 text-2xl font-semibold">{value}</div>
    </div>
  );
}
