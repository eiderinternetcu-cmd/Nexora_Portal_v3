/**
 * Tipos de dominio del panel.
 *
 * FUENTE DE VERDAD: los esquemas Pydantic de `app/schemas/*.py` y los modelos
 * de `app/models/*.py`. Cada bloque indica de donde sale. Si cambias la API,
 * cambia aqui; NO inventes campos.
 *
 * Convenciones de serializacion de FastAPI/Pydantic v2:
 *   - `uuid.UUID`   -> string
 *   - `datetime`    -> string ISO-8601 (con zona)
 *   - `Decimal`     -> string o number segun version; usa `decimalToNumber()`
 *   - campos `X | None` -> `X | null` (presentes en la respuesta, con null)
 */

// ---------------------------------------------------------------------------
// Envolturas comunes  (app/schemas/common.py)
// ---------------------------------------------------------------------------

/** `ApiResponse[T]` — la mayoria de endpoints de escritura y de detalle. */
export type ApiResponse<T> = {
  success: boolean;
  data: T | null;
  message: string | null;
};

/** `PaginatedResponse[T]` — listados de users y subscribers. */
export type PaginatedResponse<T> = {
  success: boolean;
  data: T[];
  total: number;
  page: number;
  page_size: number;
  pages: number;
};

/** `MessageResponse` — endpoints que solo confirman. */
export type MessageResponse = {
  success: boolean;
  message: string;
};

/** `ErrorResponse` — cuerpo de error de la API. */
export type ErrorResponse = {
  success: false;
  error: string;
  detail?: unknown;
};

/**
 * `Decimal` puede llegar como string ("12.50") o como number segun la version
 * de Pydantic/FastAPI. Normaliza siempre antes de formatear.
 */
export const decimalToNumber = (value: string | number | null): number | null => {
  if (value === null) return null;
  const parsed = typeof value === "number" ? value : Number.parseFloat(value);
  return Number.isFinite(parsed) ? parsed : null;
};

// ---------------------------------------------------------------------------
// Auth  (app/schemas/auth.py, app/api/v1/auth.py, app/models/user.py)
// ---------------------------------------------------------------------------

/** `UserRole` de app/models/user.py — SOLO existen estos dos roles. */
export type UserRole = "admin" | "reseller";

/** Cuerpo de POST {prefix}/auth/login — `LoginRequest`. */
export type LoginRequest = {
  username: string;
  password: string;
};

/** Respuesta de login y de refresh — `TokenResponse`. NO trae datos del usuario. */
export type TokenResponse = {
  access_token: string;
  refresh_token: string;
  token_type: string;
  expires_in: number;
};

/** Cuerpo de POST {prefix}/auth/refresh y (opcional) de /auth/logout. */
export type RefreshRequest = {
  refresh_token: string;
};

/**
 * Respuesta de GET {prefix}/auth/me.
 * Ojo: ese endpoint NO declara response_model, devuelve este dict literal
 * (app/api/v1/auth.py:57). Es la unica via para conocer el rol tras el login.
 */
export type AdminMe = {
  id: string;
  username: string;
  email: string;
  role: UserRole;
  is_active: boolean;
};

// ---------------------------------------------------------------------------
// Usuarios del panel  (app/schemas/user.py) — solo rol admin
// ---------------------------------------------------------------------------

export type User = {
  id: string;
  username: string;
  email: string;
  full_name: string | null;
  role: UserRole;
  is_active: boolean;
  notes: string | null;
  created_at: string;
  updated_at: string;
  last_login_at: string | null;
  last_login_ip: string | null;
};

export type UserCreate = {
  username: string;
  email: string;
  /** min 8, max 128 */
  password: string;
  full_name?: string | null;
  role?: UserRole;
  notes?: string | null;
};

export type UserUpdate = {
  email?: string;
  full_name?: string | null;
  role?: UserRole;
  is_active?: boolean;
  notes?: string | null;
};

export type UserPasswordChange = {
  current_password: string;
  /** min 8, max 128 */
  new_password: string;
};

