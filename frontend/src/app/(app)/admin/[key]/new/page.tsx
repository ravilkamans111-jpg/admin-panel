"use client";

import { useState } from "react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useSchema } from "@/lib/schema-context";
import { AuthExpiredError, ApiError, createSettlement } from "@/lib/api";
import { useAuth } from "@/lib/auth-context";
import { humanizeFieldName } from "@/lib/format";

// Only "settlements" has a create page today — see `app.api.settlement_writes`
// (`POST /admin/settlements`). This page is intentionally settlement-specific
// (not a generic "create any model" form) since creating one runs real
// business logic (creates a linked Transaction, resolves/overrides balance
// rows) that a generic field-list form can't meaningfully represent.
const SETTL_TYPE_CHOICES = ["FROM_PARTNER", "TO_MERCHANT", "FROM_MERCHANT", "TO_PARTNER"];
// See the analogous comment in [id]/page.tsx — NOT_FOUND is legacy data,
// not in the current source enum, but real in existing rows.
const STATUS_CHOICES = ["ACCEPTED", "SUCCESS", "DECLINED", "APPEAL", "NOT_FOUND"];

const CREATE_ONLY_FIELD_LABELS: Record<string, string> = {
  settl_type: "Тип сеттлмента",
  balance_merchant_id: "ID баланса мерчанта",
  balance_partner_id: "ID баланса партнёра",
};

const NUMERIC_FIELDS = new Set(["balance_merchant_id", "balance_partner_id"]);
const DECIMAL_FIELDS = new Set([
  "amount", "commission", "our_funds", "clients_funds", "conversion_rate",
  "amount_in_usdt", "final_amount", "final_amount_in_usdt",
]);

