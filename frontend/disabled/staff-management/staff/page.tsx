"use client";

import { FormEvent, useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import {
  AuthExpiredError,
  createStaff,
  listStaff,
  resetStaffPassword,
  setStaffBrandAccess,
  unlockStaff,
  updateStaff,
} from "@/lib/api";
import { useAuth } from "@/lib/auth-context";
import { roleLabel } from "@/lib/format";
import type { StaffUser } from "@/lib/types";

const BRANDS = [
  { id: "ampay", name: "AmPay" },
  { id: "rajapay", name: "RajaPay" },
  { id: "quiet-forest", name: "quiet-forest" },
];
const ROLES = ["viewer", "operator", "brand_admin", "superadmin"];

const input =
  "rounded-md border border-[var(--border)] bg-transparent px-3 py-1.5 text-sm outline-none focus:ring-2 focus:ring-accent";
const btn = "rounded-md border border-[var(--border)] px-2.5 py-1 text-xs hover:border-accent disabled:opacity-60";

export default function StaffPage() {
  const { role, logout } = useAuth();
  const router = useRouter();
  const [users, setUsers] = useState<StaffUser[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);

  const fail = useCallback(
    (err: unknown, fallback: string) => {
      if (err instanceof AuthExpiredError) {
        logout();
        router.replace("/login");
        return;
      }
      setError(err instanceof Error ? err.message : fallback);
    },
    [logout, router]
  );

  const load = useCallback(async () => {
    try {
      setUsers(await listStaff());
    } catch (err) {
      fail(err, "Не удалось загрузить сотрудников.");
    } finally {
      setLoading(false);
    }
  }, [fail]);

  useEffect(() => {
    load();
  }, [load]);

  async function run(action: () => Promise<unknown>, fallback: string) {
    setError(null);
    try {
      await action();
      await load();
    } catch (err) {
      fail(err, fallback);
    }
  }

  function replaceUser(updated: StaffUser) {
    setUsers((prev) => prev.map((u) => (u.id === updated.id ? updated : u)));
  }

  if (role !== "superadmin") {
    return <p className="text-sm text-red-600">Раздел доступен только суперадминистратору.</p>;
  }

  return (
    <div className="space-y-4">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h1 className="text-lg font-semibold">Сотрудники</h1>
          <p className="text-xs text-[var(--text-muted)]">Учётные записи и доступ к брендам</p>
        </div>
        <button
          onClick={() => setCreating((v) => !v)}
          className="rounded-md bg-accent px-3 py-1.5 text-sm font-medium text-white hover:bg-accent-dark"
        >
          {creating ? "Отмена" : "Добавить"}
        </button>
      </div>

      {error && (
        <div className="rounded-md border border-red-300 bg-red-50 px-3 py-2 text-sm text-red-700 dark:border-red-900 dark:bg-red-950 dark:text-red-300">
          {error}
        </div>
      )}

      {creating && (
        <CreateForm
          onCreated={async () => {
            setCreating(false);
            await load();
          }}
          onError={(e) => fail(e, "Не удалось создать сотрудника.")}
        />
      )}

      {loading ? (
        <p className="text-sm text-[var(--text-muted)]">Загрузка…</p>
      ) : (
        <div className="space-y-3">
          {users.map((u) => (
            <div key={u.id} className="card space-y-3 p-4">
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div>
                  <div className="text-sm font-medium">
                    {u.full_name || u.email}
                    {u.is_superuser && <span className="ml-2 text-xs text-accent">суперпользователь</span>}
                    {!u.is_active && <span className="ml-2 text-xs text-red-600">деактивирован</span>}
                    {u.locked && <span className="ml-2 text-xs text-amber-600">заблокирован</span>}
                  </div>
                  <div className="text-xs text-[var(--text-muted)]">
                    {u.email} · последний вход: {u.last_login_at ? new Date(u.last_login_at).toLocaleString("ru-RU") : "—"}
                  </div>
                </div>
                <div className="flex flex-wrap gap-2">
                  {u.locked && (
                    <button className={btn} onClick={() => run(() => unlockStaff(u.id), "Не удалось разблокировать.")}>
                      Разблокировать
                    </button>
                  )}
                  <button
                    className={btn}
                    onClick={() => {
                      const pwd = window.prompt(`Новый пароль для ${u.email} (12+ символов, буквы и цифры):`);
                      if (pwd) run(() => resetStaffPassword(u.id, pwd), "Не удалось сбросить пароль.");
                    }}
                  >
                    Сбросить пароль
                  </button>
                  <button
                    className={btn}
                    onClick={() =>
                      run(
                        async () => replaceUser(await updateStaff(u.id, { is_superuser: !u.is_superuser })),
                        "Не удалось изменить права."
                      )
                    }
                  >
                    {u.is_superuser ? "Снять суперправа" : "Сделать суперпользователем"}
                  </button>
                  <button
                    className={btn}
                    onClick={() =>
                      run(
                        async () => replaceUser(await updateStaff(u.id, { is_active: !u.is_active })),
                        "Не удалось изменить статус."
                      )
                    }
                  >
                    {u.is_active ? "Деактивировать" : "Активировать"}
                  </button>
                </div>
              </div>

              {u.is_superuser ? (
                <p className="text-xs text-[var(--text-muted)]">Суперпользователь имеет доступ ко всем брендам.</p>
              ) : (
                <div className="flex flex-wrap gap-3">
                  {BRANDS.map((b) => (
                    <label key={b.id} className="flex items-center gap-2 text-xs">
                      <span className="w-20 text-[var(--text-muted)]">{b.name}</span>
                      <select
                        value={u.brand_access[b.id] ?? ""}
                        onChange={(e) =>
                          run(
                            async () => replaceUser(await setStaffBrandAccess(u.id, b.id, e.target.value || null)),
                            "Не удалось изменить доступ."
                          )
                        }
                        className={input}
                      >
                        <option value="">нет доступа</option>
                        {ROLES.filter((r) => r !== "superadmin").map((r) => (
                          <option key={r} value={r}>
                            {roleLabel(r)}
                          </option>
                        ))}
                      </select>
                    </label>
                  ))}
                </div>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function CreateForm({ onCreated, onError }: { onCreated: () => Promise<void>; onError: (e: unknown) => void }) {
  const [email, setEmail] = useState("");
  const [fullName, setFullName] = useState("");
  const [password, setPassword] = useState("");
  const [isSuperuser, setIsSuperuser] = useState(false);
  const [access, setAccess] = useState<Record<string, string>>({});
  const [saving, setSaving] = useState(false);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setSaving(true);
    try {
      const brand_access = Object.fromEntries(Object.entries(access).filter(([, r]) => r));
      await createStaff({ email, full_name: fullName, password, is_superuser: isSuperuser, brand_access });
      await onCreated();
    } catch (err) {
      onError(err);
    } finally {
      setSaving(false);
    }
  }

  return (
    <form onSubmit={submit} className="card max-w-xl space-y-3 p-4">
      <div className="grid gap-3 sm:grid-cols-2">
        <input required type="email" placeholder="Email" value={email} onChange={(e) => setEmail(e.target.value)} className={input} />
        <input placeholder="Имя" value={fullName} onChange={(e) => setFullName(e.target.value)} className={input} />
      </div>
      <input
        required
        type="password"
        minLength={12}
        autoComplete="new-password"
        placeholder="Временный пароль (12+ символов, буквы и цифры)"
        value={password}
        onChange={(e) => setPassword(e.target.value)}
        className={`${input} w-full`}
      />
      <label className="flex items-center gap-2 text-sm">
        <input type="checkbox" checked={isSuperuser} onChange={(e) => setIsSuperuser(e.target.checked)} />
        Суперпользователь (все бренды, управление сотрудниками)
      </label>
      {!isSuperuser && (
        <div className="flex flex-wrap gap-3">
          {BRANDS.map((b) => (
            <label key={b.id} className="flex items-center gap-2 text-xs">
              <span className="w-20 text-[var(--text-muted)]">{b.name}</span>
              <select value={access[b.id] ?? ""} onChange={(e) => setAccess((a) => ({ ...a, [b.id]: e.target.value }))} className={input}>
                <option value="">нет доступа</option>
                {ROLES.filter((r) => r !== "superadmin").map((r) => (
                  <option key={r} value={r}>
                    {roleLabel(r)}
                  </option>
                ))}
              </select>
            </label>
          ))}
        </div>
      )}
      <button
        type="submit"
        disabled={saving}
        className="rounded-md bg-accent px-4 py-2 text-sm font-medium text-white hover:bg-accent-dark disabled:opacity-60"
      >
        {saving ? "Создание…" : "Создать"}
      </button>
    </form>
  );
}
