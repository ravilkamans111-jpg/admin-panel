// Thin wrappers around localStorage/sessionStorage that stay safe during SSR
// (Next.js renders these modules on the server too, where `window` is undefined).

const isBrowser = () => typeof window !== "undefined";

export const localStore = {
  get(key: string): string | null {
    if (!isBrowser()) return null;
    try {
      return window.localStorage.getItem(key);
    } catch {
      return null;
    }
  },
  set(key: string, value: string): void {
    if (!isBrowser()) return;
    try {
      window.localStorage.setItem(key, value);
    } catch {
      /* ignore quota / privacy-mode errors */
    }
  },
  remove(key: string): void {
    if (!isBrowser()) return;
    try {
      window.localStorage.removeItem(key);
    } catch {
      /* ignore */
    }
  },
};

export const sessionStore = {
  get(key: string): string | null {
    if (!isBrowser()) return null;
    try {
      return window.sessionStorage.getItem(key);
    } catch {
      return null;
    }
  },
  set(key: string, value: string): void {
    if (!isBrowser()) return;
    try {
      window.sessionStorage.setItem(key, value);
    } catch {
      /* ignore */
    }
  },
  remove(key: string): void {
    if (!isBrowser()) return;
    try {
      window.sessionStorage.removeItem(key);
    } catch {
      /* ignore */
    }
  },
};

// Keys
export const STORAGE_KEYS = {
  accessToken: "bap.access_token",
  refreshToken: "bap.refresh_token",
  brandId: "bap.brand_id",
  role: "bap.role",
  preAuthToken: "bap.pre_auth_token", // sessionStorage only
  availableBrands: "bap.available_brands", // sessionStorage only, JSON array
};
