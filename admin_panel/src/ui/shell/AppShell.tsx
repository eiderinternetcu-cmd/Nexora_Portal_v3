import { useEffect, useState } from "react";
import { Outlet, matchPath, useLocation } from "react-router-dom";

import { SECTIONS } from "../../app/sections";
import { Sidebar } from "./Sidebar";
import { Topbar } from "./Topbar";

/**
 * Layout de la aplicacion autenticada: barra lateral + cabecera + <Outlet>.
 * El titulo sale del `label` de la seccion cuya ruta casa con la URL actual
 * (incluidas subrutas con parametros).
 */
export function AppShell() {
  const location = useLocation();
  const [navOpen, setNavOpen] = useState(false);

  const active = SECTIONS.find((section) =>
    matchPath({ path: section.path, end: true }, location.pathname),
  );

  // Al navegar, cierra la barra lateral en movil.
  useEffect(() => {
    setNavOpen(false);
  }, [location.pathname]);

  return (
    <div className={`shell${navOpen ? " shell-nav-open" : ""}`}>
      <aside className="shell-aside">
        <Sidebar onNavigate={() => setNavOpen(false)} />
      </aside>

      {navOpen ? (
        <div className="shell-scrim" onClick={() => setNavOpen(false)} role="presentation" />
      ) : null}

      <div className="shell-main">
        <Topbar
          title={active?.label ?? "Nexora Admin"}
          onToggleNav={() => setNavOpen((open) => !open)}
        />
        <main className="shell-content">
          <Outlet />
        </main>
      </div>
    </div>
  );
}
