import { BRANDS, FALLBACK_BRAND, brandById, resolveBrand } from "./registry";
import type { Brand, BrandAssets } from "./types";

export type { Brand, BrandAssets };
export { BRANDS, FALLBACK_BRAND, brandById, resolveBrand };

/** Query param que fuerza la marca en desarrollo: ?brand=lared */
const BRAND_QUERY_PARAM = "brand";

const LOCAL_HOSTNAMES = new Set(["localhost", "127.0.0.1", "0.0.0.0", "[::1]", "::1", ""]);

/** El override manual solo se acepta en desarrollo / hosts locales. */
function overridesAllowed(hostname: string): boolean {
  if (import.meta.env.DEV) return true;
  const normalized = hostname.toLowerCase();
  return LOCAL_HOSTNAMES.has(normalized) || normalized.endsWith(".localhost");
}

/**
 * Precedencia: ?brand= > VITE_BRAND > hostname > fallback (nexora).
 * Los dos primeros solo se aplican en desarrollo; en produccion manda el host.
 */
export function resolveActiveBrand(location: Location): Brand {
  const hostname = location.hostname ?? "";
  if (overridesAllowed(hostname)) {
    const fromQuery = brandById(new URLSearchParams(location.search).get(BRAND_QUERY_PARAM));
    if (fromQuery) return fromQuery;
    const fromEnv = brandById(import.meta.env.VITE_BRAND);
    if (fromEnv) return fromEnv;
  }
  return resolveBrand(hostname);
}

function setMeta(name: string, content: string): void {
  let tag = document.head.querySelector<HTMLMetaElement>(`meta[name="${name}"]`);
  if (!tag) {
    tag = document.createElement("meta");
    tag.name = name;
    document.head.appendChild(tag);
  }
  tag.content = content;
}

function setIcon(href: string): void {
  let link = document.head.querySelector<HTMLLinkElement>('link[rel="icon"]');
  if (!link) {
    link = document.createElement("link");
    link.rel = "icon";
    document.head.appendChild(link);
  }
  link.href = href;
}

/**
 * Aplica la marca al documento. Debe llamarse ANTES del primer render para que
 * no haya parpadeo de la marca equivocada: los tokens de color viven en
 * `:root[data-brand="..."]` dentro de app.css.
 */
export function applyBrand(brand: Brand): void {
  document.documentElement.dataset.brand = brand.id;
  document.title = brand.documentTitle;
  setMeta("theme-color", brand.themeColor);
  setIcon(brand.assets.icon);
}

/** Marca activa de esta sesion. Los componentes leen de aqui. */
export const brand: Brand = resolveActiveBrand(window.location);
