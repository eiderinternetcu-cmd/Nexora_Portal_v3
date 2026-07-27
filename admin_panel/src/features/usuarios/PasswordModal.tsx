import { useEffect, useState } from "react";

import type { UserPasswordChange } from "../../api/types";
import { Button, Field, Modal, TextInput } from "../../ui/primitives";

export type PasswordModalProps = {
  open: boolean;
  /** Usuario afectado; siempre es el de la sesion (ver nota de abajo). */
  username: string;
  saving: boolean;
  onSubmit: (body: UserPasswordChange) => void;
  onClose: () => void;
};

/**
 * Cambio de la propia contrasena.
 * POST /users/me/change-password: exige la contrasena actual y actua sobre el
 * usuario de la sesion.
 *
 * Para reponer la de OTRO usuario esta UserPasswordResetModal, que usa
 * POST /users/{user_id}/set-password y no pide la actual.
 */
export function PasswordModal({ open, username, saving, onSubmit, onClose }: PasswordModalProps) {
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [repeat, setRepeat] = useState("");
  const [errors, setErrors] = useState<{ current?: string; next?: string; repeat?: string }>({});

  useEffect(() => {
    if (!open) return;
    setCurrent("");
    setNext("");
    setRepeat("");
    setErrors({});
  }, [open]);

  const handleSubmit = () => {
    const found: { current?: string; next?: string; repeat?: string } = {};
    if (current === "") found.current = "Escribe tu contrasena actual.";
    if (next.length < 8 || next.length > 128) found.next = "Entre 8 y 128 caracteres.";
    if (repeat !== next) found.repeat = "Las contrasenas no coinciden.";

    setErrors(found);
    if (Object.keys(found).length > 0) return;

    onSubmit({ current_password: current, new_password: next });
  };

  return (
    <Modal
      open={open}
      title="Cambiar mi contrasena"
      onClose={onClose}
      size="sm"
      dismissable={!saving}
      footer={
        <>
          <Button variant="ghost" onClick={onClose} disabled={saving}>
            Cancelar
          </Button>
          <Button variant="primary" onClick={handleSubmit} loading={saving}>
            Cambiar
          </Button>
        </>
      }
    >
      <form
        onSubmit={(event) => {
          event.preventDefault();
          handleSubmit();
        }}
      >
        <p className="usuarios-note">
          Cambiaras la contrasena de <strong>{username}</strong>, la cuenta con la que
          has iniciado sesion.
        </p>

        <Field label="Contrasena actual" error={errors.current} required>
          {(props) => (
            <TextInput
              {...props}
              type="password"
              value={current}
              autoComplete="current-password"
              onChange={(event) => setCurrent(event.target.value)}
            />
          )}
        </Field>

        <Field label="Nueva contrasena" error={errors.next} hint="Minimo 8 caracteres." required>
          {(props) => (
            <TextInput
              {...props}
              type="password"
              value={next}
              autoComplete="new-password"
              onChange={(event) => setNext(event.target.value)}
            />
          )}
        </Field>

        <Field label="Repite la nueva contrasena" error={errors.repeat} required>
          {(props) => (
            <TextInput
              {...props}
              type="password"
              value={repeat}
              autoComplete="new-password"
              onChange={(event) => setRepeat(event.target.value)}
            />
          )}
        </Field>

        {/* Permite enviar con Enter sin duplicar el boton visible del pie. */}
        <button type="submit" className="sr-only" tabIndex={-1} aria-hidden>
          Cambiar
        </button>
      </form>
    </Modal>
  );
}
