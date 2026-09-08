"use client";

import { useRouter } from "next/navigation";
import { WorkspaceSwitcher } from "./WorkspaceSwitcher";
import { useAuth } from "@/lib/auth-context";
import { roleLabel } from "@/lib/format";

export function Header() {
  const { logout, role } = useAuth();
  const router = useRouter();

  function handleLogout() {
    logout();
    router.push("/login");
  }

  return (
    <header className="flex h-14 shrink-0 items-center justify-between border-b border-[var(--border)] px-4">
      <div className="text-sm font-semibold">Админ-панель брендов</div>
      <div className="flex items-center gap-3">
        {role && <span className="text-xs text-[var(--text-muted)]">Роль: {roleLabel(role)}</span>}
        <WorkspaceSwitcher />
        <button
          onClick={handleLogout}
          className="rounded-md border border-[var(--border)] px-3 py-1.5 text-sm hover:border-accent"
        >
          Выйти
        </button>
      </div>
    </header>
  );
}
