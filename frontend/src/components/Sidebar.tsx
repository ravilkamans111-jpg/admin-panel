"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useSchema } from "@/lib/schema-context";

export function Sidebar() {
  const { groupedByApp, loading, error } = useSchema();
  const pathname = usePathname();

  return (
    <nav className="w-64 shrink-0 overflow-y-auto border-r border-[var(--border)] px-3 py-4">
      <Link
        href="/dashboard"
        className={`mb-4 block rounded-md px-3 py-2 text-sm font-medium ${
          pathname === "/dashboard" ? "bg-accent text-white" : "hover:bg-black/5 dark:hover:bg-white/5"
        }`}
      >
        Дашборд
      </Link>

      {loading && <p className="px-3 text-xs text-[var(--text-muted)]">Загрузка моделей…</p>}
      {error && <p className="px-3 text-xs text-red-600">{error}</p>}

      {groupedByApp.map(({ app, appLabel, models }) => (
        <div key={app} className="mb-4">
          <div className="mb-1 px-3 text-xs font-semibold uppercase tracking-wide text-[var(--text-muted)]">
            {appLabel}
          </div>
          <div className="space-y-0.5">
            {models.map((m) => {
              const href = `/admin/${m.key}`;
              const active = pathname === href || pathname.startsWith(`${href}/`);
              return (
                <Link
                  key={m.key}
                  href={href}
                  className={`block rounded-md px-3 py-1.5 text-sm ${
                    active ? "bg-accent text-white" : "hover:bg-black/5 dark:hover:bg-white/5"
                  }`}
                >
                  {m.verbose_name_plural}
                </Link>
              );
            })}
          </div>
        </div>
      ))}
    </nav>
  );
}