export default function CreateSettlementPage() {
  const params = useParams<{ key: string }>();
  const modelKey = params.key;
  const { getConfig, loading: schemaLoading } = useSchema();
  const { logout } = useAuth();
  const router = useRouter();

  const config = getConfig(modelKey);

  const [form, setForm] = useState<Record<string, string>>({ status: "ACCEPTED" });
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);

  if (modelKey !== "settlements") {
    return <p className="text-sm text-red-600">Создание для «{modelKey}» пока не поддерживается.</p>;
  }

  if (schemaLoading) {
    return <p className="text-sm text-[var(--text-muted)]">Загрузка…</p>;
  }

  if (!config) {
    return <p className="text-sm text-red-600">Неизвестная модель «{modelKey}».</p>;
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setSaving(true);
    setSaveError(null);
    try {
      const values: Record<string, string | number | null> = {};
      const allFields = [...config!.creatable_fields, ...config!.editable_fields];
      for (const field of allFields) {
        const raw = form[field];
        if (raw === undefined || raw === "") continue;
        values[field] = NUMERIC_FIELDS.has(field) ? Number(raw) : raw;
      }
      const result = await createSettlement(values);
      router.push(`/admin/settlements/${result.settlement.id}`);
    } catch (err) {
      if (err instanceof AuthExpiredError) {
        logout();
        router.replace("/login");
        return;
      }
      setSaveError(err instanceof Error ? err.message : "Не удалось создать сеттлмент.");
    } finally {
      setSaving(false);
    }
  }

  function setField(field: string, value: string) {
    setForm((f) => ({ ...f, [field]: value }));
  }

  return (
    <div className="max-w-xl space-y-4">
      <div>
        <Link href={`/admin/${modelKey}`} className="text-sm text-accent hover:underline">
          ← {config.verbose_name_plural}
        </Link>
        <h1 className="mt-1 text-lg font-semibold">Новый сеттлмент</h1>
      </div>

      <div className="rounded-md border border-amber-300 bg-amber-50 px-3 py-2 text-sm text-amber-900 dark:border-amber-900 dark:bg-amber-950 dark:text-amber-200">
        Создание сеттлмента автоматически создаёт связанную транзакцию и пересчитывает балансы мерчанта и
        партнёра — как в оригинальной Django-админке. Для FROM_PARTNER/TO_PARTNER баланс мерчанта будет
        переопределён на служебный (admin) баланс; для TO_MERCHANT/FROM_MERCHANT баланс партнёра будет
        переопределён на баланс компании «AmPay» — независимо от того, что указано ниже.
      </div>

      <p className="text-xs text-[var(--text-muted)]">
        ID балансов можно найти в списках{" "}
        <Link href="/admin/merchant-balances" className="text-accent hover:underline">
          Балансы мерчантов
        </Link>{" "}
        и{" "}
        <Link href="/admin/company-balances" className="text-accent hover:underline">
          Балансы компаний
        </Link>
        .
      </p>

      {saveError && (
        <div className="rounded-md border border-red-300 bg-red-50 px-3 py-2 text-sm text-red-700 dark:border-red-900 dark:bg-red-950 dark:text-red-300">
          {saveError}
        </div>
      )}

      <form onSubmit={handleSubmit} className="card space-y-3 p-4">
        <div>
          <label className="mb-1 block text-xs font-medium text-[var(--text-muted)]">Тип сеттлмента</label>
          <select
            required
            value={form.settl_type ?? ""}
            onChange={(e) => setField("settl_type", e.target.value)}
            className="w-full rounded-md border border-[var(--border)] bg-transparent px-3 py-1.5 text-sm outline-none focus:ring-2 focus:ring-accent"
          >
            <option value="" disabled>
              Выберите тип…
            </option>
            {SETTL_TYPE_CHOICES.map((s) => (
              <option key={s} value={s}>
                {s}
              </option>
            ))}
          </select>
        </div>

        <div>
          <label className="mb-1 block text-xs font-medium text-[var(--text-muted)]">
            {CREATE_ONLY_FIELD_LABELS.balance_merchant_id}
          </label>
          <input
            required
            type="number"
            value={form.balance_merchant_id ?? ""}
            onChange={(e) => setField("balance_merchant_id", e.target.value)}
            className="w-full rounded-md border border-[var(--border)] bg-transparent px-3 py-1.5 text-sm outline-none focus:ring-2 focus:ring-accent"
          />
        </div>

        <div>
          <label className="mb-1 block text-xs font-medium text-[var(--text-muted)]">
            {CREATE_ONLY_FIELD_LABELS.balance_partner_id}
          </label>
          <input
            required
            type="number"
            value={form.balance_partner_id ?? ""}
            onChange={(e) => setField("balance_partner_id", e.target.value)}
            className="w-full rounded-md border border-[var(--border)] bg-transparent px-3 py-1.5 text-sm outline-none focus:ring-2 focus:ring-accent"
          />
        </div>

        <div>
          <label className="mb-1 block text-xs font-medium text-[var(--text-muted)]">Статус</label>
          <select
            value={form.status ?? "ACCEPTED"}
            onChange={(e) => setField("status", e.target.value)}
            className="w-full rounded-md border border-[var(--border)] bg-transparent px-3 py-1.5 text-sm outline-none focus:ring-2 focus:ring-accent"
          >
            {STATUS_CHOICES.map((s) => (
              <option key={s} value={s}>
                {s}
              </option>
            ))}
          </select>
        </div>

        {config.editable_fields
          .filter((f) => f !== "status")
          .map((field) => (
            <div key={field}>
              <label className="mb-1 block text-xs font-medium text-[var(--text-muted)]">
                {humanizeFieldName(field)}
                {field === "amount" && " *"}
              </label>
              <input
                required={field === "amount"}
                value={form[field] ?? ""}
                onChange={(e) => setField(field, e.target.value)}
                inputMode={DECIMAL_FIELDS.has(field) ? "decimal" : "text"}
                className="w-full rounded-md border border-[var(--border)] bg-transparent px-3 py-1.5 text-sm outline-none focus:ring-2 focus:ring-accent"
              />
            </div>
          ))}

        <div className="flex gap-2 pt-2">
          <button
            type="submit"
            disabled={saving}
            className="rounded-md bg-accent px-4 py-2 text-sm font-medium text-white hover:bg-accent-dark disabled:opacity-60"
          >
            {saving ? "Создание…" : "Создать"}
          </button>
          <Link
            href={`/admin/${modelKey}`}
            className="rounded-md border border-[var(--border)] px-4 py-2 text-sm hover:border-accent"
          >
            Отмена
          </Link>
        </div>
      </form>
    </div>
  );
}
