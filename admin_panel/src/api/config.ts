/**
 * Configuracion del panel. Mismo enfoque que web_player/src/api/config.ts:19 —
 * la base de la API es window.location.origin salvo override por env, de modo
 * que en produccion el panel habla con el mismo host que lo sirve (nginx
 * proxya /api/ a la API) y en dev el proxy de Vite hace lo propio.
 */

const trimTrailingSlash = (value: string) => value.replace(/\/+$/, "");

const intFromEnv = (value: string | undefined, fallback: number) => {
  const parsed = Number.parseInt(value ?? "", 10);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : fallback;
};

export type AdminConfig = {
  /** Origen absoluto de la API, sin barra final. */
  apiBaseUrl: string;
  /**
   * Prefijo de la superficie admin, sin barra final.
   *
   * Por defecto `/api/admin`. La API monta el MISMO router de auth/users/
   * subscribers/devices/plans en `/api/v1` (compat historica) y en
   * `/api/admin`, pero sessions, subscriptions, channels, metrics, alerts y
   * audit existen SOLO bajo `/api/admin`. Por eso el panel usa `/api/admin`
   * para todo: es el superconjunto y es la superficie viva.
   * Ver app/api/admin/router.py y app/api/v1/router.py.
   */
  apiPrefix: string;
  appVersion: string;
  /** Margen para refrescar el access token antes de que caduque. */
  tokenRefreshSkewSeconds: number;
};

export const adminConfig: AdminConfig = {
  apiBaseUrl: trimTrailingSlash(
    import.meta.env.VITE_ADMIN_API_BASE_URL?.trim() || window.location.origin,
  ),
  apiPrefix: trimTrailingSlash(
    import.meta.env.VITE_ADMIN_API_PREFIX?.trim() || "/api/admin",
  ),
  appVersion:
    import.meta.env.VITE_ADMIN_APP_VERSION?.trim() || "admin-panel-0.1.0",
  tokenRefreshSkewSeconds: intFromEnv(
    import.meta.env.VITE_ADMIN_TOKEN_REFRESH_SKEW_SECONDS,
    120,
  ),
};
