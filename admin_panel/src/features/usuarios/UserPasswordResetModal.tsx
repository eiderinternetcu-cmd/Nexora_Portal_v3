import { useEffect, useState } from "react";

import { messageForError } from "../../api/errors";
import type { User } from "../../api/types";
import { useApi } from "../../auth/AuthContext";
import { Button, ConfirmDialog, Field, Modal, TextInput, useToast } from "../../ui/primitives";

export type UserPasswordResetModalProps = {
  open: boolean;
  /** Usuario ajeno al que se le repone la clave. Null mientras esta cerrado. */
  user: User | null;
  onClose: () => void;
};

/**
 * Reposicion de la contrasena de OTRO usuario del panel.
 * POST /api/admin/users/{user_id}/set-password  (require_admin, minimo 8)
 *
 * No pide la contrasena actual a proposito: el caso de uso es justamente que el
 * usuario la ha perdido. Para cambiar la propia esta PasswordModal, que si la
 * exige (POST /users/me/change-password).
 *
 * Se confirma antes de escribir porque la clave anterior deja de servir en el
 * acto: si el afectado es otro admin, se queda fuera hasta que le pasen la nueva.
 */
export function UserPasswordResetModal({ open, user, onClose }: UserPasswordResetModalProps) {
  const api = useApi();
  const toast = useToast();

  const [clave, setClave] = useState("");
  const [repetida, setRepetida] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [errorGeneral, setErrorGeneral] = useState<string | null>(null);
  const [guardando, setGuardando] = useState(false);
  const [confirmando, setConfirmando] = useState(false);

  useEffect(() => {
    if (!open) return;
    setClave("");
    setRepetida("");
    setError(null);
    setErrorGeneral(null);
    setGuardando(false);
    setConfirmando(false);
  }, [open]);

  const validar = () => {
    if (clave.length < 8 || clave.length > 128) {
      setError("Entre 8 y 128 caracteres.");
      return false;
    }
    if (clave !== repetida) {
      setError("Las dos contrasenas no coinciden.");
      return false;
    }
    setError(null);
    return true;
  };

  const guardar = async () => {
    if (!user) return;
    setGuardando(true);
    setErrorGeneral(null);
    try {
      await api.setUserPassword(user.id, clave);
      toast.success(`Contrasena de "${user.username}" repuesta.`);
      setConfirmando(false);
      onClose();
    } catch (err) {
      setConfirmando(false);
      setErrorGeneral(messageForError(err));
    } finally {
      setGuardando(false);
    }
  };

  const cerrar = () => {
    if (guardando) return;
    onClose();
  };

  return (
    <>
      <Modal
        open={open}
        title={user ? `Reponer contrasena de ${user.username}` : "Reponer contrasena"}
        onClose={cerrar}
        size="sm"
        dismissable={!guardando}
        footer={
          <>
            <Button variant="ghost" onClick={cerrar} disabled={guardando}>
              Cancelar
            </Button>
            <Button
              variant="primary"
              loading={guardando}
              onClick={() => {
                if (validar()) setConfirmando(true);
              }}
            >
              Reponer
            </Button>
          </>
        }
      >
        <form
          onSubmit={(evento) => {
            evento.preventDefault();
            if (validar()) setConfirmando(true);
          }}
        >
          {errorGeneral ? (
            <p className="usuarios-form-error" role="alert">
              {errorGeneral}
            </p>
          ) : null}

          <p className="usuarios-note">
            Le pondras una contrasena nueva a <strong>{user?.username ?? ""}</strong> (
            {user?.role ?? ""}) sin conocer la actual. La anterior dejara de funcionar de
            inmediato, asi que tendras que comunicarle la nueva.
          </p>

          <Field label="Contrasena nueva" error={error} hint="Minimo 8 caracteres." required>
            {(props) => (
              <TextInput
                {...props}
                type="password"
                value={clave}
                autoComplete="new-password"
                maxLength={128}
                onChange={(evento) => setClave(evento.target.value)}
              />
            )}
          </Field>

          <Field label="Repite la contrasena nueva" required>
            {(props) => (
              <TextInput
                {...props}
                type="password"
                value={repetida}
                autoComplete="new-password"
                maxLength={128}
                onChange={(evento) => setRepetida(evento.target.value)}
              />
            )}
          </Field>

          {/* Permite enviar con Enter sin duplicar el boton visible del pie. */}
          <button type="submit" className="sr-only" tabIndex={-1} aria-hidden>
            Reponer
          </button>
        </form>
      </Modal>

      <ConfirmDialog
        open={confirmando}
        tone="danger"
        title="Reponer contrasena"
        message={
          <>
            Se cambiara la contrasena de <strong>{user?.username ?? ""}</strong> (
            {user?.role ?? ""}). La que tuviera dejara de servir al instante y no podra
            entrar al panel hasta que le des la nueva.
          </>
        }
        confirmLabel="Reponer"
        busy={guardando}
        onCancel={() => {
          if (!guardando) setConfirmando(false);
        }}
        onConfirm={() => void guardar()}
      />
    </>
  );
}
