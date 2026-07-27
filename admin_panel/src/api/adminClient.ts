import type { AdminConfig } from "./config";
import { ApiError, SessionExpiredError, extractApiMessage } from "./errors";
import type {
  ActiveAlertsResponse,
  AdminMe,
  ApiResponse,
  AuditEntry,
  AuditQuery,
  Channel,
  Device,
  DeviceBlockRequest,
  LiveSession,
  LoginRequest,
  MessageResponse,
  NodeHealth,
  PageQuery,
  PaginatedResponse,
  Plan,
  PlanCreate,
  PlanUpdate,
  StreamStatus,
  Subscriber,
  SubscriberActiveStatus,
  SubscriberCreate,
  SubscriberFull,
  SubscriberListQuery,
  SubscriberSession,
  SubscriberUpdate,
  Subscription,
  SubscriptionCreate,
  SubscriptionRenew,
  SystemMetrics,
  TokenResponse,
  User,
  UserCreate,
  UserPasswordChange,
  UserUpdate,
} from "./types";
import { TokenStore } from "../auth/tokenStore";

type RequestOptions = {
  /** Adjuntar Authorization: Bearer. Por defecto true. */
  auth?: boolean;
  /** Reintentar una vez tras refrescar el token si la API responde 401. Por defecto true. */
  retryOnUnauthorized?: boolean;
  /** Query string. Los valores undefined/null se omiten. */
  query?: QueryParams;
  signal?: AbortSignal;
};

export type QueryParams = Record<
  string,
  string | number | boolean | null | undefined
>;

const buildQuery = (params?: QueryParams) => {
  if (!params) return "";
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value === undefined || value === null || value === "") continue;
    search.set(key, String(value));
  }
  const qs = search.toString();
  return qs ? `?${qs}` : "";
};

const safeJson = (text: string) => {
  try {
    return JSON.parse(text) as unknown;
  } catch {
    return text;
  }
};

/**
 * Cliente HTTP del panel.
 *
 * COMO USARLO DESDE UNA PANTALLA
 *   import { useApi } from "../../auth/AuthContext";
 *   const api = useApi();
 *   const page = await api.listSubscribers({ page: 1, page_size: 50 });
 *
 * Cada metodo devuelve YA el cuerpo JSON parseado y tipado. Fijate en la
 * envoltura: los listados paginados devuelven PaginatedResponse<T> (usa
 * `.data`), los detalles devuelven ApiResponse<T> (usa `.data`, puede ser
 * null) y algunos endpoints (channels, sessions/live, audit, metrics)
 * devuelven el objeto o el array desnudo. Los tipos lo reflejan.
 *
 * SI TU ENDPOINT NO ESTA ENVUELTO AQUI no anadas un metodo a medias: usa los
 * escape hatches genericos, que hacen exactamente lo mismo (auth, refresh,
 * errores tipados) con la ruta relativa al prefijo:
 *   await api.get<ApiResponse<Foo>>("/foo", { query: { page: 1 } });
 *   await api.post<MessageResponse>("/foo/1/bar", { reason: "x" });
 *
 * ERRORES: todo fallo lanza `ApiError` con `.status` y `.payload`. En la UI,
 * `messageForError(err)` da el texto presentable. Un 401 irrecuperable dispara
 * el logout global (ver setUnauthorizedHandler) y lanza `SessionExpiredError`.
 */
export class AdminClient {
  private refreshPromise: Promise<TokenResponse> | null = null;
  private onUnauthorized: (() => void) | null = null;

  constructor(
    private readonly config: AdminConfig,
    private readonly store: TokenStore,
  ) {}

  /**
   * Lo registra AuthProvider. Se invoca cuando la sesion queda invalidada de
   * forma irrecuperable (refresh fallido o 401 sin refresh posible).
   * Las pantallas no deberian llamar a esto.
   */
  setUnauthorizedHandler(handler: (() => void) | null) {
    this.onUnauthorized = handler;
  }

