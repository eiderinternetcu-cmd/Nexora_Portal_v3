import { useEffect, useState } from "react";

import { decimalToNumber } from "../../api/types";
import type { Plan } from "../../api/types";
import { Button, Field, Modal, Select, TextArea } from "../../ui/primitives";

type Props = {
  open: boolean;
  /** "crear" exige plan; "renovar" permite conservar el plan actual. */
  modo: "crear" | "renovar";
  planes: Plan[];
  planesError: string | null;
  planActual?: Plan | null;
  guardando: boolean;
  onClose: () => void;
  /** planId null en "renovar" = conservar el plan actual (asi lo hace la API). */
  onConfirmar: (planId: string | null, nota: string | null) => void;
};

const etiquetaPlan = (plan: Plan) => {
  const precio = decimalToNumber(plan.price);
  const partes = [`${plan.duration_days} dias`];
  if (precio !== null) partes.push(precio.toFixed(2));
  return `${plan.name} (${partes.join(", ")})`;
};

/**
 * Alta y renovacion de suscripcion.
 *
 * POST /api/admin/subscribers/{sub_id}/subscriptions
 * POST /api/admin/subscribers/{sub_id}/subscriptions/{subscription_id}/renew
 *
 * Solo se ofrecen planes activos: SubscriptionService rechaza los inactivos.
 */
export function SuscripcionModal({
  open,
  modo,
  planes,
  planesError,
  planActual,
  guardando,
  onClose,
  onConfirmar,
}: Props) {
  const [planId, setPlanId] = useState("");
  const [nota, setNota] = useState("");
  const [error, setError] = useState<string | null>(null);

  const activos = planes.filter((plan) => plan.is_active);

  useEffect(() => {
    if (!open) return;
    setPlanId("");
    setNota("");
    setError(null);
  }, [open]);

  const confirmar = () => {
    if (modo === "crear" && !planId) {
      setError("Elige un plan.");
      return;
    }
    setError(null);
    onConfirmar(planId ? planId : null, nota.trim() ? nota.trim() : null);
  };

  return (
    <Modal
      open={open}
      title={modo === "crear" ? "Nueva suscripcion" : "Renovar suscripcion"}
      onClose={onClose}
      size="md"
      dismissable={!guardando}
      footer={
        <>
          <Button variant="ghost" onClick={onClose} disabled={guardando}>
            Cancelar
          </Button>
          <Button variant="primary" onClick={confirmar} loading={guardando}>
            {modo === "crear" ? "Crear suscripcion" : "Renovar"}
          </Button>
        </>
      }
    >
      <form
        className="susc-form"
        onSubmit={(evento) => {
          evento.preventDefault();
          confirmar();
        }}
      >
        {planesError ? (
          <p className="susc-form-error" role="alert">
            No se pudieron cargar los planes: {planesError}
          </p>
        ) : null}

        {modo === "crear" ? (
          <p className="susc-aviso">
            Crear una suscripcion desactiva la suscripcion activa anterior (si la hay);
            el historial se conserva.
          </p>
        ) : (
          <p className="susc-aviso">
            Si la suscripcion sigue vigente, la renovacion extiende desde su fecha de
            caducidad; si ya vencio, cuenta desde hoy.
            {planActual ? ` Plan actual: ${planActual.name}.` : ""}
          </p>
        )}

        <Field
          label="Plan"
          error={error}
          required={modo === "crear"}
          hint={modo === "renovar" ? "Vacio = conservar el plan actual." : undefined}
        >
          {(props) => (
            <Select
              {...props}
              value={planId}
              onChange={(evento) => setPlanId(evento.target.value)}
              disabled={activos.length === 0}
            >
              <option value="">
                {activos.length === 0
                  ? "No hay planes activos"
                  : modo === "renovar"
                    ? "Conservar el plan actual"
                    : "Elige un plan"}
              </option>
              {activos.map((plan) => (
                <option key={plan.id} value={plan.id}>
                  {etiquetaPlan(plan)}
                </option>
              ))}
            </Select>
          )}
        </Field>

        <Field label="Nota">
          {(props) => (
            <TextArea
              {...props}
              value={nota}
              onChange={(evento) => setNota(evento.target.value)}
              rows={2}
            />
          )}
        </Field>
      </form>
    </Modal>
  );
}
