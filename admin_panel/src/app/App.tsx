import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";

import { AuthProvider, useAuth } from "../auth/AuthContext";
import { ToastProvider } from "../ui/primitives";
import { AppShell } from "../ui/shell/AppShell";
import { RequireAuth } from "../ui/shell/RequireAuth";
import { LoginPage } from "./LoginPage";
import { NotFoundPage } from "./NotFoundPage";
import { HOME_PATH, LOGIN_PATH, SECTIONS, canAccessSection } from "./sections";

/**
 * Arbol de rutas.
 *
 * NO anadas <Route> aqui: las secciones se generan desde SECTIONS
 * (src/app/sections.tsx), que es el unico punto de registro. Alli estan las
 * instrucciones para dar de alta una pantalla.
 *
 * Jerarquia:
 *   /login                         publico
 *   /        RequireAuth+AppShell  una ruta por cada entrada de SECTIONS
 *   *                              404 dentro del shell
 *
 * Una seccion cuyo `roles` no incluye el rol del usuario existe igualmente
 * como ruta, pero redirige al dashboard: asi una URL pegada a mano no rompe.
 */
function AppRoutes() {
  const { role } = useAuth();

  return (
    <Routes>
      <Route path={LOGIN_PATH} element={<LoginPage />} />
      <Route element={<RequireAuth />}>
        <Route element={<AppShell />}>
          <Route index element={<Navigate to={HOME_PATH} replace />} />
          {SECTIONS.map((section) => {
            const Component = section.component;
            return (
              <Route
                key={section.path}
                path={section.path}
                element={
                  canAccessSection(section, role) ? (
                    <Component />
                  ) : (
                    <Navigate to={HOME_PATH} replace />
                  )
                }
              />
            );
          })}
          <Route path="*" element={<NotFoundPage />} />
        </Route>
      </Route>
    </Routes>
  );
}

export function App() {
  return (
    <BrowserRouter>
      <AuthProvider>
        <ToastProvider>
          <AppRoutes />
        </ToastProvider>
      </AuthProvider>
    </BrowserRouter>
  );
}
