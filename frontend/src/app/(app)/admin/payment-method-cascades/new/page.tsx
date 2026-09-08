"use client";

import { useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { AuthExpiredError, ApiError, createCascade } from "@/lib/api";
import { useAuth } from "@/lib/auth-context";

// Own page (not the generic `[key]/new`) — payment_method_id is required at
// create time and then locked forever (see `app.services.cascade_write_service`),
// which the generic create form has no concept of.
export default function CreateCascadePage() {
  const { logout } = useAuth();
  const router = useRouter();

  const [name, setName] = useState("");
  const [paymentMethodId, setPaymentMethodId] = useState("");
  const [description, setDescription] = useState("");
  const [isActive, setIsActive] = useState(true);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setSaving(true);
    setSaveError(null);
    try {
      const created = await createCascade({
        name,
        payment_method_id: Number(paymentMethodId),
        description: description || null,
        is_active: isActive,
      });
      router.push(`/admin/payment-method-cascades/${created.id}`);
    } catch (err) {
      if (err instanceof AuthExpiredError) {
        logout();
        router.replace("/login");
        return;
      }
      setSaveError(err instanceof Error ? err.message : "Не удалось создать каскад.");
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="max-w-xl space-y-4">
      <div>
        <Link href="/admin/payment-method-cascades" className="text-sm text-accent hover:underline">
          ← Каскады платёжных методов
        </Link>
        <h1 className="mt-1 text-lg font-semibold">Новый каскад</h1>
      </div>

      <div className="rounded-md border border-amber-300 bg-amber-50 px-3 py-2 text-sm text-amber-900 dark:border-amber-900 dark:bg-amber-950 dark:text-amber-200">
        Платёжный метод нельзя изменить после создания каскада — как в оригинальной Django-админке.
      </div>

      {saveError && (
        <div className="rounded-md border border-red-300 bg-red-50 px-3 py-2 text-sm text-red-700 dark:border-red-900 dark:bg-red-950 dark:text-red-300">
          {saveError}
        </div>
      )}

      <form onSubmit={handleSubmit} className="card space-y-3 p-4">
        <div>
          <label className="mb-1 block text-xs font-medium text-[var(--text-muted)]">Название *</label>
          <input
            required
            value={name}
            onChange={(e) => setName(e.target.value)}
            className="w-full rounded-md border border-[var(--border)] bg-transparent px-3 py-1.5 text-sm outline-none focus:ring-2 focus:ring-accent"
          />
        </div>

        <div>
          <label className="mb-1 block text-xs font-medium text-[var(--text-muted)]">ID платёжного метода *</label>
          <input
            required
            type="number"
            value={paymentMethodId}
            onChange={(e) => setPaymentMethodId(e.target.value)}
            className="w-full rounded-md border border-[var(--border)] bg-transparent px-3 py-1.5 text-sm outline-none focus:ring-2 focus:ring-accent"
          />
          <p className="mt-1 text-[10px] text-[var(--text-muted)]">
            ID можно найти в списке{" "}
            <Link href="/admin/payment-methods" className="text-accent hover:underline">
              Платёжные методы
            </Link>
            .
          </p>
        </div>

        <div>
          <label className="mb-1 block text-xs font-medium text-[var(--text-muted)]">Описание</label>
          <textarea
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            rows={3}
            className="w-full rounded-md border border-[var(--border)] bg-transparent px-3 py-1.5 text-sm outline-none focus:ring-2 focus:ring-accent"
          />
        </div>

        <label className="flex items-center gap-2 text-sm">
          <input type="checkbox" checked={isActive} onChange={(e) => setIsActive(e.target.checked)} />
          Активен
        </label>

        <div className="flex gap-2 pt-2">
          <button
            type="submit"
            disabled={saving}
            className="rounded-md bg-accent px-4 py-2 text-sm font-medium text-white hover:bg-accent-dark disabled:opacity-60"
          >
            {saving ? "Создание…" : "Создать"}
          </button>
          <Link
            href="/admin/payment-method-cascades"
            className="rounded-md border border-[var(--border)] px-4 py-2 text-sm hover:border-accent"
          >
            Отмена
          </Link>
        </div>
      </form>
    </div>
  );
}
