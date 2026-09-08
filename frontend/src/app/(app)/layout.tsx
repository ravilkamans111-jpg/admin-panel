"use client";

import { useRequireAuth } from "@/lib/auth-context";
import { Header } from "@/components/Header";
import { Sidebar } from "@/components/Sidebar";

export default function AppLayout({ children }: { children: React.ReactNode }) {
  const { loading, isAuthenticated } = useRequireAuth();

  if (loading) {
    return (
      <div className="flex min-h-screen items-center justify-center text-sm text-[var(--text-muted)]">
        Загрузка…
      </div>
    );
  }

  if (!isAuthenticated) {
    // useRequireAuth already triggered a redirect to /login.
    return null;
  }

  return (
    <div className="flex h-screen flex-col">
      <Header />
      <div className="flex flex-1 overflow-hidden">
        <Sidebar />
        <main className="flex-1 overflow-y-auto p-6">{children}</main>
      </div>
    </div>
  );
}
