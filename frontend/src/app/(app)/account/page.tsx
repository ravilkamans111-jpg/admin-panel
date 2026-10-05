"use client";

import { FormEvent, useState } from "react";
import { useRouter } from "next/navigation";
import { ApiError, AuthExpiredError, changePassword } from "@/lib/api";
import { useAuth } from "@/lib/auth-context";

export default function AccountPage() {
  const { logout } = useAuth();
  const router = useRouter();
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [repeat, setRepeat] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState(false);

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setDone(false);
    if (next !== repeat) {
      setError("Новые пароли не совпадают.");
      return;
    }
    setSaving(true);
    try {
      await changePassword(current, next);
      setCurrent("");
      setNext("");
      setRepeat("");
      setDone(true);
    } catch (err) {
      if (err instanceof AuthExpiredError) {
        logout();
        router.replace("/login");
        return;
      }
      setError(err instanceof ApiError || err instanceof Error ? err.message : "Не удалось сменить пароль.");
    } finally {
      setSaving(false);
    }
  }

  const input =
    "w-full rounded-md border border-[var(--border)] bg-transparent px-3 py-1.5 text-sm outline-none focus:ring-2 focus:ring-accent";

  return (
    <div className="max-w-md space-y-4">
      <h1 className="text-lg font-semibold">Смена пароля</h1>
      <p className="text-xs text-[var(--text-muted)]">
        Не короче 12 символов, буквы и цифры. Остальные ваши сессии будут завершены.
      </p>
      {error && (
        <div className="rounded-md border border-red-300 bg-red-50 px-3 py-2 text-sm text-red-700 dark:border-red-900 dark:bg-red-950 dark:text-red-300">
          {error}
        </div>
      )}
      {done && (
        <div className="rounded-md border border-green-300 bg-green-50 px-3 py-2 text-sm text-green-800 dark:border-green-900 dark:bg-green-950 dark:text-green-300">
          Пароль изменён.
        </div>
      )}
      <form onSubmit={handleSubmit} className="card space-y-3 p-4">
        <div>
          <label className="mb-1 block text-xs font-medium text-[var(--text-muted)]">Текущий пароль</label>
          <input type="password" required autoComplete="current-password" value={current} onChange={(e) => setCurrent(e.target.value)} className={input} />
        </div>
        <div>
          <label className="mb-1 block text-xs font-medium text-[var(--text-muted)]">Новый пароль</label>
          <input type="password" required minLength={12} autoComplete="new-password" value={next} onChange={(e) => setNext(e.target.value)} className={input} />
        </div>
        <div>
          <label className="mb-1 block text-xs font-medium text-[var(--text-muted)]">Повторите новый пароль</label>
          <input type="password" required autoComplete="new-password" value={repeat} onChange={(e) => setRepeat(e.target.value)} className={input} />
        </div>
        <button
          type="submit"
          disabled={saving}
          className="rounded-md bg-accent px-4 py-2 text-sm font-medium text-white hover:bg-accent-dark disabled:opacity-60"
        >
          {saving ? "Сохранение…" : "Сменить пароль"}
        </button>
      </form>
    </div>
  );
}
