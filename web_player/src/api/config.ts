const trimTrailingSlash = (value: string) => value.replace(/\/+$/, "");

const intFromEnv = (value: string | undefined, fallback: number) => {
  const parsed = Number.parseInt(value ?? "", 10);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : fallback;
};

export type AppConfig = {
  apiBaseUrl: string;
  playbackUrlTemplate: string;
  appVersion: string;
  heartbeatIntervalMs: number;
  playbackRenewSkewSeconds: number;
  tokenRefreshSkewSeconds: number;
};

export const appConfig: AppConfig = {
  apiBaseUrl: trimTrailingSlash(
    import.meta.env.VITE_NEXORA_API_BASE_URL?.trim() || window.location.origin,
  ),
  playbackUrlTemplate:
    import.meta.env.VITE_NEXORA_PLAYBACK_URL_TEMPLATE?.trim() ?? "",
  appVersion:
    import.meta.env.VITE_NEXORA_APP_VERSION?.trim() || "web-player-0.1.0",
  heartbeatIntervalMs: intFromEnv(
    import.meta.env.VITE_NEXORA_HEARTBEAT_INTERVAL_MS,
    45_000,
  ),
  playbackRenewSkewSeconds: intFromEnv(
    import.meta.env.VITE_NEXORA_PLAYBACK_RENEW_SKEW_SECONDS,
    15,
  ),
  tokenRefreshSkewSeconds: intFromEnv(
    import.meta.env.VITE_NEXORA_TOKEN_REFRESH_SKEW_SECONDS,
    120,
  ),
};
