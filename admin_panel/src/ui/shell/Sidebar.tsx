import { NavLink } from "react-router-dom";

import { useAuth } from "../../auth/AuthContext";
import { visibleSections } from "../../app/sections";

/**
 * Navegacion lateral. Se genera entera desde SECTIONS (src/app/sections.tsx):
 * para anadir un enlace se anade una seccion alli, aqui no se toca nada.
 */
export function Sidebar({ onNavigate }: { onNavigate?: () => void }) {
  const { role } = useAuth();
  const sections = visibleSections(role);

  return (
    <nav className="sidebar" aria-label="Secciones">
      <div className="sidebar-brand">
        <span className="sidebar-brand-mark" aria-hidden />
        <span className="sidebar-brand-text">
          Nexora <strong>Admin</strong>
        </span>
      </div>

      <ul className="sidebar-nav">
        {sections.map((section) => {
          const Icon = section.icon;
          return (
            <li key={section.path}>
              <NavLink
                to={section.path}
                onClick={onNavigate}
                className={({ isActive }) =>
                  isActive ? "sidebar-link sidebar-link-active" : "sidebar-link"
                }
              >
                <Icon size={18} aria-hidden />
                <span>{section.label}</span>
              </NavLink>
            </li>
          );
        })}
      </ul>
    </nav>
  );
}