/**
 * `UserPasswordSet` — cuerpo de POST /users/{user_id}/set-password.
 *
 * Reposicion hecha por un admin sobre OTRO usuario: no pide la contrasena
 * actual porque el caso de uso es que el usuario la ha perdido. Mantiene el
 * minimo de 8 caracteres de UserCreate/UserPasswordChange.
 */
export type UserPasswordSet = {
  /** min 8, max 128 */
  new_password: string;
};

// ---------------------------------------------------------------------------
// Suscriptores  (app/schemas/subscriber.py, app/models/subscriber.py)
// ---------------------------------------------------------------------------

/** `SubscriberStatus` de app/models/subscriber.py. */
export type SubscriberStatus = "active" | "expired" | "suspended" | "banned";

export const SUBSCRIBER_STATUSES: readonly SubscriberStatus[] = [
  "active",
  "expired",
  "suspended",
  "banned",
];

export type Subscriber = {
  id: string;
  username: string;
  email: string | null;
  phone: string | null;
  full_name: string | null;
  id_cedula: string | null;
  status: SubscriberStatus;
  notes: string | null;
  created_at: string;
  updated_at: string;
  /**
   * Enriquecimiento de reseller que anade el backend en paralelo. OPCIONALES a
   * proposito (string|number|null|undefined): un panel compilado contra el
   * backend viejo, que aun no los devuelve, no debe romperse. Cuando llegan:
   *   - subscription_expires_at: fin de la suscripcion vigente (ISO) o null.
   *   - plan_name: nombre del plan vigente o null.
   *   - days_remaining: dias que quedan; <= 0 es vencido; null sin suscripcion.
   *   - owner_username: distribuidor (reseller) dueno; solo util para un admin.
   */
  subscription_expires_at?: string | null;
  plan_name?: string | null;
  days_remaining?: number | null;
  owner_username?: string | null;
};

/** `SubscriberOutFull` — el detalle y la creacion anaden estos dos campos. */
export type SubscriberFull = Subscriber & {
  activation_code: string | null;
  created_by: string | null;
};

export type SubscriberCreate = {
  username: string;
  /** min 6, max 128. Alternativa: activation_code. */
  password?: string | null;
  activation_code?: string | null;
  email?: string | null;
  phone?: string | null;
  full_name?: string | null;
  id_cedula?: string | null;
  notes?: string | null;
};

export type SubscriberUpdate = {
  email?: string | null;
  phone?: string | null;
  full_name?: string | null;
  id_cedula?: string | null;
  status?: SubscriberStatus;
  notes?: string | null;
};

/** `SubscriberActiveStatus` (app/schemas/subscription.py) — GET .../{id}/status */
export type SubscriberActiveStatus = {
  subscriber_id: string;
  username: string;
  is_active: boolean;
  subscription_expires_at: string | null;
  max_connections: number;
  max_devices: number;
  device_count: number;
  days_remaining: number | null;
};

// ---------------------------------------------------------------------------
// Planes  (app/schemas/plan.py)
// ---------------------------------------------------------------------------

export type Plan = {
  id: string;
  name: string;
  description: string | null;
  max_connections: number;
  max_devices: number;
  duration_days: number;
  /** Decimal — usa decimalToNumber(). */
  price: string | number | null;
  is_active: boolean;
  notes: string | null;
  created_at: string;
};

export type PlanCreate = {
  name: string;
  description?: string | null;
  /** 1..100 */
  max_connections?: number;
  /** 1..50 */
  max_devices?: number;
  /** >= 1 */
  duration_days?: number;
  price?: string | number | null;
  notes?: string | null;
};

export type PlanUpdate = Partial<PlanCreate> & {
  is_active?: boolean;
};

// ---------------------------------------------------------------------------
// Lista blanca de canales por plan  (app/schemas/plan_channel.py)
// ---------------------------------------------------------------------------

/**
 * `PlanChannelOut` — un canal del catalogo visto desde la lista blanca de un plan.
 *
 * `id` es el ID DEL CANAL, que es justo lo que aceptan los endpoints de
 * escritura (la fila de plan_channels no se expone: se edita la pertenencia).
 *
 * `included` es true solo si existe fila con `is_enabled` — exactamente la
 * condicion que comprueba EntitlementService antes de permitir reproducir.
 *
 * `is_active` es del CANAL, no del plan: incluir un canal inactivo no sirve de
 * nada porque no se emite.
 */
