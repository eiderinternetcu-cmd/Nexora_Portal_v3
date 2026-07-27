/**
 * Errores tipados de la API. Mismo contrato que web_player/src/api/errors.ts,
 * con mensajes adaptados al panel de administracion.
 *
 * La API devuelve errores en dos formas (app/schemas/common.py y los
 * HTTPException de FastAPI):
 *   { "error": "...", "detail": ... }   -> ErrorResponse
 *   { "detail": "..." }                 -> HTTPException
 *   { "detail": [{ "msg": "...", ...}] } -> error de validacion 422
 */

export class ApiError extends Error {
  readonly status: number;
  readonly payload: unknown;

  constructor(status: number, message: string, payload?: unknown) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.payload = payload;
  }
}

/** Se lanza cuando no hay sesion utilizable (nunca la hubo o el refresh fallo). */
export class SessionExpiredError extends ApiError {
  constructor(message = "Sesion vencida. Inicia sesion otra vez.") {
    super(401, message);
    this.name = "SessionExpiredError";
  }
}

const asRecord = (value: unknown): Record<string, unknown> | null => {
  if (value && typeof value === "object" && !Array.isArray(value)) {
    return value as Record<string, unknown>;
  }
  return null;
};

export const extractApiMessage = (payload: unknown, fallback: string) => {
  const record = asRecord(payload);
  const detail = record?.detail;
  const error = record?.error;

  if (typeof error === "string" && error.trim()) return error;
  if (typeof detail === "string" && detail.trim()) return detail;
  if (Array.isArray(detail) && detail.length > 0) {
    const first = asRecord(detail[0]);
    const msg = first?.msg;
    const loc = first?.loc;
    if (typeof msg === "string") {
      const field = Array.isArray(loc) ? loc[loc.length - 1] : undefined;
      return typeof field === "string" ? `${field}: ${msg}` : msg;
    }
  }
  return fallback;
};

/**
 * Mensaje presentable para el usuario. Uselo en los catch de las pantallas:
 * `toast.error(messageForError(err))`.
 */
export const messageForError = (error: unknown) => {
  if (error instanceof ApiError) {
    if (error.status === 401) return "Sesion vencida. Inicia sesion otra vez.";
    if (error.status === 403) return error.message || "No tienes permiso para esta accion.";
    if (error.status === 404) return error.message || "No se encontro el recurso.";
    if (error.status === 409) return error.message || "Conflicto: el recurso ya existe o esta en uso.";
    if (error.status === 422) return error.message || "Datos invalidos.";
    if (error.status === 423) return "Cuenta bloqueada temporalmente por intentos fallidos.";
    if (error.status === 429) return "Demasiados intentos. Espera un momento.";
    if (error.status >= 500) return "Error del servidor. Intenta de nuevo.";
    return error.message;
  }
  if (error instanceof Error) return error.message;
  return "No se pudo completar la operacion.";
};
