import { useEffect, useState } from "react";

import { messageForError } from "../../api/errors";
import { SUBSCRIBER_STATUSES } from "../../api/types";
import type {
  Subscriber,
  SubscriberCreate,
  SubscriberStatus,
  SubscriberUpdate,
} from "../../api/types";
import { useApi } from "../../auth/AuthContext";
import {
  Button,
  Field,
  Modal,
  Select,
  TextArea,
  TextInput,
  useToast,
} from "../../ui/primitives";
import { nuloSiVacio } from "./formato";

type Props = {
  open: boolean;
  /** null = alta; con valor = edicion de ese suscriptor. */
  suscriptor: Subscriber | null;
  onClose: () => void;
  /** Se llama tras guardar con exito para que la pantalla recargue. */
  onGuardado: (suscriptor: Subscriber) => void;
};

type Errores = Partial<Record<string, string>>;

const VACIO = {
  username: "",
  password: "",
  activation_code: "",
  email: "",
  phone: "",
  full_name: "",
  id_cedula: "",
  notes: "",
};

// Mismo patron que SubscriberCreate.username en app/schemas/subscriber.py.
const PATRON_USUARIO = /^[a-zA-Z0-9_.\-]+$/;

/**
 * Alta y edicion de suscriptor.
 *
 * POST   /api/admin/subscribers          (alta)
 * PATCH  /api/admin/subscribers/{sub_id} (edicion)
 *
 * En el alta la API exige `password` O `activation_code` (uno de los dos); si
 * se manda un activation_code vacio, el servidor genera uno. En la edicion el
 * username no se puede cambiar: el schema SubscriberUpdate no lo acepta.
 */
