/**
 * Contrato de marca. Un unico build sirve varias marcas: cada marca describe
 * aqui todo lo que cambia entre despliegues (textos, assets, metadatos).
 * Los identificadores internos (NexoraClient, VITE_NEXORA_*, claves
 * `nexora.web_player.*`) NO forman parte de este contrato y no cambian.
 */
export type BrandAssets = {
  /** Favicon + isotipo de la cabecera de login. */
  icon: string;
  /** Logotipo horizontal. */
  logo: string;
  /** Marca usada dentro del player y del launcher. */
  playerLogo: string;
  /** Fondo general de la app (consumido hoy desde app.css). */
  background: string;
  /** Fondo de la vista del player (consumido hoy desde app.css). */
  playerBackground: string;
};

export type Brand = {
  /** Identificador estable; tambien es la carpeta en /assets/<id>/ y el valor de <html data-brand>. */
  id: string;
  /** Hostnames que resuelven a esta marca (sin puerto, en minusculas). */
  hostnames: readonly string[];
  /** Nombre visible en mayusculas. */
  name: string;
  /** Lema bajo el nombre. */
  tagline: string;
  /** Dominio mostrado en el pie de pagina. */
  displayDomain: string;
  /** Linea legal del pie de pagina. */
  legal: string;
  /** <title> del documento. */
  documentTitle: string;
  /** <meta name="theme-color">. */
  themeColor: string;
  /**
   * Pinta el logotipo real en la portada en vez del nombre en tipografia.
   * Opcional a proposito: solo debe activarse si assets.logo tiene resolucion
   * suficiente para verse a ~330px de ancho. El logotipo de Nexora es de
   * 158x50, asi que ahi se sigue componiendo el nombre con tipografia.
   */
  useLogoAsWordmark?: boolean;
  assets: BrandAssets;
};
