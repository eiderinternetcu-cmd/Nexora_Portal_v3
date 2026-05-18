import type { ClientTokenResponse } from "../api/types";

const SESSION_KEY = "nexora.web_player.session.v1";
const DEVICE_KEY = "nexora.web_player.device_id.v1";

export type StoredSession = {
  accessToken: string;
  refreshToken: string;
  tokenType: string;
  subscriberId: string;
  accessExpiresAt: number;
};

const randomId = () => {
  if (crypto.randomUUID) return crypto.randomUUID();
  const bytes = crypto.getRandomValues(new Uint8Array(16));
  return Array.from(bytes, (byte) => byte.toString(16).padStart(2, "0")).join("");
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

  save(token: ClientTokenResponse): StoredSession {
    const session: StoredSession = {
      accessToken: token.access_token,
      refreshToken: token.refresh_token,
      tokenType: token.token_type,
      subscriberId: token.subscriber_id,
      accessExpiresAt: Date.now() + token.expires_in * 1000,
    };
    localStorage.setItem(SESSION_KEY, JSON.stringify(session));
    return session;
  }

  clear() {
    localStorage.removeItem(SESSION_KEY);
  }

  getDeviceId() {
    const existing = localStorage.getItem(DEVICE_KEY);
    if (existing) return existing;
    const next = `web-${randomId()}`;
    localStorage.setItem(DEVICE_KEY, next);
    return next;
  }
}