export type PlanChannel = {
  id: string;
  channel_key: string;
  number: number;
  name: string;
  category: string | null;
  logo_url: string | null;
  is_active: boolean;
  included: boolean;
};

/**
 * `PlanChannelsReplace` — cuerpo del PUT: la lista blanca COMPLETA deseada.
 *
 * Una lista vacia es legal y significa "este plan no incluye ningun canal", lo
 * que deniega la reproduccion de TODO a sus suscriptores. No da acceso total.
 */
export type PlanChannelsReplace = {
  channel_ids: string[];
};

/** `PlanChannelsSummary` — lo que cambio de verdad una escritura. */
export type PlanChannelsSummary = {
  plan_id: string;
  /** Canales que NO estaban incluidos y ahora si. */
  added: number;
  /** Filas de plan_channels eliminadas. */
  removed: number;
  /** Canales incluidos por el plan despues de la operacion. */
  total: number;
};

// ---------------------------------------------------------------------------
// Suscripciones  (app/schemas/subscription.py)
// ---------------------------------------------------------------------------

export type Subscription = {
  id: string;
  subscriber_id: string;
  plan_id: string;
  starts_at: string;
  expires_at: string;
  is_active: boolean;
  renewal_note: string | null;
  created_at: string;
  plan: Plan | null;
};

/** `SubscriptionAdminCreate` — el subscriber_id va en la URL, no en el cuerpo. */
export type SubscriptionCreate = {
  plan_id: string;
  renewal_note?: string | null;
};

/** `SubscriptionRenew` — sin plan_id conserva el plan actual. */
export type SubscriptionRenew = {
  plan_id?: string | null;
  renewal_note?: string | null;
};

// ---------------------------------------------------------------------------
// Dispositivos  (app/schemas/device.py)
// ---------------------------------------------------------------------------

export type Device = {
  id: string;
  subscriber_id: string;
  device_id: string;
  mac_address: string | null;
  model: string | null;
  brand: string | null;
  device_type: string | null;
  app_version: string | null;
  os_version: string | null;
  last_ip: string | null;
  status: string;
  is_blocked: boolean;
  block_reason: string | null;
  last_seen_at: string | null;
  registered_at: string;
};

export type DeviceBlockRequest = {
  reason?: string | null;
};

// ---------------------------------------------------------------------------
// Canales  (app/schemas/channel.py)
// ---------------------------------------------------------------------------

/**
 * `ChannelAdminOut` — vista de listado Y de detalle.
 *
 * NO trae los secretos. `stream_key_masked` y `source_url_masked` son formas
 * ENMASCARADAS y no reversibles (ver app/schemas/channel.py): conservan si el
 * campo esta puesto, si la URL es relativa o absoluta, su esquema y si lleva
 * credenciales incrustadas; el host y el path se tiran porque juntos son la URL
 * que se salta el gate de entitlements.
 *
 * Los valores reales solo llegan por `getChannelSecrets()`, que es admin y queda
 * auditado. El sufijo `_masked` es intencionado: si algo vuelve a leer
 * `channel.stream_key` el compilador lo canta en vez de pintar "***".
 */
export type Channel = {
  id: string;
  channel_key: string;
  number: number;
  name: string;
  category: string | null;
  logo_url: string | null;
  stream_key_masked: string;
  source_type: string;
  source_url_masked: string | null;
  epg_id: string | null;
  is_active: boolean;
  requires_subscription: boolean;
  created_at: string;
  updated_at: string;
};

/**
 * `ChannelSecretsOut` — GET {prefix}/channels/{id}/secrets.
 * Solo admin (un reseller recibe 403) y cada llamada queda en `audit_log`.
 * Se pide SOLO tras una accion explicita del usuario, nunca al cargar la vista.
 */
export type ChannelSecrets = {
  channel_id: string;
  channel_key: string;
  stream_key: string;
  source_url: string | null;
};

