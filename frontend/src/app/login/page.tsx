"use client";

import { FormEvent, useState } from "react";
import { useRouter } from "next/navigation";
import { ApiError, login } from "@/lib/api";
import { sessionStore, STORAGE_KEYS } from "@/lib/storage";

export default function LoginPage() {
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      const data = await login(email, password);
      sessionStore.set(STORAGE_KEYS.preAuthToken, data.pre_auth_token);
      sessionStore.set(STORAGE_KEYS.availableBrands, JSON.stringify(data.available_brands));
      router.push("/select-brand");
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        setError("Неверный email или пароль.");
      } else {
        setError("Что-то пошло не так. Попробуйте ещё раз.");
      }
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <main className="flex min-h-screen items-center justify-center px-4">
      <div className="w-full max-w-sm">
        <div className="mb-8 text-center">
          <h1 className="text-xl font-semibold">Админ-панель брендов</h1>
          <p className="mt-1 text-sm text-[var(--text-muted)]">
            Войдите, чтобы продолжить
          </p>
        </div>
        <form onSubmit={handleSubmit} className="card p-6 space-y-4">
          {error && (
            <div className="rounded-md border border-red-300 bg-red-50 px-3 py-2 text-sm text-red-700 dark:border-red-900 dark:bg-red-950 dark:text-red-300">
              {error}
            </div>
          )}
          <div>
            <label htmlFor="email" className="mb-1 block text-sm font-medium">
              Электронная почта
            </label>
            <input
              id="email"
              type="email"
              required
              autoComplete="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              className="w-full rounded-md border border-[var(--border)] bg-transparent px-3 py-2 text-sm outline-none focus:ring-2 focus:ring-accent"
            />
          </div>
          <div>
            <label htmlFor="password" className="mb-1 block text-sm font-medium">
              Пароль
            </label>
            <input
              id="password"
              type="password"
              required
              autoComplete="current-password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              className="w-full rounded-md border border-[var(--border)] bg-transparent px-3 py-2 text-sm outline-none focus:ring-2 focus:ring-accent"
            />
          </div>
          <button
            type="submit"
            disabled={submitting}
            className="w-full rounded-md bg-accent px-3 py-2 text-sm font-medium text-white hover:bg-accent-dark disabled:opacity-60"
          >
            {submitting ? "Выполняется вход…" : "Войти"}
          </button>
        </form>
      </div>
    </main>
  );
}
