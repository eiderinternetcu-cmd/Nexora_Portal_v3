import { Navigate, Outlet, useLocation } from "react-router-dom";
import { Loader2 } from "lucide-react";

import { useAuth } from "../../auth/AuthContext";
import { LOGIN_PATH } from "../../app/sections";

/**
 * Guard de sesion. Envuelve todas las rutas del shell:
 *   - "loading": pinta un splash mientras se valida /auth/me.
 *   - "anon":    redirige a /login guardando la ruta pretendida en state.from,
 *                para volver a ella tras entrar.
 *   - "authed":  renderiza el <Outlet>.
 */
export function RequireAuth() {
  const { status } = useAuth();
  const location = useLocation();

  if (status === "loading") {
    return (
      <div className="boot-splash" aria-live="polite">
        <Loader2 size={24} className="spin" aria-hidden />
        <span>Cargando sesion...</span>
      </div>
    );
  }

  if (status === "anon") {
    return <Navigate to={LOGIN_PATH} replace state={{ from: location.pathname }} />;
  }

  return <Outlet />;
}