/** `StreamStatusOut` — GET {prefix}/channels/{id}/stream-status */
export type StreamStatus = {
  channel_key: string;
  alive: boolean;
  client_count: number;
  input_alive: boolean;
  flussonic_configured: boolean;
};

// ---------------------------------------------------------------------------
// Flussonic  (app/api/admin/flussonic.py) — respuestas SIN envoltura
// ---------------------------------------------------------------------------

/** `FlussonicHealthOut` — GET {prefix}/flussonic/health. */
export type FlussonicHealthOut = {
  configured: boolean;
  reachable: boolean;
  /** Solo host:puerto; la API nunca incluye credenciales. */
  base_url_host: string;
};

/** `FlussonicStreamItem` — GET {prefix}/flussonic/streams (503 si no configurado). */
export type FlussonicStreamItem = {
  name: string;
  alive: boolean;
  client_count: number;
  hls_url: string;
};

// ---------------------------------------------------------------------------
// Sesiones  (app/schemas/session.py, app/api/admin/sessions.py)
// ---------------------------------------------------------------------------

/** `SessionOut` — sesiones de un suscriptor concreto. */
export type SubscriberSession = {
  id: string;
  subscriber_id: string;
  device_id: string | null;
  device_fingerprint: string | null;
  ip_address: string | null;
  user_agent: string | null;
  created_at: string;
  expires_at: string;
  last_heartbeat_at: string | null;
  revoked_at: string | null;
  is_active: boolean;
};

/** `LiveSessionOut` — GET {prefix}/sessions/live (maximo 200 filas). */
export type LiveSession = {
  session_id: string;
  subscriber_id: string;
  subscriber_username: string;
  device_id: string | null;
  ip_address: string | null;
  created_at: string;
  expires_at: string;
  last_heartbeat_at: string | null;
};

// ---------------------------------------------------------------------------
// Metricas y salud  (app/api/admin/metrics.py)
// ---------------------------------------------------------------------------

/** `SystemMetrics`. `playback` es un dict libre de contadores acumulados. */
export type SystemMetrics = {
  timestamp: string;
  active_iptv_sessions: number;
  redis_healthy: boolean;
  redis_latency_ms: number;
  postgres_healthy: boolean;
  flussonic_configured: boolean;
  /** null = no comprobado porque Flussonic no esta configurado. */
  flussonic_reachable: boolean | null;
  playback: Record<string, unknown>;
};

/** `NodeHealth` — un elemento por nodo Flussonic configurado. */
export type NodeHealth = {
  node_id: string;
  host: string;
  region: string | null;
  configured: boolean;
  reachable: boolean;
  latency_ms: number | null;
  stream_count: number | null;
};

/** GET {prefix}/alerts devuelve `{ active: [...] }`; el contenido no esta tipado en la API. */
export type ActiveAlertsResponse = {
  active: unknown[];
};

// ---------------------------------------------------------------------------
// Auditoria  (app/api/admin/metrics.py:119, app/models/audit.py)
// ---------------------------------------------------------------------------

/** GET {prefix}/audit devuelve una lista plana de estos objetos (sin envoltura). */
export type AuditEntry = {
  id: string;
  actor_username: string | null;
  action: string;
  target_type: string | null;
  target_id: string | null;
  details: Record<string, unknown> | null;
  ip_address: string | null;
  created_at: string | null;
};

export type AuditQuery = {
  action?: string;
  actor?: string;
  limit?: number;
  offset?: number;
};

// ---------------------------------------------------------------------------
// Parametros de listado
// ---------------------------------------------------------------------------

export type PageQuery = {
  /** >= 1 */
  page?: number;
  /** 1..200, por defecto 50 en la API */
  page_size?: number;
};

export type SubscriberListQuery = PageQuery & {
  status?: SubscriberStatus;
  /**
   * Busqueda libre EN SERVIDOR (max 128): username, full_name, email e
   * id_cedula (SubscriberService.list_subscribers). Recorre toda la base, no
   * solo la pagina cargada.
   */
  q?: string;
};

export type UserListQuery = PageQuery & {
  /** Busqueda libre EN SERVIDOR (max 128): username, full_name y email. */
  q?: string;
  role?: UserRole;
  is_active?: boolean;
};
