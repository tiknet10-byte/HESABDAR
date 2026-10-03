import { createContext, useCallback, useContext, useEffect, useState } from "react";
import { api } from "./api";

export function useApi<T = any>(path: string | null, deps: unknown[] = []) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const reload = useCallback(async () => {
    if (!path) return;
    setLoading(true);
    try {
      setData(await api<T>(path));
      setError(null);
    } catch (e: any) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [path, ...deps]);
  useEffect(() => {
    reload();
  }, [reload]);
  return { data, error, loading, reload, setData };
}

export type User = { id: number; username: string; full_name: string; role: string };
export type AuthCtx = { user: User | null; setUser: (u: User | null) => void; logout: () => void };
export const AuthContext = createContext<AuthCtx>({ user: null, setUser: () => {}, logout: () => {} });
export const useAuth = () => useContext(AuthContext);

const PERMS: Record<string, string[]> = {
  finance: ["owner", "admin", "accountant", "ai_agent"],
  reports: ["owner", "admin", "accountant", "ai_agent"],
  settings: ["owner", "admin"],
  users: ["owner"],
  backup: ["owner", "admin"],
  write: ["owner", "admin", "accountant", "receptionist", "ai_agent"],
};
export const can = (user: User | null, perm: string) => !!user && (PERMS[perm]?.includes(user.role) ?? true);

type Toast = { id: number; text: string; kind: "ok" | "error" | "info" };
export const ToastContext = createContext<(text: string, kind?: Toast["kind"]) => void>(() => {});
export const useToast = () => useContext(ToastContext);
export type { Toast };
