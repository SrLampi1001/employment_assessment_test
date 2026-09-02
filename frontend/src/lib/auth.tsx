import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { ApiError, NetworkError, apiFetch, decodeJwtSub, type ApiResult } from "./api";
import type { CurrentUser, Locale, Tokens } from "./types";

const TOKENS_KEY = "rw_tokens";
const USER_KEY = "rw_user";

function readStored<T>(key: string): T | null {
  if (typeof window === "undefined") return null;
  try {
    const raw = window.localStorage.getItem(key);
    return raw ? (JSON.parse(raw) as T) : null;
  } catch {
    return null;
  }
}

function writeStored(key: string, value: unknown | null) {
  if (typeof window === "undefined") return;
  if (value === null) window.localStorage.removeItem(key);
  else window.localStorage.setItem(key, JSON.stringify(value));
}

export type Request = <T>(path: string, options?: RequestInit) => Promise<ApiResult<T>>;

interface AuthContextValue {
  tokens: Tokens | null;
  user: CurrentUser | null;
  ready: boolean;
  request: Request;
  login: (username: string, password: string) => Promise<void>;
  register: (input: {
    username: string;
    display_name: string;
    password: string;
    locale: Locale;
  }) => Promise<void>;
  logout: () => void;
  setLocale: (locale: Locale) => void;
}

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [tokens, setTokens] = useState<Tokens | null>(null);
  const [user, setUser] = useState<CurrentUser | null>(null);
  const [ready, setReady] = useState(false);
  const tokensRef = useRef<Tokens | null>(null);

  const applyTokens = useCallback((next: Tokens | null) => {
    tokensRef.current = next;
    setTokens(next);
    writeStored(TOKENS_KEY, next);
  }, []);

  const applyUser = useCallback((next: CurrentUser | null) => {
    setUser(next);
    writeStored(USER_KEY, next);
  }, []);

  const logout = useCallback(() => {
    applyTokens(null);
    applyUser(null);
  }, [applyTokens, applyUser]);

  useEffect(() => {
    const storedTokens = readStored<Tokens>(TOKENS_KEY);
    const storedUser = readStored<CurrentUser>(USER_KEY);
    if (storedTokens && new Date(storedTokens.refresh_expires_at).getTime() > Date.now()) {
      tokensRef.current = storedTokens;
      setTokens(storedTokens);
      setUser(storedUser);
    } else {
      writeStored(TOKENS_KEY, null);
      writeStored(USER_KEY, null);
    }
    setReady(true);
  }, []);

  const refresh = useCallback(async (): Promise<Tokens | null> => {
    const current = tokensRef.current;
    if (!current) return null;
    try {
      const { data } = await apiFetch<Tokens>("/api/v1/auth/refresh", {
        method: "POST",
        body: JSON.stringify({ refresh_token: current.refresh_token }),
      });
      applyTokens(data);
      return data;
    } catch {
      logout();
      return null;
    }
  }, [applyTokens, logout]);

  const request = useCallback<Request>(
    async <T,>(path: string, options: RequestInit = {}) => {
      const current = tokensRef.current;
      if (current && new Date(current.refresh_expires_at).getTime() < Date.now()) {
        logout();
        throw new ApiError(401, { title: "Session expired", detail: "Session expired" });
      }
      try {
        return await apiFetch<T>(path, options, current?.access_token);
      } catch (error) {
        if (error instanceof ApiError && error.status === 401 && current) {
          const next = await refresh();
          if (!next) throw error;
          return await apiFetch<T>(path, options, next.access_token);
        }
        throw error;
      }
    },
    [logout, refresh],
  );

  const finishLogin = useCallback(
    async (nextTokens: Tokens, fallback: { username: string; display_name: string; locale: Locale }) => {
      applyTokens(nextTokens);
      let actorId = decodeJwtSub(nextTokens.access_token) ?? "";
      try {
        const { data } = await apiFetch<{ actor_id: string }>(
          "/api/v1/me",
          {},
          nextTokens.access_token,
        );
        if (data?.actor_id) actorId = data.actor_id;
      } catch {
        /* server remains source of truth; keep decoded sub */
      }
      applyUser({
        actor_id: actorId,
        username: fallback.username,
        display_name: fallback.display_name || fallback.username,
        locale: fallback.locale,
      });
    },
    [applyTokens, applyUser],
  );

  const login = useCallback(
    async (username: string, password: string) => {
      const { data } = await apiFetch<Tokens>("/api/v1/auth/login", {
        method: "POST",
        body: JSON.stringify({ username, password }),
      });
      const existing = readStored<CurrentUser>(USER_KEY);
      const known = existing?.username === username ? existing : null;
      await finishLogin(data, {
        username,
        display_name: known?.display_name || username,
        locale: known?.locale ?? "es",
      });
    },
    [finishLogin],
  );

  const register = useCallback(
    async (input: { username: string; display_name: string; password: string; locale: Locale }) => {
      await apiFetch<{ user_id: string }>("/api/v1/auth/register", {
        method: "POST",
        body: JSON.stringify(input),
      });
      const { data } = await apiFetch<Tokens>("/api/v1/auth/login", {
        method: "POST",
        body: JSON.stringify({ username: input.username, password: input.password }),
      });
      await finishLogin(data, {
        username: input.username,
        display_name: input.display_name,
        locale: input.locale,
      });
    },
    [finishLogin],
  );

  const setLocale = useCallback(
    (locale: Locale) => {
      setUser((prev) => {
        const next = prev ? { ...prev, locale } : prev;
        writeStored(USER_KEY, next);
        return next;
      });
    },
    [],
  );

  const value = useMemo(
    () => ({ tokens, user, ready, request, login, register, logout, setLocale }),
    [tokens, user, ready, request, login, register, logout, setLocale],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within AuthProvider");
  return ctx;
}

export function describeError(
  error: unknown,
  t: (key: string) => string,
): string {
  if (error instanceof NetworkError) return t("errors.network");
  if (error instanceof ApiError) {
    if (error.status === 403) return t("errors.forbidden");
    if (error.status === 404) return t("errors.not_found");
    return error.detail;
  }
  return t("errors.generic");
}