export function SuscriptorFormModal({ open, suscriptor, onClose, onGuardado }: Props) {
  const api = useApi();
  const toast = useToast();
  const esAlta = suscriptor === null;

  const [valores, setValores] = useState(VACIO);
  const [estado, setEstado] = useState<SubscriberStatus>("active");
  const [errores, setErrores] = useState<Errores>({});
  const [errorGeneral, setErrorGeneral] = useState<string | null>(null);
  const [guardando, setGuardando] = useState(false);

  // Al abrir, el formulario se rellena con el suscriptor (o se vacia en alta).
  useEffect(() => {
    if (!open) return;
    setErrores({});
    setErrorGeneral(null);
    setGuardando(false);
    if (suscriptor) {
      setValores({
        username: suscriptor.username,
        password: "",
        activation_code: "",
        email: suscriptor.email ?? "",
        phone: suscriptor.phone ?? "",
        full_name: suscriptor.full_name ?? "",
        id_cedula: suscriptor.id_cedula ?? "",
        notes: suscriptor.notes ?? "",
      });
      setEstado(suscriptor.status);
    } else {
      setValores(VACIO);
      setEstado("active");
    }
  }, [open, suscriptor]);

  const cambiar = (campo: keyof typeof VACIO) => (valor: string) => {
    setValores((actual) => ({ ...actual, [campo]: valor }));
  };

  const validar = (): boolean => {
    const nuevos: Errores = {};

    if (esAlta) {
      const usuario = valores.username.trim();
      if (usuario.length < 3 || usuario.length > 64) {
        nuevos.username = "Entre 3 y 64 caracteres.";
      } else if (!PATRON_USUARIO.test(usuario)) {
        nuevos.username = "Solo letras, numeros y . _ -";
      }

      const clave = valores.password;
      const codigo = valores.activation_code.trim();
      if (!clave && !codigo) {
        nuevos.password = "Indica una contrasena o un codigo de activacion.";
      } else if (clave && (clave.length < 6 || clave.length > 128)) {
        nuevos.password = "Entre 6 y 128 caracteres.";
      }
      if (codigo.length > 64) nuevos.activation_code = "Maximo 64 caracteres.";
    }

    if (valores.phone.trim().length > 32) nuevos.phone = "Maximo 32 caracteres.";
    if (valores.full_name.trim().length > 128) nuevos.full_name = "Maximo 128 caracteres.";
    if (valores.id_cedula.trim().length > 32) nuevos.id_cedula = "Maximo 32 caracteres.";

    setErrores(nuevos);
    return Object.keys(nuevos).length === 0;
  };

  const guardar = async () => {
    if (!validar()) return;
    setGuardando(true);
    setErrorGeneral(null);
    try {
      if (esAlta) {
        const cuerpo: SubscriberCreate = {
          username: valores.username.trim(),
          password: nuloSiVacio(valores.password),
          activation_code: nuloSiVacio(valores.activation_code),
          email: nuloSiVacio(valores.email),
          phone: nuloSiVacio(valores.phone),
          full_name: nuloSiVacio(valores.full_name),
          id_cedula: nuloSiVacio(valores.id_cedula),
          notes: nuloSiVacio(valores.notes),
        };
        const respuesta = await api.createSubscriber(cuerpo);
        if (!respuesta.data) throw new Error("La API no devolvio el suscriptor creado.");
        toast.success(`Suscriptor ${respuesta.data.username} creado.`);
        onGuardado(respuesta.data);
      } else {
        const cuerpo: SubscriberUpdate = {
          email: nuloSiVacio(valores.email),
          phone: nuloSiVacio(valores.phone),
          full_name: nuloSiVacio(valores.full_name),
          id_cedula: nuloSiVacio(valores.id_cedula),
          status: estado,
          notes: nuloSiVacio(valores.notes),
        };
        const respuesta = await api.updateSubscriber(suscriptor.id, cuerpo);
        if (!respuesta.data) throw new Error("La API no devolvio el suscriptor actualizado.");
        toast.success(`Suscriptor ${respuesta.data.username} actualizado.`);
        onGuardado(respuesta.data);
      }
    } catch (err) {
      setErrorGeneral(messageForError(err));
    } finally {
      setGuardando(false);
    }
  };

  return (
    <Modal
      open={open}
      title={esAlta ? "Nuevo suscriptor" : `Editar ${suscriptor?.username ?? ""}`}
      onClose={onClose}
      size="lg"
      dismissable={!guardando}
      footer={
        <>
          <Button variant="ghost" onClick={onClose} disabled={guardando}>
            Cancelar
          </Button>
          <Button variant="primary" onClick={guardar} loading={guardando}>
            {esAlta ? "Crear" : "Guardar"}
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

        {esAlta ? (
          <>
            <Field label="Usuario" error={errores.username} required>
              {(props) => (
                <TextInput
                  {...props}
                  value={valores.username}
                  onChange={(evento) => cambiar("username")(evento.target.value)}
                  autoComplete="off"
                  maxLength={64}
                />
              )}
            </Field>
            <div className="susc-form-grid">
              <Field
                label="Contrasena"
                error={errores.password}
                hint="Minimo 6 caracteres. Puedes dejarla vacia y usar un codigo de activacion."
              >
                {(props) => (
                  <TextInput
                    {...props}
                    type="password"
                    value={valores.password}
                    onChange={(evento) => cambiar("password")(evento.target.value)}
                    autoComplete="new-password"
                    maxLength={128}
                  />
                )}
              </Field>
              <Field
                label="Codigo de activacion"
                error={errores.activation_code}
                hint="Si lo dejas vacio y no pones contrasena, la API genera uno."
              >
                {(props) => (
                  <TextInput
                    {...props}
                    value={valores.activation_code}
                    onChange={(evento) => cambiar("activation_code")(evento.target.value)}
                    autoComplete="off"
                    maxLength={64}
                  />
                )}
              </Field>
            </div>
          </>
        ) : (
          <Field label="Estado">
            {(props) => (
              <Select
                {...props}
                value={estado}
                onChange={(evento) => setEstado(evento.target.value as SubscriberStatus)}
              >
                {SUBSCRIBER_STATUSES.map((valor) => (
                  <option key={valor} value={valor}>
                    {valor}
                  </option>
                ))}
              </Select>
            )}
          </Field>
        )}

        <div className="susc-form-grid">
          <Field label="Nombre completo" error={errores.full_name}>
            {(props) => (
              <TextInput
                {...props}
                value={valores.full_name}
                onChange={(evento) => cambiar("full_name")(evento.target.value)}
                maxLength={128}
              />
            )}
          </Field>
          <Field label="Cedula / ID" error={errores.id_cedula}>
            {(props) => (
              <TextInput
                {...props}
                value={valores.id_cedula}
                onChange={(evento) => cambiar("id_cedula")(evento.target.value)}
                maxLength={32}
              />
            )}
          </Field>
          <Field label="Email" error={errores.email}>
            {(props) => (
              <TextInput
                {...props}
                type="email"
                value={valores.email}
                onChange={(evento) => cambiar("email")(evento.target.value)}
                autoComplete="off"
              />
            )}
          </Field>
          <Field label="Telefono" error={errores.phone}>
            {(props) => (
              <TextInput
                {...props}
                value={valores.phone}
                onChange={(evento) => cambiar("phone")(evento.target.value)}
                maxLength={32}
              />
            )}
          </Field>
        </div>

        <Field label="Notas">
          {(props) => (
            <TextArea
              {...props}
              value={valores.notes}
              onChange={(evento) => cambiar("notes")(evento.target.value)}
              rows={3}
            />
          )}
        </Field>
      </form>
    </Modal>
  );
}
