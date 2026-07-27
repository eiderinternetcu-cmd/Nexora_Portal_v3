import { useLocation } from "react-router-dom";
import { Hammer } from "lucide-react";

import { SECTIONS } from "./sections";

/**
 * Marcador de posicion de una seccion todavia no construida.
 *
 * NO lo edites para "arreglar" una seccion: escribe tu pantalla en
 * src/features/<seccion>/ y cambia el `component` de tu entrada en
 * src/app/sections.tsx (ahi estan las instrucciones completas).
 */
export function SectionPlaceholder() {
  const { pathname } = useLocation();
  const section = SECTIONS.find((item) => item.path === pathname);

  return (
    <div className="placeholder">
      <Hammer size={28} aria-hidden />
      <h2>{section?.label ?? "Seccion"}</h2>
      <p>
        Esta seccion aun no esta implementada. Registrala cambiando su
        <code> component </code> en <code>src/app/sections.tsx</code>.
      </p>
      <p className="placeholder-path">{pathname}</p>
    </div>
  );
}
