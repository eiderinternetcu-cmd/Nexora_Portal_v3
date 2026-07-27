/**
 * Formateo de fechas para las pantallas de suscriptores.
 *
 * La API serializa datetime como ISO-8601 con zona; aqui solo se presenta.
 * No hay logica de negocio: si la cadena no parsea, se devuelve el marcador
 * de vacio en vez de "Invalid Date".
 */

const FECHA = new Intl.DateTimeFormat("es-ES", { dateStyle: "medium" });

const FECHA_HORA = new Intl.DateTimeFormat("es-ES", {
  dateStyle: "medium",
  timeStyle: "short",
});

/** Marcador visual para un dato ausente. */
export const VACIO = "--";

const parsear = (iso: string | null | undefined): Date | null => {
  if (!iso) return null;
  const fecha = new Date(iso);
  return Number.isNaN(fecha.getTime()) ? null : fecha;
};

export const formatFecha = (iso: string | null | undefined) => {
  const fecha = parsear(iso);
  return fecha ? FECHA.format(fecha) : VACIO;
};

export const formatFechaHora = (iso: string | null | undefined) => {
  const fecha = parsear(iso);
  return fecha ? FECHA_HORA.format(fecha) : VACIO;
};

/** Texto de un campo opcional; evita pintar celdas en blanco. */
export const textoOpcional = (valor: string | null | undefined) => {
  const limpio = valor?.trim();
  return limpio ? limpio : VACIO;
};

/**
 * Convierte el valor de un input a lo que espera la API: los opcionales viajan
 * como null, nunca como cadena vacia (un "" en un EmailStr da 422).
 */
export const nuloSiVacio = (valor: string): string | null => {
  const limpio = valor.trim();
  return limpio ? limpio : null;
};
