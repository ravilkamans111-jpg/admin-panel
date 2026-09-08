"use client";

import { createContext, useContext, useEffect, useState, useCallback } from "react";
import { AuthExpiredError, fetchSchema } from "./api";
import type { AdminModelConfig } from "./types";
import { useAuth } from "./auth-context";
import { useRouter } from "next/navigation";

interface SchemaContextValue {
  schema: AdminModelConfig[];
  loading: boolean;
  error: string | null;
  getConfig: (key: string) => AdminModelConfig | undefined;
  groupedByApp: Array<{ app: string; appLabel: string; models: AdminModelConfig[] }>;
  reload: () => void;
}

const SchemaContext = createContext<SchemaContextValue | null>(null);

export function SchemaProvider({ children }: { children: React.ReactNode }) {
  const { isAuthenticated, brandId, logout } = useAuth();
  const router = useRouter();
  const [schema, setSchema] = useState<AdminModelConfig[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [reloadKey, setReloadKey] = useState(0);

  useEffect(() => {
    let cancelled = false;
    if (!isAuthenticated) {
      setLoading(false);
      return;
    }
    setLoading(true);
    setError(null);
    fetchSchema()
      .then((data) => {
        if (!cancelled) {
          setSchema(data);
          setLoading(false);
        }
      })
      .catch((err) => {
        if (cancelled) return;
        if (err instanceof AuthExpiredError) {
          logout();
          router.replace("/login");
          return;
        }
        setError(err instanceof Error ? err.message : "Не удалось загрузить схему");
        setLoading(false);
      });
    return () => {
      cancelled = true;
    };
    // Reload when brand changes or explicit reload requested.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isAuthenticated, brandId, reloadKey]);

  const getConfig = useCallback(
    (key: string) => schema.find((m) => m.key === key),
    [schema]
  );

  const groupedByApp = groupByApp(schema);

  const reload = useCallback(() => setReloadKey((k) => k + 1), []);

  return (
    <SchemaContext.Provider value={{ schema, loading, error, getConfig, groupedByApp, reload }}>
      {children}
    </SchemaContext.Provider>
  );
}

function groupByApp(schema: AdminModelConfig[]) {
  const map = new Map<string, { appLabel: string; models: AdminModelConfig[] }>();
  for (const model of schema) {
    if (!map.has(model.app)) map.set(model.app, { appLabel: model.app_label, models: [] });
    map.get(model.app)!.models.push(model);
  }
  return Array.from(map.entries()).map(([app, { appLabel, models }]) => ({ app, appLabel, models }));
}

export function useSchema(): SchemaContextValue {
  const ctx = useContext(SchemaContext);
  if (!ctx) throw new Error("useSchema must be used within a SchemaProvider");
  return ctx;
}
