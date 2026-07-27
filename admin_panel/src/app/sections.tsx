import type { ComponentType } from "react";
import {
  Activity,
  LayoutDashboard,
  MonitorSmartphone,
  Package,
  Radio,
  ScrollText,
  ShieldCheck,
  Users,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";

import type { UserRole } from "../api/types";
import { SectionPlaceholder } from "./SectionPlaceholder";
import { PlanesView } from "../features/planes/PlanesView";
import { UsuariosView } from "../features/usuarios/UsuariosView";
import { SuscriptoresView } from "../features/suscriptores/SuscriptoresView";
import { SuscriptorDetalleView } from "../features/suscriptores/SuscriptorDetalleView";
import { DispositivosView } from "../features/dispositivos/DispositivosView";

/**
 * =========================================================================
 *  REGISTRO DE SECCIONES  --  UNICO SITIO DONDE SE DA DE ALTA UNA PANTALLA
 * =========================================================================
 *
 * COMO REGISTRAR UNA SECCION NUEVA (o reemplazar un placeholder):
 *
 *  1. Escribe tu pantalla en `src/features/<tu-seccion>/<TuPantalla>.tsx`.
 *     Debe ser un componente sin props:  export function PlanesView() { ... }
 *     No metas pantallas en src/app/, src/ui/shell/ ni src/ui/primitives/:
 *     esas carpetas son de los cimientos y las tocan otros.
 *
 *  2. Importala arriba en ESTE archivo:
 *       import { PlanesView } from "../features/planes/PlanesView";
 *
 *  3. Cambia el `component` de tu entrada en SECTIONS por tu componente:
 *       component: PlanesView,
 *     Deja `path`, `label` e `icon` como estan salvo que haga falta.
 *
 *  4. Ya esta. La ruta, el enlace de la barra lateral y el guard de sesion
 *     salen solos de esta lista; no hay que tocar App.tsx ni Sidebar.tsx.
 *
 * SUBRUTAS (detalle de un recurso, p.ej. /suscriptores/:id):
 *     Anade una entrada con `hidden: true` para que no salga en la barra:
 *       { path: "/suscriptores/:id", label: "Detalle de suscriptor",
 *         icon: Users, component: SubscriberDetailView, hidden: true }
 *     Ponla DESPUES de la ruta padre en el array.
 *
 * PERMISOS:
 *     `roles` limita quien ve el enlace y quien puede entrar. Si lo omites,
 *     la seccion es visible para admin y reseller. Esto es solo ergonomia de
 *     UI: la autorizacion real la hace la API (require_admin /
 *     require_admin_or_reseller en app/core/dependencies.py). Nunca asumas
 *     que ocultar un enlace protege un dato.
 *
 * CONFLICTOS: varios agentes editan este archivo. Toca SOLO la linea
 * `component:` de tu seccion y su import. No reordenes el array.
 */

export type SectionRoute = {
  /** Ruta absoluta. Puede llevar parametros (":id"). */
  path: string;
  /** Texto del enlace en la barra lateral y titulo por defecto de la pagina. */
  label: string;
  icon: LucideIcon;
  /** Componente de pantalla, sin props. */
  component: ComponentType;
  /** Roles que pueden verla. Sin definir = todos los roles autenticados. */
  roles?: UserRole[];
  /** No aparece en la barra lateral (subrutas, detalles). */
  hidden?: boolean;
};

export const SECTIONS: SectionRoute[] = [
  {
    path: "/dashboard",
    label: "Dashboard",
    icon: LayoutDashboard,
    component: SectionPlaceholder,
  },
  {
    path: "/suscriptores",
    label: "Suscriptores",
    icon: Users,
    component: SuscriptoresView,
  },
  {
    // Subruta: sin ella, pulsar una fila del listado cae en el 404.
    path: "/suscriptores/:id",
    label: "Detalle de suscriptor",
    icon: Users,
    component: SuscriptorDetalleView,
    hidden: true,
  },
  {
    path: "/planes",
    label: "Planes",
    icon: Package,
    component: PlanesView,
  },
  {
    path: "/dispositivos",
    label: "Dispositivos",
    icon: MonitorSmartphone,
    component: DispositivosView,
  },
  {
    // La API exige rol admin para /users (require_admin en app/api/v1/users.py).
    path: "/usuarios",
    label: "Usuarios",
    icon: ShieldCheck,
    component: UsuariosView,
    roles: ["admin"],
  },
  {
    path: "/canales",
    label: "Canales",
    icon: Radio,
    component: SectionPlaceholder,
  },
  {
    path: "/sesiones",
    label: "Sesiones",
    icon: Activity,
    component: SectionPlaceholder,
  },
  {
    path: "/auditoria",
    label: "Auditoria",
    icon: ScrollText,
    component: SectionPlaceholder,
  },
];

/** Ruta a la que se entra tras el login. */
export const HOME_PATH = "/dashboard";

/** Ruta del login. */
export const LOGIN_PATH = "/login";

export const canAccessSection = (section: SectionRoute, role: UserRole | null) =>
  !section.roles || (role !== null && section.roles.includes(role));

export const visibleSections = (role: UserRole | null) =>
  SECTIONS.filter((section) => !section.hidden && canAccessSection(section, role));
