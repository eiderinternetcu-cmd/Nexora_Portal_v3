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
    if (typeof msg === "string") return msg;
  }
  return fallback;
};

export const messageForError = (error: unknown) => {
  if (error instanceof ApiError) {
    if (error.status === 401) return "Sesion vencida. Inicia sesion otra vez.";
    if (error.status === 403) return error.message || "Suscripcion no disponible.";
    if (error.status === 409) return error.message || "Limite de conexiones alcanzado.";
    if (error.status === 423) return "Cuenta bloqueada temporalmente por intentos fallidos.";
    if (error.status === 429) return "Demasiados intentos. Espera un momento.";
    return error.message;
  }
  if (error instanceof Error) return error.message;
  return "No se pudo completar la operacion.";
};
