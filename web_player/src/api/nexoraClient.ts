import type { AppConfig } from "./config";
import { ApiError, extractApiMessage } from "./errors";
import type {
  Channel,
  ClientLoginPayload,
  ClientProfile,
  ClientTokenResponse,
  EpgEntry,
  HeartbeatResponse,
  LoginInput,
  PlaybackResponse,
} from "./types";
import { TokenStore } from "../auth/tokenStore";

type RequestOptions = {
  auth?: boolean;
  retryOnUnauthorized?: boolean;
};

export class NexoraClient {
  private refreshPromise: Promise<ClientTokenResponse> | null = null;

  constructor(
    private readonly config: AppConfig,
    private readonly store: TokenStore,
  ) {}

  currentSession() {
    return this.store.load();
  }

  deviceId() {
    return this.store.getDeviceId();
  }

  async getDevices() {
    return this.request<Array<{ id: string; device_id: string; is_blocked: boolean }>>("/api/client/profile/devices");
  }

  async login(input: LoginInput) {
    const payload: ClientLoginPayload = {
      username: input.username.trim(),
      password: input.password?.trim() || undefined,
      activation_code: input.activationCode?.trim() || undefined,
      device_id: this.store.getDeviceId(),
      device_type: "web_player",
      model: navigator.platform || "browser",
      brand: "Nexora",
      app_version: this.config.appVersion,
      os_version: navigator.userAgent,
    };

    const token = await this.request<ClientTokenResponse>(
      "/api/client/auth/login",
      { method: "POST", body: JSON.stringify(payload) },
      { auth: false },
    );
    this.store.save(token);

    if (token.device_registration === "limit_reached") {
      try {
        const devices = await this.getDevices();
        const activeDev = devices.find((d) => !d.is_blocked && d.device_id);
        if (activeDev) {
          this.store.setDeviceId(activeDev.device_id);
        }
      } catch {
        // ignore fallback error
      }
    }
    return token;
  }

  async refresh() {
    if (this.refreshPromise) return this.refreshPromise;
    const session = this.store.load();
    if (!session?.refreshToken) {
      throw new ApiError(401, "No hay refresh token disponible.");
    }

    this.refreshPromise = this.request<ClientTokenResponse>(
      "/api/client/auth/refresh",
      {
        method: "POST",
        body: JSON.stringify({ refresh_token: session.refreshToken }),
      },
      { auth: false },
    )
      .then((token) => {
        this.store.save(token);
        return token;
      })
      .finally(() => {
        this.refreshPromise = null;
      });

    return this.refreshPromise;
  }

  async logout() {
    const session = this.store.load();
    if (!session) return;
    try {
      await this.request<void>(
        "/api/client/auth/logout",
        {
          method: "POST",
          body: JSON.stringify({ refresh_token: session.refreshToken }),
        },
        { auth: true, retryOnUnauthorized: false },
      );
    } finally {
      this.store.clear();
    }
  }

  async getProfile() {
    return this.request<ClientProfile>("/api/client/profile");
  }

  async getChannels() {
    return this.request<Channel[]>("/api/client/catalog/channels");
  }

  async getEpg(channelKey: string) {
    return this.request<EpgEntry[]>(`/api/client/catalog/channels/${encodeURIComponent(channelKey)}/epg`);
  }

  async authorizePlayback(channelKey: string) {
    try {
      return await this.request<PlaybackResponse>("/api/client/playback/authorize", {
        method: "POST",
        body: JSON.stringify({
          device_id: this.store.getDeviceId(),
          channel_id: channelKey,
        }),
      });
    } catch (err) {
      if (err instanceof ApiError && String(err.message).includes("DEVICE_NOT_REGISTERED")) {
        try {
          const devices = await this.getDevices();
          const activeDev = devices.find((d) => !d.is_blocked && d.device_id);
          if (activeDev && activeDev.device_id !== this.store.getDeviceId()) {
            this.store.setDeviceId(activeDev.device_id);
            return await this.request<PlaybackResponse>("/api/client/playback/authorize", {
              method: "POST",
              body: JSON.stringify({
                device_id: activeDev.device_id,
                channel_id: channelKey,
              }),
            });
          }
        } catch {
          // ignore
        }
      }
      throw err;
    }
  }

  async reissuePlayback(channelKey: string) {
    const deviceId = encodeURIComponent(this.store.getDeviceId());
    const channel = encodeURIComponent(channelKey);
    return this.request<PlaybackResponse>(`/api/client/playback/${channel}?device_id=${deviceId}`);
  }

  async heartbeat() {
    return this.request<HeartbeatResponse>("/api/client/profile/devices/heartbeat", {
      method: "POST",
      body: JSON.stringify({
        device_id: this.store.getDeviceId(),
        app_version: this.config.appVersion,
      }),
    });
  }

  private async ensureFreshAccessToken() {
    const session = this.store.load();
    if (!session) return;
    const skewMs = this.config.tokenRefreshSkewSeconds * 1000;
    if (Date.now() + skewMs >= session.accessExpiresAt) {
      await this.refresh();
    }
  }

  private async request<T>(
    path: string,
    init: RequestInit = {},
    options: RequestOptions = {},
  ): Promise<T> {
    const needsAuth = options.auth ?? true;
    const retryOnUnauthorized = options.retryOnUnauthorized ?? true;

    if (needsAuth) await this.ensureFreshAccessToken();

    const session = this.store.load();
    const headers = new Headers(init.headers);
    headers.set("Accept", "application/json");
    if (init.body) headers.set("Content-Type", "application/json");
    if (needsAuth && session?.accessToken) {
      headers.set("Authorization", `Bearer ${session.accessToken}`);
    }

    const response = await fetch(`${this.config.apiBaseUrl}${path}`, {
      ...init,
      headers,
    });

    if (response.status === 401 && needsAuth && retryOnUnauthorized) {
      try {
        await this.refresh();
        return this.request<T>(path, init, {
          ...options,
          retryOnUnauthorized: false,
        });
      } catch (error) {
        this.store.clear();
        throw error;
      }
    }

    if (response.status === 204) return undefined as T;

    const text = await response.text();
    const payload = text ? safeJson(text) : undefined;

    if (!response.ok) {
      const message = extractApiMessage(payload, response.statusText);
      throw new ApiError(response.status, message, payload);
    }

    return payload as T;
  }
}

const safeJson = (text: string) => {
  try {
    return JSON.parse(text) as unknown;
  } catch {
    return text;
  }
};
