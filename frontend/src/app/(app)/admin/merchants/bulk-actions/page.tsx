"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import {
  AuthExpiredError,
  fetchModelList,
  applyTemplateToMerchants,
  applySelectedMethodsToMerchants,
  type BulkApplyResult,
} from "@/lib/api";
import { useAuth } from "@/lib/auth-context";

// Own page (not the generic list/detail views) — port of `MerchantAdmin`'s
// two bulk admin actions (`app.services.merchant_bulk_actions_service`),
// each with its own intermediate confirmation screen in source (select
// merchants -> pick an action -> configure -> confirm) before anything is
// written. Selecting merchants and an action here is that same two-step
// flow, just on one page instead of a server-rendered redirect.

const DIRECTION_CHOICES = ["IN", "OUT"];

type Row = Record<string, unknown>;

export default function MerchantBulkActionsPage() {
  const { logout } = useAuth();
  const router = useRouter();

  const [merchants, setMerchants] = useState<Row[]>([]);
  const [merchantSearch, setMerchantSearch] = useState("");
  const [selectedMerchantIds, setSelectedMerchantIds] = useState<Set<number>>(new Set());
  const [loadingMerchants, setLoadingMerchants] = useState(true);

  const [templates, setTemplates] = useState<Row[]>([]);
  const [currencies, setCurrencies] = useState<Row[]>([]);
  const [methods, setMethods] = useState<Row[]>([]);

  const [activeTab, setActiveTab] = useState<"template" | "methods">("template");

  // Template tab state
  const [templateId, setTemplateId] = useState("");
  const [templateResult, setTemplateResult] = useState<BulkApplyResult | null>(null);
  const [templateError, setTemplateError] = useState<string | null>(null);
  const [templateSaving, setTemplateSaving] = useState(false);

  // Methods tab state
  const [currencyId, setCurrencyId] = useState("");
  const [direction, setDirection] = useState("IN");
  const [selectedMethodIds, setSelectedMethodIds] = useState<Set<number>>(new Set());
  const [personalRate, setPersonalRate] = useState("");
  const [minLimit, setMinLimit] = useState("");
  const [maxLimit, setMaxLimit] = useState("");
  const [testMode, setTestMode] = useState(true);
  const [onlyAdminConfigure, setOnlyAdminConfigure] = useState(false);
  const [methodsResult, setMethodsResult] = useState<BulkApplyResult | null>(null);
  const [methodsError, setMethodsError] = useState<string | null>(null);
  const [methodsSaving, setMethodsSaving] = useState(false);

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
    setLoadingMerchants(true);
    fetchModelList("merchants", { page: 1, page_size: 100, search: merchantSearch || undefined })
      .then((data) => {
        if (!cancelled) setMerchants(data.items);
      })
      .catch((err) => {
        if (!cancelled) handleAuthErr(err);
      })
      .finally(() => {
        if (!cancelled) setLoadingMerchants(false);
      });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [merchantSearch]);

  useEffect(() => {
    fetchModelList("payment-method-templates", { page: 1, page_size: 100 })
      .then((data) => setTemplates(data.items))
      .catch(() => {});
    fetchModelList("currencies", { page: 1, page_size: 100 })
      .then((data) => setCurrencies(data.items))
      .catch(() => {});
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (!currencyId) {
      setMethods([]);
      return;
    }
    fetchModelList("payment-methods", { page: 1, page_size: 200, currency_id: currencyId, direction })
      .then((data) => setMethods(data.items))
      .catch(() => {});
    setSelectedMethodIds(new Set());
  }, [currencyId, direction]);

  function toggleMerchant(id: number) {
    setSelectedMerchantIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  function toggleMethod(id: number) {
    setSelectedMethodIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  async function handleApplyTemplate(e: React.FormEvent) {
    e.preventDefault();
    setTemplateSaving(true);
    setTemplateError(null);
    setTemplateResult(null);
    try {
      const result = await applyTemplateToMerchants({
        merchant_ids: Array.from(selectedMerchantIds),
        template_id: Number(templateId),
      });
      setTemplateResult(result);
    } catch (err) {
      if (handleAuthErr(err)) return;
      setTemplateError(err instanceof Error ? err.message : "Не удалось применить шаблон.");
    } finally {
      setTemplateSaving(false);
    }
  }

  async function handleApplyMethods(e: React.FormEvent) {
    e.preventDefault();
    setMethodsSaving(true);
    setMethodsError(null);
    setMethodsResult(null);
    try {
      const result = await applySelectedMethodsToMerchants({
        merchant_ids: Array.from(selectedMerchantIds),
        currency_id: Number(currencyId),
        direction,
        method_ids: Array.from(selectedMethodIds),
        personal_rate: personalRate,
        transaction_min_limit: minLimit || null,
        transaction_max_limit: maxLimit || null,
        test_mode: testMode,
        only_admin_configure: onlyAdminConfigure,
      });
      setMethodsResult(result);
    } catch (err) {
      if (handleAuthErr(err)) return;
      setMethodsError(err instanceof Error ? err.message : "Не удалось применить методы.");
    } finally {
      setMethodsSaving(false);
    }
  }

  return (
    <div className="max-w-3xl space-y-6">
      <div>
        <Link href="/admin/merchants" className="text-sm text-accent hover:underline">
          ← Мерчанты
        </Link>
        <h1 className="mt-1 text-lg font-semibold">Массовые действия с мерчантами</h1>
        <p className="mt-1 text-xs text-[var(--text-muted)]">
          Существующие пары мерчант/метод пропускаются без ошибки и без изменения ставки — как в оригинальной
          Django-админке. Созданные таким образом методы НЕ сбрасывают Redis-кэш немедленно (баг оригинала,
          сохранён как есть).
        </p>
      </div>

      <div className="card space-y-3 p-4">
        <div className="flex items-center justify-between">
          <h2 className="text-sm font-semibold">1. Выберите мерчантов</h2>
          <span className="text-xs text-[var(--text-muted)]">Выбрано: {selectedMerchantIds.size}</span>
        </div>
        <input
          value={merchantSearch}
          onChange={(e) => setMerchantSearch(e.target.value)}
          placeholder="Поиск по имени, ключу…"
          className="w-full max-w-sm rounded-md border border-[var(--border)] bg-transparent px-3 py-1.5 text-sm outline-none focus:ring-2 focus:ring-accent"
        />
        <div className="max-h-64 divide-y divide-[var(--border)] overflow-y-auto rounded-md border border-[var(--border)]">
          {loadingMerchants && <p className="px-3 py-2 text-sm text-[var(--text-muted)]">Загрузка…</p>}
          {!loadingMerchants && merchants.length === 0 && (
            <p className="px-3 py-2 text-sm text-[var(--text-muted)]">Ничего не найдено.</p>
          )}
          {merchants.map((m) => {
            const id = Number(m.id);
            return (
              <label key={id} className="flex cursor-pointer items-center gap-2 px-3 py-1.5 text-sm hover:bg-black/5 dark:hover:bg-white/5">
                <input type="checkbox" checked={selectedMerchantIds.has(id)} onChange={() => toggleMerchant(id)} />
                <span className="text-[var(--text-muted)]">#{id}</span>
                <span>{String(m.name ?? "")}</span>
              </label>
            );
          })}
        </div>
      </div>

      <div className="card space-y-4 p-4">
        <h2 className="text-sm font-semibold">2. Выберите действие</h2>
        <div className="flex gap-2">
          <button
            onClick={() => setActiveTab("template")}
            className={`rounded-md px-3 py-1.5 text-sm ${activeTab === "template" ? "bg-accent text-white" : "border border-[var(--border)]"}`}
          >
            Применить шаблон
          </button>
          <button
            onClick={() => setActiveTab("methods")}
            className={`rounded-md px-3 py-1.5 text-sm ${activeTab === "methods" ? "bg-accent text-white" : "border border-[var(--border)]"}`}
          >
            Применить методы
          </button>
        </div>

        {activeTab === "template" ? (
          <form onSubmit={handleApplyTemplate} className="space-y-3">
            {templateError && (
              <div className="rounded-md border border-red-300 bg-red-50 px-3 py-2 text-sm text-red-700 dark:border-red-900 dark:bg-red-950 dark:text-red-300">
                {templateError}
              </div>
            )}
            {templateResult && (
              <div className="rounded-md border border-green-300 bg-green-50 px-3 py-2 text-sm text-green-800 dark:border-green-900 dark:bg-green-950 dark:text-green-300">
                Создано методов: {templateResult.created_count}. Пропущено (уже существовали): {templateResult.skipped_existing_count}.
              </div>
            )}
            <div>
              <label className="mb-1 block text-xs font-medium text-[var(--text-muted)]">Шаблон *</label>
              <select
                required
                value={templateId}
                onChange={(e) => setTemplateId(e.target.value)}
                className="w-full max-w-sm rounded-md border border-[var(--border)] bg-transparent px-3 py-1.5 text-sm outline-none focus:ring-2 focus:ring-accent"
              >
                <option value="" disabled>
                  Выберите шаблон…
                </option>
                {templates.map((t) => (
                  <option key={String(t.id)} value={String(t.id)}>
                    {String(t.name)}
                  </option>
                ))}
              </select>
            </div>
            <button
              type="submit"
              disabled={templateSaving || selectedMerchantIds.size === 0}
              className="rounded-md bg-accent px-4 py-2 text-sm font-medium text-white hover:bg-accent-dark disabled:opacity-60"
            >
              {templateSaving ? "Применение…" : `Применить к ${selectedMerchantIds.size} мерчантам`}
            </button>
          </form>
        ) : (
          <form onSubmit={handleApplyMethods} className="space-y-3">
            {methodsError && (
              <div className="rounded-md border border-red-300 bg-red-50 px-3 py-2 text-sm text-red-700 dark:border-red-900 dark:bg-red-950 dark:text-red-300">
                {methodsError}
              </div>
            )}
            {methodsResult && (
              <div className="rounded-md border border-green-300 bg-green-50 px-3 py-2 text-sm text-green-800 dark:border-green-900 dark:bg-green-950 dark:text-green-300">
                Создано методов: {methodsResult.created_count}. Пропущено (уже существовали): {methodsResult.skipped_existing_count}.
              </div>
            )}
            <div className="flex flex-wrap gap-3">
              <div>
                <label className="mb-1 block text-xs font-medium text-[var(--text-muted)]">Валюта *</label>
                <select
                  required
                  value={currencyId}
                  onChange={(e) => setCurrencyId(e.target.value)}
                  className="w-40 rounded-md border border-[var(--border)] bg-transparent px-3 py-1.5 text-sm outline-none focus:ring-2 focus:ring-accent"
                >
                  <option value="" disabled>
                    Выберите…
                  </option>
                  {currencies.map((c) => (
                    <option key={String(c.id)} value={String(c.id)}>
                      {String(c.iso_code)}
                    </option>
                  ))}
                </select>
              </div>
              <div>
                <label className="mb-1 block text-xs font-medium text-[var(--text-muted)]">Направление *</label>
                <select
                  value={direction}
                  onChange={(e) => setDirection(e.target.value)}
                  className="w-32 rounded-md border border-[var(--border)] bg-transparent px-3 py-1.5 text-sm outline-none focus:ring-2 focus:ring-accent"
                >
                  {DIRECTION_CHOICES.map((d) => (
                    <option key={d} value={d}>
                      {d}
                    </option>
                  ))}
                </select>
              </div>
            </div>

            {currencyId && (
              <div>
                <label className="mb-1 block text-xs font-medium text-[var(--text-muted)]">
                  Платёжные методы ({methods.length})
                </label>
                <div className="max-h-40 divide-y divide-[var(--border)] overflow-y-auto rounded-md border border-[var(--border)]">
                  {methods.length === 0 && (
                    <p className="px-3 py-2 text-sm text-[var(--text-muted)]">
                      Нет методов для этой валюты/направления.
                    </p>
                  )}
                  {methods.map((m) => {
                    const id = Number(m.id);
                    return (
                      <label key={id} className="flex cursor-pointer items-center gap-2 px-3 py-1.5 text-sm hover:bg-black/5 dark:hover:bg-white/5">
                        <input type="checkbox" checked={selectedMethodIds.has(id)} onChange={() => toggleMethod(id)} />
                        <span>
                          {String(m.name ?? "")}
                          {m.sub_method ? ` — ${String(m.sub_method)}` : ""} ({String(m.token ?? "")})
                        </span>
                      </label>
                    );
                  })}
                </div>
              </div>
            )}

            <div className="flex flex-wrap gap-3">
              <div>
                <label className="mb-1 block text-xs font-medium text-[var(--text-muted)]">Ставка (%) *</label>
                <input
                  required
                  inputMode="decimal"
                  value={personalRate}
                  onChange={(e) => setPersonalRate(e.target.value)}
                  className="w-32 rounded-md border border-[var(--border)] bg-transparent px-3 py-1.5 text-sm outline-none focus:ring-2 focus:ring-accent"
                />
              </div>
              <div>
                <label className="mb-1 block text-xs font-medium text-[var(--text-muted)]">Мин. сумма</label>
                <input
                  inputMode="decimal"
                  value={minLimit}
                  onChange={(e) => setMinLimit(e.target.value)}
                  className="w-32 rounded-md border border-[var(--border)] bg-transparent px-3 py-1.5 text-sm outline-none focus:ring-2 focus:ring-accent"
                />
              </div>
              <div>
                <label className="mb-1 block text-xs font-medium text-[var(--text-muted)]">Макс. сумма</label>
                <input
                  inputMode="decimal"
                  value={maxLimit}
                  onChange={(e) => setMaxLimit(e.target.value)}
                  className="w-32 rounded-md border border-[var(--border)] bg-transparent px-3 py-1.5 text-sm outline-none focus:ring-2 focus:ring-accent"
                />
              </div>
            </div>

            <div className="flex gap-4">
              <label className="flex items-center gap-2 text-sm">
                <input type="checkbox" checked={testMode} onChange={(e) => setTestMode(e.target.checked)} />
                Тестовый режим
              </label>
              <label className="flex items-center gap-2 text-sm">
                <input
                  type="checkbox"
                  checked={onlyAdminConfigure}
                  onChange={(e) => setOnlyAdminConfigure(e.target.checked)}
                />
                ТОЛЬКО для ADMIN КОНФИГОВ
              </label>
            </div>

            <button
              type="submit"
              disabled={methodsSaving || selectedMerchantIds.size === 0 || selectedMethodIds.size === 0}
              className="rounded-md bg-accent px-4 py-2 text-sm font-medium text-white hover:bg-accent-dark disabled:opacity-60"
            >
              {methodsSaving
                ? "Применение…"
                : `Применить ${selectedMethodIds.size} метод(ов) к ${selectedMerchantIds.size} мерчантам`}
            </button>
          </form>
        )}
      </div>
    </div>
  );
}
