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
    const raw = (error.message || "").toLowerCase();
    if (
      raw.includes("max concurrent") ||
      raw.includes("concurrent") ||
      raw.includes("conexiones") ||
      raw.includes("pantallas") ||
      error.status === 409
    ) {
      return "Límite de pantallas alcanzado: Tu cuenta ya se está reproduciendo en otro dispositivo. Cierra la reproducción en el otro equipo para continuar.";
    }
    if (raw.includes("device_not_registered") || raw.includes("device not registered")) {
      return "Dispositivo no autorizado para esta cuenta.";
    }
    if (error.status === 401) return "Usuario o contraseña incorrectos o sesión vencida.";
    if (error.status === 403) return error.message || "Suscripción no disponible o expirada.";
    if (error.status === 404) return "Canal no disponible temporalmente.";
    if (error.status === 423) return "Cuenta bloqueada temporalmente por seguridad.";
    if (error.status === 429) return "Demasiados intentos seguidos. Espera un momento.";
    if (error.status >= 500) return "Error temporal en el servidor. Intenta de nuevo en unos segundos.";
    return error.message;
  }
  if (error instanceof Error) {
    const msg = error.message.toLowerCase();
    if (msg.includes("max concurrent") || msg.includes("concurrent")) {
      return "Límite de pantallas alcanzado: Tu cuenta ya se está reproduciendo en otro dispositivo. Cierra la reproducción en el otro equipo para continuar.";
    }
    if (msg.includes("failed to fetch") || msg.includes("networkerror") || msg.includes("network request failed")) {
      return "No se pudo conectar con el servidor. Verifica tu conexión a internet.";
    }
    return error.message;
  }
  return "Ocurrió un error inesperado al conectar.";
};
