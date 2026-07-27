import { AlertTriangle } from "lucide-react";

import type { Plan } from "../../api/types";
import { Button, Modal } from "../../ui/primitives";

export type PlanChannelsModalProps = {
  open: boolean;
  plan: Plan | null;
  onClose: () => void;
};

/**
 * Canales incluidos en un plan.
 *
 * NO HAY PANTALLA PORQUE NO HAY ENDPOINT. La tabla `plan_channels` es la lista
 * blanca que decide si un canal se puede ver (app/services/entitlement_service.py:130-141)
 * y hoy la API no la expone: ni para leerla ni para escribirla. Se revisaron
 * app/api/admin/router.py, app/api/admin/channels.py y app/api/v1/plans.py y
 * ninguno toca plan_channels.
 *
 * Esta ventana explica el hueco con datos reales del repositorio en lugar de
 * fingir una gestion que la API no soporta.
 */
export function PlanChannelsModal({ open, plan, onClose }: PlanChannelsModalProps) {
  return (
    <Modal
      open={open}
      title={plan ? `Canales incluidos: ${plan.name}` : "Canales incluidos"}
      onClose={onClose}
      size="lg"
      footer={
        <Button variant="primary" onClick={onClose}>
          Entendido
        </Button>
      }
    >
      <div className="planes-gap">
        <div className="planes-gap-warning">
          <AlertTriangle size={18} aria-hidden />
          <span>
            La API no expone los canales de un plan. No se puede leer ni modificar
            la lista desde el panel, asi que aqui no se muestra ninguna: mostrar el
            catalogo completo daria a entender que el plan lo incluye entero, y eso
            seria falso.
          </span>
        </div>

        <p>
          <code>plan_channels</code> es una <strong>lista blanca estricta</strong>. Si un
          canal no tiene fila con <code>is_enabled = true</code> para el plan del
          suscriptor, la reproduccion se deniega con <code>CHANNEL_NOT_INCLUDED</code>.
          Un plan sin filas no da acceso a todo: no da acceso a nada. Con
          <code> ENTITLEMENT_ENFORCE </code> activo esto se aplica en produccion.
        </p>

        <div>
          <h4>Como se administra hoy</h4>
          <p>
            A mano, ejecutando el script de siembra dentro del contenedor de la API.
            Es idempotente e incluye todos los canales activos en el plan indicado:
          </p>
          <pre>
            {plan
              ? `SEED_PLAN_NAME="${plan.name}" python scripts/seed_plan_channels.py`
              : `SEED_PLAN_NAME="<nombre del plan>" python scripts/seed_plan_channels.py`}
          </pre>
        </div>

        <div>
          <h4>Lo que falta en la API</h4>
          <ul className="planes-gap-list">
            <li>
              <code>GET /api/admin/plans/{"{plan_id}"}/channels</code> — canales del plan
              con su <code>is_enabled</code>, para poder pintar esta pantalla.
            </li>
            <li>
              <code>PUT /api/admin/plans/{"{plan_id}"}/channels</code> — reemplazo completo
              de la lista blanca en una operacion (cuerpo:{" "}
              <code>{"{ channel_ids: [...] }"}</code>).
            </li>
            <li>
              <code>POST</code> y <code>DELETE /api/admin/plans/{"{plan_id}"}/channels/{"{channel_id}"}</code>{" "}
              — alta y baja de un canal suelto.
            </li>
          </ul>
        </div>
      </div>
    </Modal>
  );
}
