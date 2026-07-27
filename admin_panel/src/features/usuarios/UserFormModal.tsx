import { useEffect, useState } from "react";

import type { User, UserCreate, UserRole, UserUpdate } from "../../api/types";
import {
  Button,
  Field,
  Modal,
  Select,
  TextArea,
  TextInput,
} from "../../ui/primitives";

/**
 * Alta y edicion de usuario del panel.
 *
 * El rol y el estado activo NO se editan aqui a proposito: son cambios de
 * permisos con efecto inmediato y tienen su propia accion confirmada en el
 * listado. En el alta si hay que elegir rol, y la pantalla lo confirma aparte
 * cuando el rol elegido es admin.
 */
export type UserFormPayload =
  | { mode: "create"; body: UserCreate }
  | { mode: "edit"; body: UserUpdate };

export type UserFormModalProps = {
  open: boolean;
  /** null = alta; un usuario = edicion. */
  user: User | null;
  saving: boolean;
  onSubmit: (payload: UserFormPayload) => void;
  onClose: () => void;
};

type FormState = {
  username: string;
  email: string;
  password: string;
  full_name: string;
  role: UserRole;
  notes: string;
};

type FormErrors = Partial<Record<keyof FormState, string>>;

const EMPTY: FormState = {
  username: "",
  email: "",
  password: "",
  full_name: "",
  role: "reseller",
  notes: "",
};

// Mismo patron que UserCreate.username en app/schemas/user.py.
const USERNAME_PATTERN = /^[a-zA-Z0-9_.-]+$/;

export function UserFormModal({ open, user, saving, onSubmit, onClose }: UserFormModalProps) {
  const [form, setForm] = useState<FormState>(EMPTY);
  const [errors, setErrors] = useState<FormErrors>({});

  useEffect(() => {
    if (!open) return;
    setForm(
      user
        ? {
            username: user.username,
            email: user.email,
            password: "",
            full_name: user.full_name ?? "",
            role: user.role,
            notes: user.notes ?? "",
          }
        : EMPTY,
    );
    setErrors({});
  }, [open, user]);

  const set = <K extends keyof FormState>(key: K, value: FormState[K]) =>
    setForm((current) => ({ ...current, [key]: value }));

  const handleSubmit = () => {
    const next: FormErrors = {};
    const username = form.username.trim();
    const email = form.email.trim();
    const fullName = form.full_name.trim();
    const notes = form.notes.trim();

    if (!user) {
      if (username.length < 3 || username.length > 64) {
        next.username = "Entre 3 y 64 caracteres.";
      } else if (!USERNAME_PATTERN.test(username)) {
        next.username = "Solo letras, numeros y los signos . _ -";
      }
      if (form.password.length < 8 || form.password.length > 128) {
        next.password = "Entre 8 y 128 caracteres.";
      }
    }

    if (email === "" || !email.includes("@")) {
      next.email = "Escribe un email valido.";
    }
    if (fullName.length > 128) {
      next.full_name = "Maximo 128 caracteres.";
    }

    setErrors(next);
    if (Object.keys(next).length > 0) return;

    if (user) {
      onSubmit({
        mode: "edit",
        body: {
          email,
          full_name: fullName === "" ? null : fullName,
          notes: notes === "" ? null : notes,
        },
      });
      return;
    }

    onSubmit({
      mode: "create",
      body: {
        username,
        email,
        password: form.password,
        full_name: fullName === "" ? null : fullName,
        role: form.role,
        notes: notes === "" ? null : notes,
      },
    });
  };

  return (
    <Modal
      open={open}
      title={user ? `Editar usuario: ${user.username}` : "Nuevo usuario"}
      onClose={onClose}
      size="lg"
      dismissable={!saving}
      footer={
        <>
          <Button variant="ghost" onClick={onClose} disabled={saving}>
            Cancelar
          </Button>
          <Button variant="primary" onClick={handleSubmit} loading={saving}>
            {user ? "Guardar cambios" : "Crear usuario"}
          </Button>
        </>
      }
    >
      <form
        className="usuarios-form-grid"
        onSubmit={(event) => {
          event.preventDefault();
          handleSubmit();
        }}
      >
        <Field
          label="Usuario"
          error={errors.username}
          hint={user ? "El nombre de usuario no se puede cambiar." : "Letras, numeros y . _ -"}
          required={!user}
        >
          {(props) => (
            <TextInput
              {...props}
              value={form.username}
              maxLength={64}
              autoComplete="off"
              disabled={user !== null}
              onChange={(event) => set("username", event.target.value)}
            />
          )}
        </Field>

        <Field label="Email" error={errors.email} required>
          {(props) => (
            <TextInput
              {...props}
              type="email"
              value={form.email}
              autoComplete="off"
              onChange={(event) => set("email", event.target.value)}
            />
          )}
        </Field>

        {user ? null : (
          <Field
            label="Contrasena"
            error={errors.password}
            hint="Minimo 8 caracteres. Solo se define en el alta."
            required
          >
            {(props) => (
              <TextInput
                {...props}
                type="password"
                value={form.password}
                autoComplete="new-password"
                onChange={(event) => set("password", event.target.value)}
              />
            )}
          </Field>
        )}

        {user ? null : (
          <Field
            label="Rol"
            error={errors.role}
            hint="admin gestiona todo el panel; reseller tiene permisos reducidos."
          >
            {(props) => (
              <Select
                {...props}
                value={form.role}
                onChange={(event) => set("role", event.target.value as UserRole)}
              >
                <option value="reseller">reseller</option>
                <option value="admin">admin</option>
              </Select>
            )}
          </Field>
        )}

        <div className="usuarios-span-2">
          <Field label="Nombre completo" error={errors.full_name}>
            {(props) => (
              <TextInput
                {...props}
                value={form.full_name}
                maxLength={128}
                onChange={(event) => set("full_name", event.target.value)}
              />
            )}
          </Field>
        </div>

        <div className="usuarios-span-2">
          <Field label="Notas internas" error={errors.notes}>
            {(props) => (
              <TextArea
                {...props}
                value={form.notes}
                onChange={(event) => set("notes", event.target.value)}
              />
            )}
          </Field>
        </div>

        {user ? (
          <p className="usuarios-note usuarios-span-2">
            El rol y el estado activo se cambian desde el listado, con confirmacion.
          </p>
        ) : null}

        {/* Permite enviar con Enter sin duplicar el boton visible del pie. */}
        <button type="submit" className="sr-only" tabIndex={-1} aria-hidden>
          Guardar
        </button>
      </form>
    </Modal>
  );
}
