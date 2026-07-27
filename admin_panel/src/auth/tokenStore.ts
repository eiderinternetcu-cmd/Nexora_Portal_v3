import type { TokenResponse } from "../api/types";

/**
 * CRITICO — AISLAMIENTO RESPECTO AL WEB PLAYER
 *
 * El player guarda su sesion en `nexora.web_player.session.v1`
 * (web_player/src/auth/tokenStore.ts:3). Si el panel reutilizara esa clave,
 * abrir el panel desloguearia al usuario del player y viceversa, ademas de
 * mezclar dos tipos de token incompatibles (el player usa tokens de la
 * superficie `client`, el panel los de la superficie `admin`; la API los
 * rechaza cruzados, ver enforce_surface en app/services/auth_service.py).
 *
 * Todas las claves de este panel DEBEN empezar por `nexora.admin.`.
 */
const SESSION_KEY = "nexora.admin.session.v1";

export type StoredSession = {
  accessToken: string;
  refreshToken: string;
  tokenType: string;
  /** Epoch ms en el que caduca el access token. */
  accessExpiresAt: number;
};

export class TokenStore {
  load(): StoredSession | null {
    const raw = localStorage.getItem(SESSION_KEY);
    if (!raw) return null;
    try {
      const parsed = JSON.parse(raw) as StoredSession;
      if (!parsed.accessToken || !parsed.refreshToken) return null;
      return parsed;
    } catch {
      localStorage.removeItem(SESSION_KEY);
      return null;
    }
  }

  save(token: TokenResponse): StoredSession {
    const session: StoredSession = {
      accessToken: token.access_token,
      refreshToken: token.refresh_token,
      tokenType: token.token_type,
      accessExpiresAt: Date.now() + token.expires_in * 1000,
    };
    localStorage.setItem(SESSION_KEY, JSON.stringify(session));
    return session;
  }

  clear() {
    localStorage.removeItem(SESSION_KEY);
  }
}
