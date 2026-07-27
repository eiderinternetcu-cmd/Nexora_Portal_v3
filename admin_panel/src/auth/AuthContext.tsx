import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import type { ReactNode } from "react";

import { AdminClient } from "../api/adminClient";
import { adminConfig } from "../api/config";
import type { AdminMe, LoginRequest, UserRole } from "../api/types";
import { TokenStore } from "./tokenStore";

/**
 * Estado de sesion del panel.
 *
 *   "loading"  -> arrancando: hay tokens y estamos validandolos contra /auth/me
 *   "anon"     -> sin sesion utilizable; RequireAuth manda a /login
 *   "authed"   -> `user` disponible con su rol
 */
export type AuthStatus = "loading" | "anon" | "authed";

export type AuthContextValue = {
  status: AuthStatus;
  user: AdminMe | null;
  role: UserRole | null;
  /** true si el usuario tiene rol admin (los reseller tienen menos permisos). */
  isAdmin: boolean;
  client: AdminClient;
  login: (input: LoginRequest) => Promise<AdminMe>;
  logout: () => Promise<void>;
  /** Revalida /auth/me. Util tras cambiar el propio perfil. */
  reload: () => Promise<void>;
};

const AuthContext = createContext<AuthContextValue | null>(null);

// Instancias unicas por pestana: el store toca localStorage y el cliente
// deduplica los refresh, asi que no deben recrearse en cada render.
const tokenStore = new TokenStore();
const adminClient = new AdminClient(adminConfig, tokenStore);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [status, setStatus] = useState<AuthStatus>(() =>
    adminClient.hasSession() ? "loading" : "anon",
  );
  const [user, setUser] = useState<AdminMe | null>(null);
  const mounted = useRef(true);

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);

  const clearSession = useCallback(() => {
    if (!mounted.current) return;
    setUser(null);
    setStatus("anon");
  }, []);

  // Un 401 irrecuperable en CUALQUIER peticion tira la sesion global.
  useEffect(() => {
    adminClient.setUnauthorizedHandler(clearSession);
    return () => adminClient.setUnauthorizedHandler(null);
  }, [clearSession]);

  const loadMe = useCallback(async () => {
    const me = await adminClient.me();
    if (!mounted.current) return me;
    setUser(me);
    setStatus("authed");
    return me;
  }, []);

  // Arranque: si hay tokens guardados, confirmamos quienes somos.
  useEffect(() => {
    if (!adminClient.hasSession()) {
      setStatus("anon");
      return;
    }
    void loadMe().catch(() => {
      adminClient.setUnauthorizedHandler(null);
      void adminClient.logout().finally(clearSession);
      adminClient.setUnauthorizedHandler(clearSession);
    });
  }, [loadMe, clearSession]);

  const login = useCallback(
    async (input: LoginRequest) => {
      await adminClient.login(input);
      return loadMe();
    },
    [loadMe],
  );

  const logout = useCallback(async () => {
    try {
      await adminClient.logout();
    } finally {
      clearSession();
    }
  }, [clearSession]);

  const reload = useCallback(async () => {
    await loadMe();
  }, [loadMe]);

  const value = useMemo<AuthContextValue>(
    () => ({
      status,
      user,
      role: user?.role ?? null,
      isAdmin: user?.role === "admin",
      client: adminClient,
      login,
      logout,
      reload,
    }),
    [status, user, login, logout, reload],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

/** Hook principal de sesion. Lanza si se usa fuera de <AuthProvider>. */
export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth debe usarse dentro de <AuthProvider>");
  return ctx;
}

/** Atajo para las pantallas: `const api = useApi();` */
export function useApi(): AdminClient {
  return useAuth().client;
}
