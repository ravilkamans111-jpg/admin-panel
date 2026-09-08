"use client";

import { AuthProvider } from "@/lib/auth-context";
import { SchemaProvider } from "@/lib/schema-context";

export function Providers({ children }: { children: React.ReactNode }) {
  return (
    <AuthProvider>
      <SchemaProvider>{children}</SchemaProvider>
    </AuthProvider>
  );
}
