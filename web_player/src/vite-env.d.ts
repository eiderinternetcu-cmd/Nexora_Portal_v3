/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_NEXORA_API_BASE_URL?: string;
  readonly VITE_NEXORA_PLAYBACK_URL_TEMPLATE?: string;
  readonly VITE_NEXORA_APP_VERSION?: string;
  readonly VITE_NEXORA_HEARTBEAT_INTERVAL_MS?: string;
  readonly VITE_NEXORA_PLAYBACK_RENEW_SKEW_SECONDS?: string;
  readonly VITE_NEXORA_TOKEN_REFRESH_SKEW_SECONDS?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
