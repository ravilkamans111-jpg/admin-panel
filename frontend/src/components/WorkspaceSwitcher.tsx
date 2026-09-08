"use client";

import { useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { useAuth } from "@/lib/auth-context";
import { roleLabel } from "@/lib/format";

export function WorkspaceSwitcher() {
  const { brandId, availableBrands, switchBrand, hasPreAuth } = useAuth();
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [switching, setSwitching] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const containerRef = useRef<HTMLDivElement>(null);

  const current = availableBrands.find((b) => b.brand_id === brandId);
  const label = current?.display_name || brandId || "Рабочее пространство";

  async function handlePick(id: string) {
    if (id === brandId) {
      setOpen(false);
      return;
    }
    setError(null);
    setSwitching(id);
    const result = await switchBrand(id);
    setSwitching(null);
    if (result.ok) {
      setOpen(false);
      router.push("/dashboard");
      router.refresh();
    } else {
      setError(result.error || "Не удалось переключить бренд.");
      if (!hasPreAuth) {
        router.replace("/login");
      }
    }
  }

  return (
    <div className="relative" ref={containerRef}>
      <button
        onClick={() => setOpen((o) => !o)}
        className="flex items-center gap-2 rounded-md border border-[var(--border)] px-3 py-1.5 text-sm hover:border-accent"
      >
        <span className="font-medium">{label}</span>
        <svg width="12" height="12" viewBox="0 0 12 12" fill="none" aria-hidden="true">
          <path d="M2.5 4.5L6 8L9.5 4.5" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
      </button>
      {open && (
        <>
          <div className="fixed inset-0 z-10" onClick={() => setOpen(false)} />
          <div className="card absolute right-0 z-20 mt-1 w-64 p-1 shadow-lg">
            {error && (
              <div className="mb-1 rounded-md bg-red-50 px-2 py-1.5 text-xs text-red-700 dark:bg-red-950 dark:text-red-300">
                {error}
              </div>
            )}
            {availableBrands.length === 0 && (
              <div className="px-2 py-1.5 text-xs text-[var(--text-muted)]">
                В этой сессии другие рабочие пространства недоступны.
              </div>
            )}
            {availableBrands.map((b) => (
              <button
                key={b.brand_id}
                onClick={() => handlePick(b.brand_id)}
                disabled={switching !== null}
                className="flex w-full items-center justify-between rounded-md px-2 py-1.5 text-left text-sm hover:bg-black/5 dark:hover:bg-white/5 disabled:opacity-60"
              >
                <span>
                  {b.display_name}
                  <span className="ml-1 text-xs text-[var(--text-muted)]">({roleLabel(b.role)})</span>
                </span>
                {b.brand_id === brandId && <span className="text-accent">✓</span>}
                {switching === b.brand_id && <span className="text-xs">…</span>}
              </button>
            ))}
          </div>
        </>
      )}
    </div>
  );
}
