"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { ApiError, selectBrand } from "@/lib/api";
import { localStore, sessionStore, STORAGE_KEYS } from "@/lib/storage";
import type { AvailableBrand } from "@/lib/types";
import { useAuth } from "@/lib/auth-context";
import { roleLabel } from "@/lib/format";

export default function SelectBrandPage() {
  const router = useRouter();
  const { refreshFromStorage } = useAuth();
  const [brands, setBrands] = useState<AvailableBrand[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState<string | null>(null);

  useEffect(() => {
    const preAuthToken = sessionStore.get(STORAGE_KEYS.preAuthToken);
    if (!preAuthToken) {
      router.replace("/login");
      return;
    }
    const raw = sessionStore.get(STORAGE_KEYS.availableBrands);
    if (raw) {
      try {
        setBrands(JSON.parse(raw));
      } catch {
        setBrands([]);
      }
    }
  }, [router]);

  async function handleSelect(brandId: string) {
    setError(null);
    setPending(brandId);
    const preAuthToken = sessionStore.get(STORAGE_KEYS.preAuthToken);
    if (!preAuthToken) {
      router.replace("/login");
      return;
    }
    try {
      const data = await selectBrand(preAuthToken, brandId);
      localStore.set(STORAGE_KEYS.accessToken, data.access_token);
      localStore.set(STORAGE_KEYS.refreshToken, data.refresh_token);
      localStore.set(STORAGE_KEYS.brandId, data.brand_id);
      localStore.set(STORAGE_KEYS.role, data.role);
      refreshFromStorage();
      router.push("/dashboard");
    } catch (err) {
      if (err instanceof ApiError && err.status === 403) {
        setError("У вас нет доступа к этому бренду.");
      } else if (err instanceof ApiError && err.status === 401) {
        setError("Сессия входа истекла. Войдите заново.");
      } else {
        setError("Что-то пошло не так. Попробуйте ещё раз.");
      }
      setPending(null);
    }
  }

  return (
    <main className="flex min-h-screen items-center justify-center px-4">
      <div className="w-full max-w-md">
        <div className="mb-8 text-center">
          <h1 className="text-xl font-semibold">Выберите рабочее пространство</h1>
          <p className="mt-1 text-sm text-[var(--text-muted)]">
            Выберите бренд, которым хотите управлять
          </p>
        </div>
        {error && (
          <div className="mb-4 rounded-md border border-red-300 bg-red-50 px-3 py-2 text-sm text-red-700 dark:border-red-900 dark:bg-red-950 dark:text-red-300">
            {error}
          </div>
        )}
        <div className="space-y-2">
          {brands.map((b) => (
            <button
              key={b.brand_id}
              onClick={() => handleSelect(b.brand_id)}
              disabled={pending !== null}
              className="card flex w-full items-center justify-between px-4 py-3 text-left hover:border-accent disabled:opacity-60"
            >
              <div>
                <div className="font-medium">{b.display_name}</div>
                <div className="text-xs text-[var(--text-muted)]">{roleLabel(b.role)}</div>
              </div>
              <span className="text-sm text-[var(--text-muted)]">
                {pending === b.brand_id ? "Вход…" : "→"}
              </span>
            </button>
          ))}
          {brands.length === 0 && (
            <p className="text-center text-sm text-[var(--text-muted)]">
              Для этой учётной записи нет доступных брендов.
            </p>
          )}
        </div>
      </div>
    </main>
  );
}
