import { useEffect } from "react";
import type { ReactNode } from "react";
import { X } from "lucide-react";

export type ModalProps = {
  open: boolean;
  title: ReactNode;
  /** Se llama al pulsar Escape, el fondo o la X. */
  onClose: () => void;
  children: ReactNode;
  /** Zona de acciones al pie (botones). */
  footer?: ReactNode;
  size?: "sm" | "md" | "lg";
  /** Desactiva cerrar por Escape/fondo mientras hay una operacion en curso. */
  dismissable?: boolean;
};

/**
 * Modal base. Para confirmar una accion destructiva usa <ConfirmDialog>, que
 * ya monta este con la ergonomia correcta.
 */
export function Modal({
  open,
  title,
  onClose,
  children,
  footer,
  size = "md",
  dismissable = true,
}: ModalProps) {
  useEffect(() => {
    if (!open || !dismissable) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [open, dismissable, onClose]);

  if (!open) return null;

  return (
    <div
      className="modal-backdrop"
      onClick={dismissable ? onClose : undefined}
      role="presentation"
    >
      <div
        className={`modal modal-${size}`}
        role="dialog"
        aria-modal="true"
        aria-label={typeof title === "string" ? title : undefined}
        onClick={(event) => event.stopPropagation()}
      >
        <header className="modal-header">
          <h2 className="modal-title">{title}</h2>
          <button
            type="button"
            className="modal-close"
            onClick={onClose}
            aria-label="Cerrar"
            disabled={!dismissable}
          >
            <X size={18} aria-hidden />
          </button>
        </header>
        <div className="modal-body">{children}</div>
        {footer ? <footer className="modal-footer">{footer}</footer> : null}
      </div>
    </div>
  );
}
