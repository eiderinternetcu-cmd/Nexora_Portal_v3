/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_ADMIN_API_BASE_URL?: string;
  readonly VITE_ADMIN_API_PREFIX?: string;
  readonly VITE_ADMIN_APP_VERSION?: string;
  readonly VITE_ADMIN_TOKEN_REFRESH_SKEW_SECONDS?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
