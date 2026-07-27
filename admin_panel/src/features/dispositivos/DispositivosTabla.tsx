import { useState } from "react";
import type { ReactNode } from "react";
import { Ban, ShieldCheck, Trash2 } from "lucide-react";

import { messageForError } from "../../api/errors";
import type { Device } from "../../api/types";
import { useApi } from "../../auth/AuthContext";
import {
  Button,
  ConfirmDialog,
  DataTable,
  Field,
  Modal,
  StatusBadge,
  TextInput,
  useToast,
} from "../../ui/primitives";
import type { BadgeTone, Column } from "../../ui/primitives";
import "./dispositivos.css";

/** Dispositivo mas el nombre de su suscriptor, que la API de devices no trae. */
export type FilaDispositivo = Device & {
  suscriptorUsername?: string | null;
};

type Props = {
  filas: FilaDispositivo[];
  loading: boolean;
  error: string | null;
  onRetry: () => void;
  /** Recargar tras bloquear, desbloquear o eliminar. */
  onCambio: () => void;
  /** Oculta la columna de suscriptor cuando ya estas dentro de uno. */
  mostrarSuscriptor?: boolean;
  emptyMessage?: string;
  footer?: ReactNode;
  caption?: string;
};

const VACIO = "--";

const FECHA_HORA = new Intl.DateTimeFormat("es-ES", {
  dateStyle: "medium",
  timeStyle: "short",
});

const formatFechaHora = (iso: string | null) => {
  if (!iso) return VACIO;
  const fecha = new Date(iso);
  return Number.isNaN(fecha.getTime()) ? VACIO : FECHA_HORA.format(fecha);
};

const texto = (valor: string | null | undefined) => {
  const limpio = valor?.trim();
  return limpio ? limpio : VACIO;
};

/** `status` del modelo: active | pending | revoked (app/models/device.py). */
const toneParaStatus = (status: string): BadgeTone => {
  if (status === "active") return "success";
  if (status === "pending") return "warning";
  if (status === "revoked") return "danger";
  return "neutral";
};

/**
 * Tabla de dispositivos con sus acciones. La usan el listado global
 * (DispositivosView) y el detalle de suscriptor, por eso recibe las filas ya
 * cargadas en vez de pedirlas: cada pantalla las compone a su manera.
 *
 * Acciones (app/api/v1/devices.py):
 *   POST   /api/admin/devices/{device_id}/block
 *   POST   /api/admin/devices/{device_id}/unblock
 *   DELETE /api/admin/devices/{device_id}
 */
