import { useEffect, useState } from "react";

import type { Plan } from "../../api/types";
import { decimalToNumber } from "../../api/types";
import {
  Button,
  Field,
  Modal,
  TextArea,
  TextInput,
} from "../../ui/primitives";

/**
 * Valores ya normalizados que devuelve el formulario. La pantalla decide si
 * los manda a POST /plans (alta) o a PATCH /plans/{id} (edicion): `is_active`
 * solo existe en `PlanUpdate`, en el alta la API lo pone a true por defecto.
 */
export type PlanPayload = {
  name: string;
  description: string | null;
  max_connections: number;
  max_devices: number;
  duration_days: number;
  price: number | null;
  notes: string | null;
  is_active: boolean;
};

export type PlanFormModalProps = {
  open: boolean;
  /** null = alta; un plan = edicion. */
  plan: Plan | null;
  saving: boolean;
  onSubmit: (payload: PlanPayload) => void;
  onClose: () => void;
};

type FormState = {
  name: string;
  description: string;
  max_connections: string;
  max_devices: string;
  duration_days: string;
  price: string;
  notes: string;
  is_active: boolean;
};

type FormErrors = Partial<Record<keyof FormState, string>>;

// Valores por defecto identicos a los de PlanCreate (app/schemas/plan.py).
const EMPTY: FormState = {
  name: "",
  description: "",
  max_connections: "1",
  max_devices: "2",
  duration_days: "30",
  price: "",
  notes: "",
  is_active: true,
};

const fromPlan = (plan: Plan): FormState => {
  const price = decimalToNumber(plan.price);
  return {
    name: plan.name,
    description: plan.description ?? "",
    max_connections: String(plan.max_connections),
    max_devices: String(plan.max_devices),
    duration_days: String(plan.duration_days),
    price: price === null ? "" : String(price),
    notes: plan.notes ?? "",
    is_active: plan.is_active,
  };
};

/** Entero dentro de rango, con el mismo criterio que valida Pydantic. */
const parseInteger = (raw: string, min: number, max: number) => {
  const value = Number(raw.trim());
  if (!Number.isInteger(value) || value < min || value > max) return null;
  return value;
};

export function PlanFormModal({ open, plan, saving, onSubmit, onClose }: PlanFormModalProps) {
  const [form, setForm] = useState<FormState>(EMPTY);
  const [errors, setErrors] = useState<FormErrors>({});

  // Al abrir, el formulario parte del plan editado o queda limpio para el alta.
  useEffect(() => {
    if (!open) return;
    setForm(plan ? fromPlan(plan) : EMPTY);
    setErrors({});
  }, [open, plan]);

  const set = <K extends keyof FormState>(key: K, value: FormState[K]) =>
    setForm((current) => ({ ...current, [key]: value }));

  const handleSubmit = () => {
    const next: FormErrors = {};

    const name = form.name.trim();
    if (name.length < 2 || name.length > 128) {
      next.name = "El nombre debe tener entre 2 y 128 caracteres.";
    }

    const maxConnections = parseInteger(form.max_connections, 1, 100);
    if (maxConnections === null) next.max_connections = "Entero entre 1 y 100.";

    const maxDevices = parseInteger(form.max_devices, 1, 50);
    if (maxDevices === null) next.max_devices = "Entero entre 1 y 50.";

    const durationDays = parseInteger(form.duration_days, 1, 100000);
    if (durationDays === null) next.duration_days = "Entero mayor o igual que 1.";

    let price: number | null = null;
    const rawPrice = form.price.trim();
    if (rawPrice !== "") {
      const parsed = Number(rawPrice);
      if (!Number.isFinite(parsed) || parsed < 0) {
        next.price = "Numero mayor o igual que 0, o vacio.";
      } else {
        price = parsed;
      }
    }

    setErrors(next);
    if (Object.keys(next).length > 0) return;

    onSubmit({
      name,
      description: form.description.trim() === "" ? null : form.description.trim(),
      max_connections: maxConnections as number,
      max_devices: maxDevices as number,
      duration_days: durationDays as number,
      price,
      notes: form.notes.trim() === "" ? null : form.notes.trim(),
      is_active: form.is_active,
    });
  };

  return (
    <Modal
      open={open}
      title={plan ? `Editar plan: ${plan.name}` : "Nuevo plan"}
      onClose={onClose}
      size="lg"
      dismissable={!saving}
      footer={
        <>
          <Button variant="ghost" onClick={onClose} disabled={saving}>
            Cancelar
          </Button>
          <Button variant="primary" onClick={handleSubmit} loading={saving}>
            {plan ? "Guardar cambios" : "Crear plan"}
          </Button>
        </>
      }
    >
      <form
        className="planes-form-grid"
        onSubmit={(event) => {
          event.preventDefault();
          handleSubmit();
        }}
      >
        <div className="planes-span-2">
          <Field label="Nombre" error={errors.name} required>
            {(props) => (
              <TextInput
                {...props}
                value={form.name}
                maxLength={128}
                autoComplete="off"
                onChange={(event) => set("name", event.target.value)}
              />
            )}
          </Field>
        </div>

        <Field
          label="Duracion (dias)"
          error={errors.duration_days}
          hint="Dias que dura la suscripcion creada con este plan."
          required
        >
          {(props) => (
            <TextInput
              {...props}
              type="number"
              min={1}
              step={1}
              value={form.duration_days}
              onChange={(event) => set("duration_days", event.target.value)}
            />
          )}
        </Field>

        <Field
          label="Precio"
          error={errors.price}
          hint="Opcional. Dejalo vacio si el plan no tiene precio asignado."
        >
          {(props) => (
            <TextInput
              {...props}
              type="number"
              min={0}
              step="0.01"
              value={form.price}
              onChange={(event) => set("price", event.target.value)}
            />
          )}
        </Field>

        <Field
          label="Conexiones simultaneas"
          error={errors.max_connections}
          hint="1 a 100. Sesiones de reproduccion a la vez."
          required
        >
          {(props) => (
            <TextInput
              {...props}
              type="number"
              min={1}
              max={100}
              step={1}
              value={form.max_connections}
              onChange={(event) => set("max_connections", event.target.value)}
            />
          )}
        </Field>

        <Field
          label="Dispositivos maximos"
          error={errors.max_devices}
          hint="1 a 50. Equipos que puede registrar el suscriptor."
          required
        >
          {(props) => (
            <TextInput
              {...props}
              type="number"
              min={1}
              max={50}
              step={1}
              value={form.max_devices}
              onChange={(event) => set("max_devices", event.target.value)}
            />
          )}
        </Field>

        <div className="planes-span-2">
          <Field label="Descripcion" error={errors.description}>
            {(props) => (
              <TextArea
                {...props}
                value={form.description}
                onChange={(event) => set("description", event.target.value)}
              />
            )}
          </Field>
        </div>

        <div className="planes-span-2">
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

        {plan ? (
          <div className="planes-span-2">
            <label className="planes-check">
              <input
                type="checkbox"
                checked={form.is_active}
                onChange={(event) => set("is_active", event.target.checked)}
              />
              Plan activo. Si lo desactivas, la reproduccion de sus suscriptores se
              deniega con PLAN_INACTIVE.
            </label>
          </div>
        ) : null}

        {/* Permite enviar con Enter sin duplicar el boton visible del pie. */}
        <button type="submit" className="sr-only" tabIndex={-1} aria-hidden>
          Guardar
        </button>
      </form>
    </Modal>
  );
}
