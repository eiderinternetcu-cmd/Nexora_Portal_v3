import { useEffect, useState } from "react";

import { messageForError } from "../../api/errors";
import { useApi } from "../../auth/AuthContext";
import { Button, Field, Modal, TextInput, useToast } from "../../ui/primitives";

type Props = {
  open: boolean;
  subId: string;
  username: string;
  onClose: () => void;
};

/**
 * Cambio de contrasena del suscriptor.
 * POST /api/admin/subscribers/{sub_id}/set-password
 *
 * La API NO pide la contrasena actual (SubscriberPasswordChange solo lleva
 * new_password, min 6 max 128): es una accion de administrador, no un cambio
 * hecho por el propio cliente. Por eso se pide repetirla aqui.
 */
export function SuscriptorPasswordModal({ open, subId, username, onClose }: Props) {
  const api = useApi();
  const toast = useToast();

  const [clave, setClave] = useState("");
  const [repetida, setRepetida] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [errorGeneral, setErrorGeneral] = useState<string | null>(null);
  const [guardando, setGuardando] = useState(false);

  useEffect(() => {
    if (!open) return;
    setClave("");
    setRepetida("");
    setError(null);
    setErrorGeneral(null);
    setGuardando(false);
  }, [open]);

  const guardar = async () => {
    if (clave.length < 6 || clave.length > 128) {
      setError("Entre 6 y 128 caracteres.");
      return;
    }
    if (clave !== repetida) {
      setError("Las dos contrasenas no coinciden.");
      return;
    }
    setError(null);
    setGuardando(true);
    setErrorGeneral(null);
    try {
      await api.setSubscriberPassword(subId, clave);
      toast.success(`Contrasena de ${username} actualizada.`);
      onClose();
    } catch (err) {
      setErrorGeneral(messageForError(err));
    } finally {
      setGuardando(false);
    }
  };

  return (
    <Modal
      open={open}
      title={`Cambiar contrasena de ${username}`}
      onClose={onClose}
      size="sm"
      dismissable={!guardando}
      footer={
        <>
          <Button variant="ghost" onClick={onClose} disabled={guardando}>
            Cancelar
          </Button>
          <Button variant="primary" onClick={guardar} loading={guardando}>
            Cambiar
          </Button>
        </>
      }
    >
      <form
        className="susc-form"
        onSubmit={(evento) => {
          evento.preventDefault();
          void guardar();
        }}
      >
        {errorGeneral ? (
          <p className="susc-form-error" role="alert">
            {errorGeneral}
          </p>
        ) : null}
        <p className="susc-aviso">
          El suscriptor tendra que volver a iniciar sesion en sus dispositivos con la
          contrasena nueva.
        </p>
        <Field label="Contrasena nueva" error={error} required>
          {(props) => (
            <TextInput
              {...props}
              type="password"
              value={clave}
              onChange={(evento) => setClave(evento.target.value)}
              autoComplete="new-password"
              maxLength={128}
            />
          )}
        </Field>
        <Field label="Repetir contrasena" required>
          {(props) => (
            <TextInput
              {...props}
              type="password"
              value={repetida}
              onChange={(evento) => setRepetida(evento.target.value)}
              autoComplete="new-password"
              maxLength={128}
            />
          )}
        </Field>
      </form>
    </Modal>
  );
}