export function DispositivosTabla({
  filas,
  loading,
  error,
  onRetry,
  onCambio,
  mostrarSuscriptor = true,
  emptyMessage = "No hay dispositivos registrados.",
  footer,
  caption,
}: Props) {
  const api = useApi();
  const toast = useToast();

  const [bloquear, setBloquear] = useState<FilaDispositivo | null>(null);
  const [motivo, setMotivo] = useState("");
  const [desbloquear, setDesbloquear] = useState<FilaDispositivo | null>(null);
  const [eliminar, setEliminar] = useState<FilaDispositivo | null>(null);
  const [ocupado, setOcupado] = useState(false);
  const [errorAccion, setErrorAccion] = useState<string | null>(null);

  const nombreDe = (fila: FilaDispositivo) =>
    fila.suscriptorUsername?.trim() || fila.subscriber_id;

  const confirmarBloqueo = async () => {
    if (!bloquear) return;
    setOcupado(true);
    setErrorAccion(null);
    try {
      await api.blockDevice(bloquear.id, { reason: motivo.trim() ? motivo.trim() : null });
      toast.success(`Dispositivo ${bloquear.device_id} bloqueado.`);
      setBloquear(null);
      setMotivo("");
      onCambio();
    } catch (err) {
      setErrorAccion(messageForError(err));
    } finally {
      setOcupado(false);
    }
  };

  const confirmarDesbloqueo = async () => {
    if (!desbloquear) return;
    setOcupado(true);
    try {
      await api.unblockDevice(desbloquear.id);
      toast.success(`Dispositivo ${desbloquear.device_id} desbloqueado.`);
      setDesbloquear(null);
      onCambio();
    } catch (err) {
      toast.error(messageForError(err));
    } finally {
      setOcupado(false);
    }
  };

  const confirmarEliminacion = async () => {
    if (!eliminar) return;
    setOcupado(true);
    try {
      await api.deleteDevice(eliminar.id);
      toast.success(`Dispositivo ${eliminar.device_id} eliminado.`);
      setEliminar(null);
      onCambio();
    } catch (err) {
      toast.error(messageForError(err));
    } finally {
      setOcupado(false);
    }
  };

  const columnas: Column<FilaDispositivo>[] = [];

  if (mostrarSuscriptor) {
    columnas.push({
      key: "suscriptor",
      header: "Suscriptor",
      width: "18%",
      render: (fila) => (
        <div className="disp-celda">
          <span className="disp-celda-principal">{nombreDe(fila)}</span>
        </div>
      ),
    });
  }

  columnas.push(
    {
      key: "dispositivo",
      header: "Dispositivo",
      render: (fila) => (
        <div className="disp-celda">
          <span className="disp-celda-principal disp-mono">{fila.device_id}</span>
          <span className="disp-celda-secundaria disp-mono">
            MAC {texto(fila.mac_address)}
          </span>
        </div>
      ),
    },
    {
      key: "tipo",
      header: "Tipo",
      width: "16%",
      render: (fila) => (
        <div className="disp-celda">
          <span className="disp-celda-principal">{texto(fila.device_type)}</span>
          <span className="disp-celda-secundaria">
            {[fila.brand, fila.model].filter(Boolean).join(" ") || VACIO}
          </span>
        </div>
      ),
    },
    {
      key: "acceso",
      header: "Ultimo acceso",
      width: "17%",
      render: (fila) => (
        <div className="disp-celda">
          <span className="disp-celda-principal">{formatFechaHora(fila.last_seen_at)}</span>
          <span className="disp-celda-secundaria disp-mono">IP {texto(fila.last_ip)}</span>
        </div>
      ),
    },
    {
      key: "estado",
      header: "Estado",
      width: "15%",
      render: (fila) => (
        <div className="disp-celda">
          {fila.is_blocked ? (
            <StatusBadge tone="danger" dot>
              bloqueado
            </StatusBadge>
          ) : (
            <StatusBadge tone={toneParaStatus(fila.status)} dot>
              {fila.status}
            </StatusBadge>
          )}
          {fila.is_blocked && fila.block_reason ? (
            <span className="disp-celda-secundaria">{fila.block_reason}</span>
          ) : null}
        </div>
      ),
    },
    {
      key: "acciones",
      header: "",
      width: "170px",
      align: "right",
      render: (fila) => (
        <div className="disp-acciones">
          {fila.is_blocked ? (
            <Button
              size="sm"
              variant="secondary"
              icon={<ShieldCheck size={14} />}
              onClick={() => setDesbloquear(fila)}
            >
              Desbloquear
            </Button>
          ) : (
            <Button
              size="sm"
              variant="secondary"
              icon={<Ban size={14} />}
              onClick={() => {
                setMotivo("");
                setErrorAccion(null);
                setBloquear(fila);
              }}
            >
              Bloquear
            </Button>
          )}
          <Button
            size="sm"
            variant="danger"
            icon={<Trash2 size={14} />}
            aria-label={`Eliminar dispositivo ${fila.device_id}`}
            onClick={() => setEliminar(fila)}
          />
        </div>
      ),
    },
  );

  return (
    <>
      <DataTable
        columns={columnas}
        rows={filas}
        rowKey={(fila) => fila.id}
        loading={loading}
        error={error}
        onRetry={onRetry}
        emptyMessage={emptyMessage}
        footer={footer}
        caption={caption}
      />

      <Modal
        open={bloquear !== null}
        title="Bloquear dispositivo"
        onClose={() => setBloquear(null)}
        size="sm"
        dismissable={!ocupado}
        footer={
          <>
            <Button variant="ghost" onClick={() => setBloquear(null)} disabled={ocupado}>
              Cancelar
            </Button>
            <Button variant="danger" onClick={confirmarBloqueo} loading={ocupado}>
              Bloquear
            </Button>
          </>
        }
      >
        <div className="disp-form">
          {errorAccion ? (
            <p className="disp-form-error" role="alert">
              {errorAccion}
            </p>
          ) : null}
          <p className="disp-aviso">
            El dispositivo <strong>{bloquear?.device_id}</strong> de{" "}
            <strong>{bloquear ? nombreDe(bloquear) : ""}</strong> dejara de reproducir.
            El resto de sus dispositivos siguen funcionando. Es reversible.
          </p>
          <Field label="Motivo" hint="Opcional. Queda en la auditoria.">
            {(props) => (
              <TextInput
                {...props}
                value={motivo}
                onChange={(evento) => setMotivo(evento.target.value)}
                maxLength={255}
              />
            )}
          </Field>
        </div>
      </Modal>

      <ConfirmDialog
        open={desbloquear !== null}
        title="Desbloquear dispositivo"
        message={
          <>
            El dispositivo <strong>{desbloquear?.device_id}</strong> de{" "}
            <strong>{desbloquear ? nombreDe(desbloquear) : ""}</strong> volvera a poder
            reproducir.
          </>
        }
        confirmLabel="Desbloquear"
        busy={ocupado}
        onCancel={() => setDesbloquear(null)}
        onConfirm={confirmarDesbloqueo}
      />

      <ConfirmDialog
        open={eliminar !== null}
        tone="danger"
        title="Eliminar dispositivo"
        message={
          <>
            Se eliminara el dispositivo <strong>{eliminar?.device_id}</strong> de{" "}
            <strong>{eliminar ? nombreDe(eliminar) : ""}</strong>. Perdera el acceso y
            tendra que registrarse otra vez. Esta accion no se puede deshacer.
          </>
        }
        confirmLabel="Eliminar"
        busy={ocupado}
        onCancel={() => setEliminar(null)}
        onConfirm={confirmarEliminacion}
      />
    </>
  );
}