  hasSession() {
    return this.store.load() !== null;
  }

  // -------------------------------------------------------------------------
  // Auth
  // -------------------------------------------------------------------------

  /** POST {prefix}/auth/login. Guarda el token y lo devuelve. */
  async login(input: LoginRequest) {
    const body: LoginRequest = {
      username: input.username.trim(),
      password: input.password,
    };
    const token = await this.request<TokenResponse>(
      "/auth/login",
      { method: "POST", body: JSON.stringify(body) },
      { auth: false },
    );
    this.store.save(token);
    return token;
  }

  /** POST {prefix}/auth/refresh. Rota el par de tokens. Deduplicado. */
  async refresh() {
    if (this.refreshPromise) return this.refreshPromise;

    const session = this.store.load();
    if (!session?.refreshToken) {
      throw new SessionExpiredError("No hay refresh token disponible.");
    }

    this.refreshPromise = this.request<TokenResponse>(
      "/auth/refresh",
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

  /** POST {prefix}/auth/logout. Limpia el almacenamiento pase lo que pase. */
  async logout() {
    const session = this.store.load();
    if (!session) return;
    try {
      await this.request<MessageResponse>(
        "/auth/logout",
        {
          method: "POST",
          body: JSON.stringify({ refresh_token: session.refreshToken }),
        },
        { auth: true, retryOnUnauthorized: false },
      );
    } catch {
      // Un logout que falla en el servidor no debe dejar la sesion local viva.
    } finally {
      this.store.clear();
    }
  }

  /** GET {prefix}/auth/me. Unica via para conocer el rol del usuario. */
  async me() {
    return this.request<AdminMe>("/auth/me");
  }

  // -------------------------------------------------------------------------
  // Usuarios del panel  (requiere rol admin)
  // -------------------------------------------------------------------------

  listUsers(query: PageQuery = {}) {
    return this.get<PaginatedResponse<User>>("/users", { query });
  }

  getUser(userId: string) {
    return this.get<ApiResponse<User>>(`/users/${encodeURIComponent(userId)}`);
  }

  createUser(body: UserCreate) {
    return this.post<ApiResponse<User>>("/users", body);
  }

  updateUser(userId: string, body: UserUpdate) {
    return this.patch<ApiResponse<User>>(`/users/${encodeURIComponent(userId)}`, body);
  }

  deleteUser(userId: string) {
    return this.del<MessageResponse>(`/users/${encodeURIComponent(userId)}`);
  }

  changeOwnPassword(body: UserPasswordChange) {
    return this.post<MessageResponse>("/users/me/change-password", body);
  }

  // -------------------------------------------------------------------------
  // Suscriptores
  // -------------------------------------------------------------------------

  listSubscribers(query: SubscriberListQuery = {}) {
    return this.get<PaginatedResponse<Subscriber>>("/subscribers", { query });
  }

  getSubscriber(subId: string) {
    return this.get<ApiResponse<SubscriberFull>>(`/subscribers/${encodeURIComponent(subId)}`);
  }

  createSubscriber(body: SubscriberCreate) {
    return this.post<ApiResponse<SubscriberFull>>("/subscribers", body);
  }

  updateSubscriber(subId: string, body: SubscriberUpdate) {
    return this.patch<ApiResponse<Subscriber>>(`/subscribers/${encodeURIComponent(subId)}`, body);
  }

  setSubscriberPassword(subId: string, newPassword: string) {
    return this.post<MessageResponse>(
      `/subscribers/${encodeURIComponent(subId)}/set-password`,
      { new_password: newPassword },
    );
  }

  getSubscriberStatus(subId: string) {
    return this.get<ApiResponse<SubscriberActiveStatus>>(
      `/subscribers/${encodeURIComponent(subId)}/status`,
    );
  }

  suspendSubscriber(subId: string) {
    return this.post<MessageResponse>(`/subscribers/${encodeURIComponent(subId)}/suspend`);
  }

  activateSubscriber(subId: string) {
    return this.post<MessageResponse>(`/subscribers/${encodeURIComponent(subId)}/activate`);
  }

  deleteSubscriber(subId: string) {
    return this.del<MessageResponse>(`/subscribers/${encodeURIComponent(subId)}`);
  }

  // -------------------------------------------------------------------------
  // Planes
  // -------------------------------------------------------------------------

  listPlans() {
    return this.get<ApiResponse<Plan[]>>("/plans");
  }

  getPlan(planId: string) {
    return this.get<ApiResponse<Plan>>(`/plans/${encodeURIComponent(planId)}`);
  }

  createPlan(body: PlanCreate) {
    return this.post<ApiResponse<Plan>>("/plans", body);
  }

  updatePlan(planId: string, body: PlanUpdate) {
    return this.patch<ApiResponse<Plan>>(`/plans/${encodeURIComponent(planId)}`, body);
  }

  deletePlan(planId: string) {
    return this.del<MessageResponse>(`/plans/${encodeURIComponent(planId)}`);
  }

  // -------------------------------------------------------------------------
  // Suscripciones  (colgadas de un suscriptor)
  // -------------------------------------------------------------------------

  listSubscriptions(subId: string) {
    return this.get<ApiResponse<Subscription[]>>(
      `/subscribers/${encodeURIComponent(subId)}/subscriptions`,
    );
  }

  createSubscription(subId: string, body: SubscriptionCreate) {
    return this.post<ApiResponse<Subscription>>(
      `/subscribers/${encodeURIComponent(subId)}/subscriptions`,
      body,
    );
  }

  /** Sin `plan_id` en el cuerpo, renueva conservando el plan actual. */
  renewSubscription(subId: string, subscriptionId: string, body: SubscriptionRenew = {}) {
    return this.post<ApiResponse<Subscription>>(
      `/subscribers/${encodeURIComponent(subId)}/subscriptions/${encodeURIComponent(subscriptionId)}/renew`,
      body,
    );
  }

  /** Cancelar revoca ademas las sesiones IPTV activas del suscriptor. */
  cancelSubscription(subId: string, subscriptionId: string) {
    return this.post<MessageResponse>(
      `/subscribers/${encodeURIComponent(subId)}/subscriptions/${encodeURIComponent(subscriptionId)}/cancel`,
    );
  }

  // -------------------------------------------------------------------------
  // Dispositivos
  // -------------------------------------------------------------------------

  listDevices(subId: string) {
    return this.get<ApiResponse<Device[]>>(
      `/devices/subscriber/${encodeURIComponent(subId)}`,
    );
  }

  blockDevice(deviceId: string, body: DeviceBlockRequest = {}) {
    return this.post<ApiResponse<Device>>(
      `/devices/${encodeURIComponent(deviceId)}/block`,
      body,
    );
  }

  unblockDevice(deviceId: string) {
    return this.post<ApiResponse<Device>>(`/devices/${encodeURIComponent(deviceId)}/unblock`);
  }

  deleteDevice(deviceId: string) {
    return this.del<MessageResponse>(`/devices/${encodeURIComponent(deviceId)}`);
  }

  // -------------------------------------------------------------------------
  // Canales  (respuesta sin envoltura)
  // -------------------------------------------------------------------------

  listChannels() {
    return this.get<Channel[]>("/channels");
  }

  getChannel(channelId: string) {
    return this.get<Channel>(`/channels/${encodeURIComponent(channelId)}`);
  }

  getChannelStreamStatus(channelId: string) {
    return this.get<StreamStatus>(`/channels/${encodeURIComponent(channelId)}/stream-status`);
  }

  // -------------------------------------------------------------------------
  // Sesiones
  // -------------------------------------------------------------------------

  /** GET {prefix}/sessions/live — array desnudo, maximo 200 filas. */
  listLiveSessions() {
    return this.get<LiveSession[]>("/sessions/live");
  }

  listSubscriberSessions(subId: string) {
    return this.get<ApiResponse<SubscriberSession[]>>(
      `/sessions/subscriber/${encodeURIComponent(subId)}`,
    );
  }

  revokeSubscriberSessions(subId: string) {
    return this.del<MessageResponse>(`/sessions/subscriber/${encodeURIComponent(subId)}`);
  }

  /** Revoca una sesion por su JTI (campo `session_id` de LiveSession). */
  revokeSession(jti: string) {
    return this.del<MessageResponse>(`/sessions/${encodeURIComponent(jti)}`);
  }

  // -------------------------------------------------------------------------
  // Metricas, salud y auditoria  (respuestas sin envoltura)
  // -------------------------------------------------------------------------

  getMetrics() {
    return this.get<SystemMetrics>("/metrics");
  }

  getNodesHealth() {
    return this.get<NodeHealth[]>("/nodes/health");
  }

  getAlerts() {
    return this.get<ActiveAlertsResponse>("/alerts");
  }

  listAudit(query: AuditQuery = {}) {
    return this.get<AuditEntry[]>("/audit", { query });
  }

  // -------------------------------------------------------------------------
  // Escape hatches genericos
  // -------------------------------------------------------------------------

  get<T>(path: string, options: RequestOptions = {}) {
    return this.request<T>(path, { method: "GET" }, options);
  }

  post<T>(path: string, body?: unknown, options: RequestOptions = {}) {
    return this.request<T>(
      path,
      { method: "POST", body: body === undefined ? undefined : JSON.stringify(body) },
      options,
    );
  }

  patch<T>(path: string, body?: unknown, options: RequestOptions = {}) {
    return this.request<T>(
      path,
      { method: "PATCH", body: body === undefined ? undefined : JSON.stringify(body) },
      options,
    );
  }

  put<T>(path: string, body?: unknown, options: RequestOptions = {}) {
    return this.request<T>(
      path,
      { method: "PUT", body: body === undefined ? undefined : JSON.stringify(body) },
      options,
    );
  }

  del<T>(path: string, options: RequestOptions = {}) {
    return this.request<T>(path, { method: "DELETE" }, options);
  }

  // -------------------------------------------------------------------------
  // Interno
  // -------------------------------------------------------------------------

  /** Refresca de forma preventiva si al access token le queda menos que el skew. */
  private async ensureFreshAccessToken() {
    const session = this.store.load();
    if (!session) return;
    const skewMs = this.config.tokenRefreshSkewSeconds * 1000;
    if (Date.now() + skewMs >= session.accessExpiresAt) {
      await this.refresh();
    }
  }

  private failSession(): never {
    this.store.clear();
    this.onUnauthorized?.();
    throw new SessionExpiredError();
  }

  private async request<T>(
    path: string,
    init: RequestInit = {},
    options: RequestOptions = {},
  ): Promise<T> {
    const needsAuth = options.auth ?? true;
    const retryOnUnauthorized = options.retryOnUnauthorized ?? true;

    if (needsAuth) {
      try {
        await this.ensureFreshAccessToken();
      } catch {
        this.failSession();
      }
    }

    const session = this.store.load();
    if (needsAuth && !session) this.failSession();

    const headers = new Headers(init.headers);
    headers.set("Accept", "application/json");
    if (init.body) headers.set("Content-Type", "application/json");
    if (needsAuth && session?.accessToken) {
      headers.set("Authorization", `Bearer ${session.accessToken}`);
    }

    const url = `${this.config.apiBaseUrl}${this.config.apiPrefix}${path}${buildQuery(options.query)}`;
    const response = await fetch(url, {
      ...init,
      headers,
      signal: options.signal,
    });

    if (response.status === 401 && needsAuth) {
      if (!retryOnUnauthorized) this.failSession();
      try {
        await this.refresh();
      } catch {
        this.failSession();
      }
      return this.request<T>(path, init, { ...options, retryOnUnauthorized: false });
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
