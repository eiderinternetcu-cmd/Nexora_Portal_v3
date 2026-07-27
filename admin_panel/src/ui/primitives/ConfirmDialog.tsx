import type { ReactNode } from "react";

import { Button } from "./Button";
import { Modal } from "./Modal";

export type ConfirmDialogProps = {
  open: boolean;
  title: string;
  message: ReactNode;
  confirmLabel?: string;
  cancelLabel?: string;
  /** "danger" para acciones destructivas (borrar, revocar, banear). */
  tone?: "default" | "danger";
  /** Operacion en curso: bloquea los botones y el cierre. */
  busy?: boolean;
  onConfirm: () => void;
  onCancel: () => void;
};

/**
 * Confirmacion de una accion. El estado `open` lo controla la pantalla.
 *
 *   const [target, setTarget] = useState<Subscriber | null>(null);
 *   <ConfirmDialog
 *     open={target !== null}
 *     tone="danger"
 *     title="Eliminar suscriptor"
 *     message={`Se eliminara ${target?.username}. Esta accion no se puede deshacer.`}
 *     busy={deleting}
 *     onCancel={() => setTarget(null)}
 *     onConfirm={handleDelete}
 *   />
 */
export function ConfirmDialog({
  open,
  title,
  message,
  confirmLabel = "Confirmar",
  cancelLabel = "Cancelar",
  tone = "default",
  busy = false,
  onConfirm,
  onCancel,
}: ConfirmDialogProps) {
  return (
    <Modal
      open={open}
      title={title}
      onClose={onCancel}
      size="sm"
      dismissable={!busy}
      footer={
        <>
          <Button variant="ghost" onClick={onCancel} disabled={busy}>
            {cancelLabel}
          </Button>
          <Button
            variant={tone === "danger" ? "danger" : "primary"}
            onClick={onConfirm}
            loading={busy}
          >
            {confirmLabel}
          </Button>
        </>
      }
    >
      <p className="confirm-message">{message}</p>
    </Modal>
  );
}
