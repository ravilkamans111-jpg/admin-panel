"use client";

import { useState } from "react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useSchema } from "@/lib/schema-context";
import { AuthExpiredError, createSettlement, createRecord } from "@/lib/api";
import { useAuth } from "@/lib/auth-context";
import { humanizeFieldName } from "@/lib/format";
import type { AdminModelConfig } from "@/lib/types";

// "settlements" keeps its own dedicated form below — creating one runs real
// business logic (creates a linked Transaction, resolves/overrides balance
// rows) that a plain field-list form can't meaningfully represent. Every
// other `config.creatable` model goes through the generic form further
// down, backed by `app.api.generic_writes` (`POST /admin/{model_key}`).
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

export default function CreateRecordPage() {
  const params = useParams<{ key: string }>();
  const modelKey = params.key;
  const { getConfig, loading: schemaLoading } = useSchema();

  const config = getConfig(modelKey);

  if (schemaLoading) {
    return <p className="text-sm text-[var(--text-muted)]">Загрузка…</p>;
  }
  if (!config) {
    return <p className="text-sm text-red-600">Неизвестная модель «{modelKey}».</p>;
  }
  if (!config.creatable) {
    return <p className="text-sm text-red-600">Создание для «{config.verbose_name_plural}» не поддерживается.</p>;
  }

  return modelKey === "settlements" ? <CreateSettlementForm config={config} /> : <GenericCreateForm config={config} />;
}

function CreateSettlementForm({ config }: { config: AdminModelConfig }) {
  const modelKey = "settlements";
  const { logout } = useAuth();
  const router = useRouter();

  const [form, setForm] = useState<Record<string, string>>({ status: "ACCEPTED" });
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setSaving(true);
    setSaveError(null);
    try {
      const values: Record<string, string | number | null> = {};
      const allFields = [...config.creatable_fields, ...config.editable_fields];
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

/**
 * Generic create form for any `config.creatable` model without its own
 * dedicated endpoint — plain text inputs for every `creatable_fields`
 * entry, POSTed as-is to `app.api.generic_writes` (which casts each value
 * to its column's real type server-side). Matches the equally-generic
 * philosophy of the read-only list/detail views: no per-model layout, just
 * every creatable field in declaration order.
 */
function GenericCreateForm({ config }: { config: AdminModelConfig }) {
  const modelKey = config.key;
  const { logout } = useAuth();
  const router = useRouter();

  const [form, setForm] = useState<Record<string, string>>({});
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);

  function setField(field: string, value: string) {
    setForm((f) => ({ ...f, [field]: value }));
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setSaving(true);
    setSaveError(null);
    try {
      const values: Record<string, string | null> = {};
      for (const field of config.creatable_fields) {
        const raw = form[field];
        // An untouched field is omitted entirely (not sent as null) so a
        // NOT NULL column with a model-level default (e.g. TestCredits'
        // wanted_status_callback="SUCCESS") gets that default applied,
        // matching how an empty Django ModelForm field with a default
        // behaves — rather than forcing an explicit NULL that would
        // violate the column's NOT NULL constraint.
        if (raw !== undefined && raw !== "") {
          values[field] = raw;
        }
      }
      const created = await createRecord(modelKey, values);
      const newId = created.id;
      router.push(newId !== undefined ? `/admin/${modelKey}/${newId}` : `/admin/${modelKey}`);
    } catch (err) {
      if (err instanceof AuthExpiredError) {
        logout();
        router.replace("/login");
        return;
      }
      setSaveError(err instanceof Error ? err.message : "Не удалось создать запись.");
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="max-w-xl space-y-4">
      <div>
        <Link href={`/admin/${modelKey}`} className="text-sm text-accent hover:underline">
          ← {config.verbose_name_plural}
        </Link>
        <h1 className="mt-1 text-lg font-semibold">Новая запись: {config.verbose_name}</h1>
      </div>

      {saveError && (
        <div className="rounded-md border border-red-300 bg-red-50 px-3 py-2 text-sm text-red-700 dark:border-red-900 dark:bg-red-950 dark:text-red-300">
          {saveError}
        </div>
      )}

      <form onSubmit={handleSubmit} className="card space-y-3 p-4">
        {config.creatable_fields.map((field) => (
          <div key={field}>
            <label className="mb-1 block text-xs font-medium text-[var(--text-muted)]">
              {humanizeFieldName(field)}
            </label>
            <input
              value={form[field] ?? ""}
              onChange={(e) => setField(field, e.target.value)}
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
