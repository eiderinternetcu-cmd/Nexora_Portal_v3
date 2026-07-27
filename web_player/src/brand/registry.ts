import type { Brand, BrandAssets } from "./types";

/**
 * Los 5 assets viven en /assets/<id>/ con nombres identicos para todas las
 * marcas, asi que se derivan del id: anadir una marca = anadir una entrada
 * a BRANDS + soltar los 5 ficheros en public/assets/<id>/.
 */
const assetsFor = (id: string): BrandAssets => ({
  icon: `/assets/${id}/icon.png`,
  logo: `/assets/${id}/logo.png`,
  playerLogo: `/assets/${id}/player-logo.png`,
  background: `/assets/${id}/bg.png`,
  playerBackground: `/assets/${id}/player-bg.jpg`,
});

const NEXORA: Brand = {
  id: "nexora",
  hostnames: ["nexoraplay.net", "www.nexoraplay.net"],
  name: "NEXORA",
  tagline: "TU UNIVERSO. TU ENTRETENIMIENTO.",
  displayDomain: "NEXORA.IO",
  legal: "(C) 2026 Nexora - Todos los derechos reservados",
  documentTitle: "Nexora Web Player",
  themeColor: "#04030e",
  assets: assetsFor("nexora"),
};

const LARED: Brand = {
  id: "lared",
  hostnames: ["tvdigital.laredtelco.com"],
  name: "LA RED",
  tagline: "INTERNET DE FIBRA OPTICA Y TV",
  displayDomain: "LARED.EC",
  legal: "(C) 2026 WRIVERA LA RED Telecomunicaciones S.A. - Todos los derechos reservados",
  documentTitle: "La Red Web Player",
  themeColor: "#ff6600",
  assets: assetsFor("lared"),
};

/** Registro de marcas. Anadir una marca = anadir UNA entrada aqui. */
export const BRANDS: readonly Brand[] = [NEXORA, LARED];

/**
 * Marca por defecto. Nexora es la marca en produccion: cualquier host
 * desconocido (IP cruda, localhost, dominio nuevo sin registrar) debe verse
 * como Nexora para que ningun acceso existente cambie de aspecto.
 */
export const FALLBACK_BRAND: Brand = NEXORA;

const BY_ID = new Map(BRANDS.map((brand) => [brand.id, brand]));

const BY_HOSTNAME = new Map(
  BRANDS.flatMap((brand) => brand.hostnames.map((hostname) => [hostname.toLowerCase(), brand] as const)),
);

/** Devuelve la marca de un id, o undefined si no existe. */
export function brandById(id: string | null | undefined): Brand | undefined {
  return id ? BY_ID.get(id.trim().toLowerCase()) : undefined;
}

/**
 * Resuelve la marca a partir del hostname. Sin excepciones ni error visible:
 * un hostname desconocido siempre cae en FALLBACK_BRAND.
 */
export function resolveBrand(hostname: string | null | undefined): Brand {
  if (!hostname) return FALLBACK_BRAND;
  const normalized = hostname.trim().toLowerCase().replace(/\.$/, "").replace(/:\d+$/, "");
  return BY_HOSTNAME.get(normalized) ?? FALLBACK_BRAND;
}
