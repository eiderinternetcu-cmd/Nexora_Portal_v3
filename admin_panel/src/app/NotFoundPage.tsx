import { Link } from "react-router-dom";
import { SearchX } from "lucide-react";

import { HOME_PATH } from "./sections";

export function NotFoundPage() {
  return (
    <div className="placeholder">
      <SearchX size={28} aria-hidden />
      <h2>Pagina no encontrada</h2>
      <p>La ruta que buscas no existe en el panel.</p>
      <Link className="btn btn-primary btn-md" to={HOME_PATH}>
        <span>Ir al dashboard</span>
      </Link>
    </div>
  );
}
