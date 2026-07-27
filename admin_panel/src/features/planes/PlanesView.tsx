import { useCallback, useEffect, useState } from "react";
import { Pencil, Plus, Power, RefreshCw, Trash2, Tv } from "lucide-react";

import { useApi, useAuth } from "../../auth/AuthContext";
import { messageForError } from "../../api/errors";
import { decimalToNumber } from "../../api/types";
import type { Plan, PlanCreate, PlanUpdate } from "../../api/types";
import {
  Button,
  ConfirmDialog,
  DataTable,
  StatusBadge,
  toneForBoolean,
  useToast,
} from "../../ui/primitives";
import type { Column } from "../../ui/primitives";
import { PlanChannelsModal } from "./PlanChannelsModal";
import { PlanFormModal } from "./PlanFormModal";
import type { PlanPayload } from "./PlanFormModal";
import "./planes.css";

/** El precio llega como Decimal (string o number segun Pydantic): normalizar siempre. */
const formatPrice = (value: Plan["price"]) => {
  const amount = decimalToNumber(value);
  if (amount === null) return "Sin precio";
  return amount.toLocaleString("es-ES", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
};

/**
 * Catalogo comercial de planes.
 *
 * Endpoints (prefijo /api/admin):
 *   GET    /plans            -> ApiResponse<Plan[]>   (admin o reseller)
 *   POST   /plans            -> ApiResponse<Plan>     (solo admin)
 *   PATCH  /plans/{id}       -> ApiResponse<Plan>     (solo admin)
 *   DELETE /plans/{id}       -> MessageResponse       (solo admin)
 *
 * La lista blanca de canales de cada plan se edita en PlanChannelsModal
 * (GET/PUT /plans/{id}/channels). Leerla vale para admin y reseller;
 * modificarla, solo para admin.
 */
export function PlanesView() {
  const api = useApi();
  const { isAdmin } = useAuth();
  const toast = useToast();

  const [plans, setPlans] = useState<Plan[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [formOpen, setFormOpen] = useState(false);
  const [editing, setEditing] = useState<Plan | null>(null);
  const [saving, setSaving] = useState(false);

  const [channelsTarget, setChannelsTarget] = useState<Plan | null>(null);

  const [toggleTarget, setToggleTarget] = useState<Plan | null>(null);
  const [toggling, setToggling] = useState(false);

  const [deleteTarget, setDeleteTarget] = useState<Plan | null>(null);
  const [deleting, setDeleting] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const response = await api.listPlans();
      // ApiResponse<Plan[]>: los datos van en .data y pueden venir a null.
      setPlans(response.data ?? []);
    } catch (err) {
      setPlans([]);
      setError(messageForError(err));
    } finally {
      setLoading(false);
    }
  }, [api]);

  useEffect(() => {
    void load();
  }, [load]);

  const openCreate = () => {
    setEditing(null);
    setFormOpen(true);
  };

  const openEdit = (plan: Plan) => {
    setEditing(plan);
    setFormOpen(true);
  };

  const handleSubmit = async (payload: PlanPayload) => {
    setSaving(true);
    try {
      if (editing) {
        const body: PlanUpdate = {
          name: payload.name,
          description: payload.description,
          max_connections: payload.max_connections,
          max_devices: payload.max_devices,
          duration_days: payload.duration_days,
          price: payload.price,
          notes: payload.notes,
          is_active: payload.is_active,
        };
        await api.updatePlan(editing.id, body);
        toast.success(`Plan "${payload.name}" actualizado.`);
      } else {
        // PlanCreate no acepta is_active: la API lo crea activo.
        const body: PlanCreate = {
          name: payload.name,
          description: payload.description,
          max_connections: payload.max_connections,
          max_devices: payload.max_devices,
          duration_days: payload.duration_days,
          price: payload.price,
          notes: payload.notes,
        };
        await api.createPlan(body);
        toast.success(`Plan "${payload.name}" creado.`);
      }
      setFormOpen(false);
      setEditing(null);
      await load();
    } catch (err) {
      toast.error(messageForError(err));
    } finally {
      setSaving(false);
    }
  };

  const handleToggle = async () => {
    if (!toggleTarget) return;
    setToggling(true);
    try {
      await api.updatePlan(toggleTarget.id, { is_active: !toggleTarget.is_active });
      toast.success(
        toggleTarget.is_active
          ? `Plan "${toggleTarget.name}" desactivado.`
          : `Plan "${toggleTarget.name}" activado.`,
      );
      setToggleTarget(null);
      await load();
    } catch (err) {
      toast.error(messageForError(err));
    } finally {
      setToggling(false);
    }
  };

  const handleDelete = async () => {
    if (!deleteTarget) return;
    setDeleting(true);
    try {
      await api.deletePlan(deleteTarget.id);
      toast.success(`Plan "${deleteTarget.name}" eliminado.`);
      setDeleteTarget(null);
      await load();
    } catch (err) {
      toast.error(messageForError(err));
    } finally {
      setDeleting(false);
    }
  };

  const columns: Column<Plan>[] = [
    {
      key: "name",
      header: "Plan",
      render: (plan) => (
        <div className="planes-name">
          <strong>{plan.name}</strong>
          {plan.description ? <span>{plan.description}</span> : null}
        </div>
      ),
    },
    {
      key: "duration",
      header: "Duracion",
      align: "right",
      width: "110px",
      render: (plan) => <span className="planes-num">{plan.duration_days} dias</span>,
    },
    {
      key: "connections",
      header: "Conexiones",
      align: "right",
      width: "110px",
      render: (plan) => <span className="planes-num">{plan.max_connections}</span>,
    },
    {
      key: "devices",
      header: "Dispositivos",
      align: "right",
      width: "115px",
      render: (plan) => <span className="planes-num">{plan.max_devices}</span>,
    },
    {
      key: "price",
      header: "Precio",
      align: "right",
      width: "120px",
      render: (plan) => <span className="planes-num">{formatPrice(plan.price)}</span>,
    },
    {
      key: "status",
      header: "Estado",
      width: "110px",
      render: (plan) => (
        <StatusBadge tone={toneForBoolean(plan.is_active)} dot>
          {plan.is_active ? "Activo" : "Inactivo"}
        </StatusBadge>
      ),
    },
    {
      key: "actions",
      header: "",
      align: "right",
      width: "300px",
      render: (plan) => (
        <div className="planes-row-actions">
          <Button
            size="sm"
            variant="ghost"
            icon={<Tv size={15} />}
            onClick={() => setChannelsTarget(plan)}
          >
            Canales
          </Button>
          {isAdmin ? (
            <>
              <Button
                size="sm"
                variant="ghost"
                icon={<Pencil size={15} />}
                onClick={() => openEdit(plan)}
              >
                Editar
              </Button>
              <Button
                size="sm"
                variant="ghost"
                icon={<Power size={15} />}
                onClick={() => setToggleTarget(plan)}
              >
                {plan.is_active ? "Desactivar" : "Activar"}
              </Button>
              <Button
                size="sm"
                variant="ghost"
                icon={<Trash2 size={15} />}
                onClick={() => setDeleteTarget(plan)}
              >
                Eliminar
              </Button>
            </>
          ) : null}
        </div>
      ),
    },
  ];

  return (
    <section>
      <header className="planes-header">
        <div className="planes-heading">
          <h2>Planes</h2>
          <p>
            Catalogo comercial. Con <strong>Canales</strong> se edita la lista blanca de
            cada plan: solo se reproduce lo que este incluido, y un plan sin canales
            no deja ver nada.
          </p>
        </div>
        <div className="planes-header-actions">
          <Button icon={<RefreshCw size={16} />} onClick={() => void load()} disabled={loading}>
            Actualizar
          </Button>
          {isAdmin ? (
            <Button variant="primary" icon={<Plus size={16} />} onClick={openCreate}>
              Nuevo plan
            </Button>
          ) : null}
        </div>
      </header>

      <DataTable
        columns={columns}
        rows={plans}
        rowKey={(plan) => plan.id}
        loading={loading}
        error={error}
        onRetry={() => void load()}
        emptyMessage="No hay planes creados todavia."
        caption="Listado de planes"
      />

      <PlanFormModal
        open={formOpen}
        plan={editing}
        saving={saving}
        onSubmit={(payload) => void handleSubmit(payload)}
        onClose={() => {
          if (saving) return;
          setFormOpen(false);
          setEditing(null);
        }}
      />

      <PlanChannelsModal
        open={channelsTarget !== null}
        plan={channelsTarget}
        canEdit={isAdmin}
        onClose={() => setChannelsTarget(null)}
      />

      <ConfirmDialog
        open={toggleTarget !== null}
        tone={toggleTarget?.is_active ? "danger" : "default"}
        title={toggleTarget?.is_active ? "Desactivar plan" : "Activar plan"}
        message={
          toggleTarget?.is_active
            ? `Se desactivara "${toggleTarget.name}". Los suscriptores con este plan dejaran de reproducir al instante (PLAN_INACTIVE).`
            : `Se activara "${toggleTarget?.name ?? ""}". Sus suscriptores volveran a poder reproducir.`
        }
        confirmLabel={toggleTarget?.is_active ? "Desactivar" : "Activar"}
        busy={toggling}
        onCancel={() => {
          if (!toggling) setToggleTarget(null);
        }}
        onConfirm={() => void handleToggle()}
      />

      <ConfirmDialog
        open={deleteTarget !== null}
        tone="danger"
        title="Eliminar plan"
        message={`Se eliminara el plan "${deleteTarget?.name ?? ""}". Esta accion no se puede deshacer y afecta a las suscripciones que lo usan.`}
        confirmLabel="Eliminar"
        busy={deleting}
        onCancel={() => {
          if (!deleting) setDeleteTarget(null);
        }}
        onConfirm={() => void handleDelete()}
      />
    </section>
  );
}
