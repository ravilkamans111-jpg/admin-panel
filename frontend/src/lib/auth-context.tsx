"use client";

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
} from "react";
import { useRouter } from "next/navigation";
import { ApiError, selectBrand, tryRefresh } from "./api";
import { localStore, sessionStore, STORAGE_KEYS } from "./storage";
import type { AvailableBrand } from "./types";

interface AuthState {
  loading: boolean;
  isAuthenticated: boolean;
  accessToken: string | null;
  brandId: string | null;
  role: string | null;
  availableBrands: AvailableBrand[];
  hasPreAuth: boolean;
}

interface AuthContextValue extends AuthState {
  /** Switch to a different brand this session already has a pre_auth_token for. */
  switchBrand: (brandId: string) => Promise<{ ok: boolean; error?: string }>;
  logout: () => void;
  refreshFromStorage: () => void;
}

const AuthContext = createContext<AuthContextValue | null>(null);

function readAvailableBrands(): AvailableBrand[] {
  const raw = sessionStore.get(STORAGE_KEYS.availableBrands);
  if (!raw) return [];
  try {
    return JSON.parse(raw) as AvailableBrand[];
  } catch {
    return [];
  }
}

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [state, setState] = useState<AuthState>({
    loading: true,
    isAuthenticated: false,
    accessToken: null,
    brandId: null,
    role: null,
    availableBrands: [],
    hasPreAuth: false,
  });

  const bootstrap = useCallback(async () => {
    const accessToken = localStore.get(STORAGE_KEYS.accessToken);
    const refreshToken = localStore.get(STORAGE_KEYS.refreshToken);
    const brandId = localStore.get(STORAGE_KEYS.brandId);
    const role = localStore.get(STORAGE_KEYS.role);
    const preAuthToken = sessionStore.get(STORAGE_KEYS.preAuthToken);
    const availableBrands = readAvailableBrands();

    if (accessToken && brandId) {
      setState({
        loading: false,
        isAuthenticated: true,
        accessToken,
        brandId,
        role,
        availableBrands,
        hasPreAuth: !!preAuthToken,
      });
      return;
    }

    // No access token cached — try a silent refresh if we at least have a refresh token.
    if (refreshToken) {
      const refreshed = await tryRefresh();
      if (refreshed) {
        setState({
          loading: false,
          isAuthenticated: true,
          accessToken: refreshed.access_token,
          brandId: refreshed.brand_id,
          role: refreshed.role,
          availableBrands,
          hasPreAuth: !!preAuthToken,
        });
        return;
      }
    }

    setState({
      loading: false,
      isAuthenticated: false,
      accessToken: null,
      brandId: null,
      role: null,
      availableBrands,
      hasPreAuth: !!preAuthToken,
    });
  }, []);

  useEffect(() => {
    bootstrap();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const refreshFromStorage = useCallback(() => {
    bootstrap();
  }, [bootstrap]);

  const switchBrand = useCallback(async (brandId: string) => {
    const preAuthToken = sessionStore.get(STORAGE_KEYS.preAuthToken);
    if (!preAuthToken) {
      return { ok: false, error: "Сессия для переключения бренда истекла. Войдите заново." };
    }
    try {
      const data = await selectBrand(preAuthToken, brandId);
      localStore.set(STORAGE_KEYS.accessToken, data.access_token);
      localStore.set(STORAGE_KEYS.refreshToken, data.refresh_token);
      localStore.set(STORAGE_KEYS.brandId, data.brand_id);
      localStore.set(STORAGE_KEYS.role, data.role);
      setState((prev) => ({
        ...prev,
        isAuthenticated: true,
        accessToken: data.access_token,
        brandId: data.brand_id,
        role: data.role,
      }));
      return { ok: true };
    } catch (err) {
      if (err instanceof ApiError && (err.status === 401 || err.status === 403)) {
        return {
          ok: false,
          error:
            err.status === 403
              ? "У вас нет доступа к этому бренду."
              : "Сессия для переключения бренда истекла. Войдите заново.",
        };
      }
      return { ok: false, error: "Не удалось переключить бренд. Попробуйте ещё раз." };
    }
  }, []);

  const logout = useCallback(() => {
    localStore.remove(STORAGE_KEYS.accessToken);
    localStore.remove(STORAGE_KEYS.refreshToken);
    localStore.remove(STORAGE_KEYS.brandId);
    localStore.remove(STORAGE_KEYS.role);
    sessionStore.remove(STORAGE_KEYS.preAuthToken);
    sessionStore.remove(STORAGE_KEYS.availableBrands);
    setState({
      loading: false,
      isAuthenticated: false,
      accessToken: null,
      brandId: null,
      role: null,
      availableBrands: [],
      hasPreAuth: false,
    });
  }, []);

  const value = useMemo(
    () => ({ ...state, switchBrand, logout, refreshFromStorage }),
    [state, switchBrand, logout, refreshFromStorage]
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within an AuthProvider");
  return ctx;
}

/** Client-side guard: redirects to /login if not authenticated once bootstrap finishes. */
export function useRequireAuth() {
  const auth = useAuth();
  const router = useRouter();

  useEffect(() => {
    if (!auth.loading && !auth.isAuthenticated) {
      router.replace("/login");
    }
  }, [auth.loading, auth.isAuthenticated, router]);

  return auth;
}
