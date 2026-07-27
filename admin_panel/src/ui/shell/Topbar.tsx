import { useState } from "react";
import { LogOut, Menu } from "lucide-react";

import { useAuth } from "../../auth/AuthContext";
import { Button } from "../primitives/Button";
import { ConfirmDialog } from "../primitives/ConfirmDialog";

/** Cabecera: titulo de la seccion actual, usuario con su rol y salir. */
export function Topbar({ title, onToggleNav }: { title: string; onToggleNav: () => void }) {
  const { user, logout } = useAuth();
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);

  const handleLogout = async () => {
    setBusy(true);
    try {
      await logout();
    } finally {
      setBusy(false);
      setConfirming(false);
    }
  };

  return (
    <header className="topbar">
      <button
        type="button"
        className="topbar-burger"
        onClick={onToggleNav}
        aria-label="Alternar navegacion"
      >
        <Menu size={20} aria-hidden />
      </button>

      <h1 className="topbar-title">{title}</h1>

      <div className="topbar-user">
        <div className="topbar-identity">
          <span className="topbar-username">{user?.username ?? "-"}</span>
          <span className="topbar-role">{user?.role ?? ""}</span>
        </div>
        <Button
          variant="ghost"
          size="sm"
          icon={<LogOut size={16} />}
          onClick={() => setConfirming(true)}
        >
          Salir
        </Button>
      </div>

      <ConfirmDialog
        open={confirming}
        title="Cerrar sesion"
        message="Se cerrara tu sesion en el panel."
        confirmLabel="Salir"
        busy={busy}
        onCancel={() => setConfirming(false)}
        onConfirm={handleLogout}
      />
    </header>
  );
}
