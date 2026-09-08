"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import {
  AuthExpiredError,
  ApiError,
  fetchModelDetail,
  fetchModelList,
  updateCascade,
  createCascadeItem,
  updateCascadeItem,
  deleteCascadeItem,
} from "@/lib/api";
import { useAuth } from "@/lib/auth-context";
import { formatCellValue } from "@/lib/format";

// Own page (not the generic `[key]/[id]`) — a cascade is a parent record
// plus an ordered child collection (items) with real cross-record
// validation on both (see `app.services.cascade_write_service`), which the
// generic flat-field detail view has no way to represent.

type CascadeItem = {
  id: number;
  cascade_id: number;
  payment_method_company_id: number;
  priority: number;
  is_active: boolean;
};

export default function CascadeDetailPage() {
  const params = useParams<{ id: string }>();
  const id = params.id;
  const { logout } = useAuth();
  const router = useRouter();

  const [cascade, setCascade] = useState<Record<string, unknown> | null>(null);
  const [items, setItems] = useState<CascadeItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Cascade base-field editing
  const [editingCascade, setEditingCascade] = useState(false);
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [isActive, setIsActive] = useState(true);
  const [cascadeSaveError, setCascadeSaveError] = useState<string | null>(null);
  const [cascadeSaving, setCascadeSaving] = useState(false);

  // New-item mini form
  const [newPmcId, setNewPmcId] = useState("");
  const [newPriority, setNewPriority] = useState("");
  const [itemError, setItemError] = useState<string | null>(null);
  const [itemSaving, setItemSaving] = useState(false);

  // Per-item inline edit state (priority / is_active)
  const [editingItemId, setEditingItemId] = useState<number | null>(null);
  const [editPriority, setEditPriority] = useState("");
  const [editItemActive, setEditItemActive] = useState(true);

  function handleAuthErr(err: unknown): boolean {
    if (err instanceof AuthExpiredError) {
      logout();
      router.replace("/login");
      return true;
    }
    return false;
  }

  function load() {
    let cancelled = false;
    setLoading(true);
    setError(null);
    Promise.all([
      fetchModelDetail("payment-method-cascades", id),
      fetchModelList("payment-method-cascade-items", { cascade_id: id, page_size: 200, ordering: "priority" }),
    ])
      .then(([cascadeData, itemsData]) => {
        if (cancelled) return;
        setCascade(cascadeData);
        setName(String(cascadeData.name ?? ""));
        setDescription(cascadeData.description == null ? "" : String(cascadeData.description));
        setIsActive(Boolean(cascadeData.is_active));
        setItems(itemsData.items as CascadeItem[]);
      })
      .catch((err) => {
        if (cancelled || handleAuthErr(err)) return;
        setError(err instanceof ApiError ? err.message : "Не удалось загрузить каскад.");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }

  useEffect(load, [id]); // eslint-disable-line react-hooks/exhaustive-deps

  async function handleSaveCascade() {
    setCascadeSaving(true);
    setCascadeSaveError(null);
    try {
      const updated = await updateCascade(id, { name, description: description || null, is_active: isActive });
      setCascade(updated);
      setEditingCascade(false);
    } catch (err) {
      if (handleAuthErr(err)) return;
      setCascadeSaveError(err instanceof Error ? err.message : "Не удалось сохранить каскад.");
    } finally {
      setCascadeSaving(false);
    }
  }

  async function handleAddItem(e: React.FormEvent) {
    e.preventDefault();
    setItemSaving(true);
    setItemError(null);
    try {
      await createCascadeItem(id, {
        payment_method_company_id: Number(newPmcId),
        priority: Number(newPriority),
      });
      setNewPmcId("");
      setNewPriority("");
      load();
    } catch (err) {
      if (handleAuthErr(err)) return;
      setItemError(err instanceof Error ? err.message : "Не удалось добавить элемент.");
    } finally {
      setItemSaving(false);
    }
  }

  function startEditingItem(item: CascadeItem) {
    setEditingItemId(item.id);
    setEditPriority(String(item.priority));
    setEditItemActive(item.is_active);
    setItemError(null);
  }

  async function handleSaveItem(itemId: number) {
    setItemSaving(true);
    setItemError(null);
    try {
      await updateCascadeItem(itemId, { priority: Number(editPriority), is_active: editItemActive });
      setEditingItemId(null);
      load();
    } catch (err) {
      if (handleAuthErr(err)) return;
      setItemError(err instanceof Error ? err.message : "Не удалось сохранить элемент.");
    } finally {
      setItemSaving(false);
    }
  }

  async function handleDeleteItem(itemId: number) {
    setItemSaving(true);
    setItemError(null);
    try {
      await deleteCascadeItem(itemId);
      load();
    } catch (err) {
      if (handleAuthErr(err)) return;
      setItemError(err instanceof Error ? err.message : "Не удалось удалить элемент.");
    } finally {
      setItemSaving(false);
    }
  }

  if (loading) return <p className="text-sm text-[var(--text-muted)]">Загрузка…</p>;
  if (error) return <p className="text-sm text-red-600">{error}</p>;
  if (!cascade) return null;

  return (
    <div className="space-y-6">
      <div>
        <Link href="/admin/payment-method-cascades" className="text-sm text-accent hover:underline">
          ← Каскады платёжных методов
        </Link>
        <h1 className="mt-1 text-lg font-semibold">Каскад #{id}</h1>
      </div>

      <div className="card space-y-3 p-4">
        {!editingCascade ? (
          <>
            <div className="grid grid-cols-1 gap-2 text-sm sm:grid-cols-2">
              <div>
                <span className="text-xs text-[var(--text-muted)]">Название: </span>
                {formatCellValue(cascade.name)}
              </div>
              <div>
                <span className="text-xs text-[var(--text-muted)]">Платёжный метод (ID, неизменяем): </span>
                {formatCellValue(cascade.payment_method_id)}
              </div>
              <div className="sm:col-span-2">
                <span className="text-xs text-[var(--text-muted)]">Описание: </span>
                {formatCellValue(cascade.description)}
              </div>
              <div>
                <span className="text-xs text-[var(--text-muted)]">Активен: </span>
                {formatCellValue(cascade.is_active)}
              </div>
            </div>
            <button
              onClick={() => setEditingCascade(true)}
              className="rounded-md bg-accent px-3 py-1.5 text-sm font-medium text-white hover:bg-accent-dark"
            >
              Редактировать
            </button>
          </>
        ) : (
          <>
            {cascadeSaveError && (
              <div className="rounded-md border border-red-300 bg-red-50 px-3 py-2 text-sm text-red-700 dark:border-red-900 dark:bg-red-950 dark:text-red-300">
                {cascadeSaveError}
              </div>
            )}
            <div>
              <label className="mb-1 block text-xs font-medium text-[var(--text-muted)]">Название</label>
              <input
                value={name}
                onChange={(e) => setName(e.target.value)}
                className="w-full max-w-md rounded-md border border-[var(--border)] bg-transparent px-3 py-1.5 text-sm outline-none focus:ring-2 focus:ring-accent"
              />
            </div>
            <div>
              <label className="mb-1 block text-xs font-medium text-[var(--text-muted)]">Описание</label>
              <textarea
                value={description}
                onChange={(e) => setDescription(e.target.value)}
                rows={2}
                className="w-full max-w-md rounded-md border border-[var(--border)] bg-transparent px-3 py-1.5 text-sm outline-none focus:ring-2 focus:ring-accent"
              />
            </div>
            <label className="flex items-center gap-2 text-sm">
              <input type="checkbox" checked={isActive} onChange={(e) => setIsActive(e.target.checked)} />
              Активен
            </label>
            <div className="flex gap-2">
              <button
                onClick={handleSaveCascade}
                disabled={cascadeSaving}
                className="rounded-md bg-accent px-4 py-2 text-sm font-medium text-white hover:bg-accent-dark disabled:opacity-60"
              >
                {cascadeSaving ? "Сохранение…" : "Сохранить"}
              </button>
              <button
                onClick={() => setEditingCascade(false)}
                disabled={cascadeSaving}
                className="rounded-md border border-[var(--border)] px-4 py-2 text-sm hover:border-accent disabled:opacity-60"
              >
                Отмена
              </button>
            </div>
          </>
        )}
      </div>

      <div className="space-y-3">
        <h2 className="text-sm font-semibold">Элементы каскада</h2>

        {itemError && (
          <div className="rounded-md border border-red-300 bg-red-50 px-3 py-2 text-sm text-red-700 dark:border-red-900 dark:bg-red-950 dark:text-red-300">
            {itemError}
          </div>
        )}

        <div className="card divide-y divide-[var(--border)]">
          {items.length === 0 && (
            <p className="px-4 py-3 text-sm text-[var(--text-muted)]">Элементов пока нет.</p>
          )}
          {items.map((item) => (
            <div key={item.id} className="flex flex-wrap items-center justify-between gap-3 px-4 py-3 text-sm">
              {editingItemId === item.id ? (
                <>
                  <span className="text-[var(--text-muted)]">
                    Конфиг метода у партнёра #{item.payment_method_company_id}
                  </span>
                  <div className="flex items-center gap-3">
                    <label className="flex items-center gap-1 text-xs">
                      Приоритет
                      <input
                        type="number"
                        value={editPriority}
                        onChange={(e) => setEditPriority(e.target.value)}
                        className="w-20 rounded-md border border-[var(--border)] bg-transparent px-2 py-1 text-sm outline-none focus:ring-2 focus:ring-accent"
                      />
                    </label>
                    <label className="flex items-center gap-1 text-xs">
                      <input
                        type="checkbox"
                        checked={editItemActive}
                        onChange={(e) => setEditItemActive(e.target.checked)}
                      />
                      Активен
                    </label>
                    <button
                      onClick={() => handleSaveItem(item.id)}
                      disabled={itemSaving}
                      className="rounded-md bg-accent px-2 py-1 text-xs font-medium text-white hover:bg-accent-dark disabled:opacity-60"
                    >
                      Сохранить
                    </button>
                    <button
                      onClick={() => setEditingItemId(null)}
                      disabled={itemSaving}
                      className="rounded-md border border-[var(--border)] px-2 py-1 text-xs hover:border-accent disabled:opacity-60"
                    >
                      Отмена
                    </button>
                  </div>
                </>
              ) : (
                <>
                  <span>
                    Приоритет <strong>{item.priority}</strong> — Конфиг метода у партнёра #
                    {item.payment_method_company_id} — {item.is_active ? "активен" : "неактивен"}
                  </span>
                  <div className="flex gap-2">
                    <button
                      onClick={() => startEditingItem(item)}
                      className="rounded-md border border-[var(--border)] px-2 py-1 text-xs hover:border-accent"
                    >
                      Изменить
                    </button>
                    <button
                      onClick={() => handleDeleteItem(item.id)}
                      disabled={itemSaving}
                      className="rounded-md border border-red-300 px-2 py-1 text-xs text-red-700 hover:bg-red-50 disabled:opacity-60 dark:border-red-900 dark:text-red-300 dark:hover:bg-red-950"
                    >
                      Удалить
                    </button>
                  </div>
                </>
              )}
            </div>
          ))}
        </div>

        <form onSubmit={handleAddItem} className="card flex flex-wrap items-end gap-3 p-4">
          <div>
            <label className="mb-1 block text-xs font-medium text-[var(--text-muted)]">ID конфига метода у партнёра *</label>
            <input
              required
              type="number"
              value={newPmcId}
              onChange={(e) => setNewPmcId(e.target.value)}
              className="w-48 rounded-md border border-[var(--border)] bg-transparent px-3 py-1.5 text-sm outline-none focus:ring-2 focus:ring-accent"
            />
          </div>
          <div>
            <label className="mb-1 block text-xs font-medium text-[var(--text-muted)]">Приоритет *</label>
            <input
              required
              type="number"
              value={newPriority}
              onChange={(e) => setNewPriority(e.target.value)}
              className="w-28 rounded-md border border-[var(--border)] bg-transparent px-3 py-1.5 text-sm outline-none focus:ring-2 focus:ring-accent"
            />
          </div>
          <button
            type="submit"
            disabled={itemSaving}
            className="rounded-md bg-accent px-4 py-2 text-sm font-medium text-white hover:bg-accent-dark disabled:opacity-60"
          >
            Добавить
          </button>
          <p className="w-full text-[10px] text-[var(--text-muted)]">
            ID можно найти в списке{" "}
            <Link
              href={`/admin/payment-method-companies?payment_method_id=${cascade.payment_method_id}`}
              className="text-accent hover:underline"
            >
              Конфиги методов у партнёров
            </Link>{" "}
            — платёжный метод должен совпадать с методом каскада ({formatCellValue(cascade.payment_method_id)}).
          </p>
        </form>
      </div>
    </div>
  );
}
