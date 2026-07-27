import { useCallback, useEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import {
  ArrowLeft,
  Ban,
  CheckCircle2,
  KeyRound,
  Pencil,
  Plus,
  RefreshCw,
  RotateCw,
  Trash2,
  XCircle,
} from "lucide-react";

import { messageForError } from "../../api/errors";
import { decimalToNumber } from "../../api/types";
import type {
  Device,
  Plan,
  Subscriber,
  SubscriberActiveStatus,
  SubscriberFull,
  Subscription,
} from "../../api/types";
import { useApi } from "../../auth/AuthContext";
import {
  Button,
  ConfirmDialog,
  DataTable,
  StatusBadge,
  toneForBoolean,
  toneForSubscriberStatus,
  useToast,
} from "../../ui/primitives";
import type { Column } from "../../ui/primitives";
import { DispositivosTabla } from "../dispositivos/DispositivosTabla";
import { SuscripcionModal } from "./SuscripcionModal";
import { SuscriptorFormModal } from "./SuscriptorFormModal";
import { SuscriptorPasswordModal } from "./SuscriptorPasswordModal";
import { VACIO, formatFecha, formatFechaHora, textoOpcional } from "./formato";
import "./suscriptores.css";

type Dato = { etiqueta: string; valor: ReactNode };

const Datos = ({ items }: { items: Dato[] }) => (
  <div className="susc-grid">
    {items.map((item) => (
      <div className="susc-dato" key={item.etiqueta}>
        <span className="susc-dato-label">{item.etiqueta}</span>
        <span className="susc-dato-valor">{item.valor}</span>
      </div>
    ))}
  </div>
);

/**
 * Detalle de un suscriptor: datos, estado de servicio, historial de
 * suscripciones y dispositivos.
 *
 * GET  /api/admin/subscribers/{sub_id}                -> ApiResponse<SubscriberOutFull>
 * GET  /api/admin/subscribers/{sub_id}/status         -> ApiResponse<SubscriberActiveStatus>
 * GET  /api/admin/subscribers/{sub_id}/subscriptions  -> ApiResponse<SubscriptionOut[]>
 * GET  /api/admin/devices/subscriber/{sub_id}         -> ApiResponse<DeviceOut[]>
 *
 * Cada bloque tiene su propio estado de carga/error: que fallen los
 * dispositivos no debe dejar en blanco las suscripciones.
 */
export function SuscriptorDetalleView() {
  const { id } = useParams<{ id: string }>();
  const api = useApi();
  const toast = useToast();
  const navigate = useNavigate();

  const [suscriptor, setSuscriptor] = useState<SubscriberFull | null>(null);
  const [cargandoSuscriptor, setCargandoSuscriptor] = useState(true);
  const [errorSuscriptor, setErrorSuscriptor] = useState<string | null>(null);

  const [servicio, setServicio] = useState<SubscriberActiveStatus | null>(null);
  const [errorServicio, setErrorServicio] = useState<string | null>(null);

  const [suscripciones, setSuscripciones] = useState<Subscription[]>([]);
  const [cargandoSuscripciones, setCargandoSuscripciones] = useState(true);
  const [errorSuscripciones, setErrorSuscripciones] = useState<string | null>(null);

  const [dispositivos, setDispositivos] = useState<Device[]>([]);
  const [cargandoDispositivos, setCargandoDispositivos] = useState(true);
  const [errorDispositivos, setErrorDispositivos] = useState<string | null>(null);

  const [planes, setPlanes] = useState<Plan[]>([]);
  const [errorPlanes, setErrorPlanes] = useState<string | null>(null);

  const [formAbierto, setFormAbierto] = useState(false);
  const [passwordAbierto, setPasswordAbierto] = useState(false);
  const [modalSuscripcion, setModalSuscripcion] = useState<
    { modo: "crear" } | { modo: "renovar"; suscripcion: Subscription } | null
  >(null);
  const [aCancelar, setACancelar] = useState<Subscription | null>(null);
  const [aSuspender, setASuspender] = useState<Subscriber | null>(null);
  const [confirmarBorrado, setConfirmarBorrado] = useState(false);
  const [ocupado, setOcupado] = useState(false);

  const cargarSuscriptor = useCallback(async () => {
    if (!id) return;
    setCargandoSuscriptor(true);
    setErrorSuscriptor(null);
    setErrorServicio(null);
    try {
      const respuesta = await api.getSubscriber(id);
      setSuscriptor(respuesta.data);
      if (!respuesta.data) setErrorSuscriptor("La API no devolvio el suscriptor.");
    } catch (err) {
      setSuscriptor(null);
      setErrorSuscriptor(messageForError(err));
    } finally {
      setCargandoSuscriptor(false);
    }

    try {
      const estado = await api.getSubscriberStatus(id);
      setServicio(estado.data);
    } catch (err) {
      setServicio(null);
      setErrorServicio(messageForError(err));
    }
  }, [api, id]);

  const cargarSuscripciones = useCallback(async () => {
    if (!id) return;
    setCargandoSuscripciones(true);
    setErrorSuscripciones(null);
    try {
      const respuesta = await api.listSubscriptions(id);
      setSuscripciones(respuesta.data ?? []);
    } catch (err) {
      setSuscripciones([]);
      setErrorSuscripciones(messageForError(err));
    } finally {
      setCargandoSuscripciones(false);
    }
  }, [api, id]);

  const cargarDispositivos = useCallback(async () => {
    if (!id) return;
    setCargandoDispositivos(true);
    setErrorDispositivos(null);
    try {
      const respuesta = await api.listDevices(id);
      setDispositivos(respuesta.data ?? []);
    } catch (err) {
      setDispositivos([]);
      setErrorDispositivos(messageForError(err));
    } finally {
      setCargandoDispositivos(false);
    }
  }, [api, id]);

  const cargarPlanes = useCallback(async () => {
    setErrorPlanes(null);
    try {
      const respuesta = await api.listPlans();
      setPlanes(respuesta.data ?? []);
    } catch (err) {
      setPlanes([]);
      setErrorPlanes(messageForError(err));
    }
  }, [api]);

  useEffect(() => {
    void cargarSuscriptor();
    void cargarSuscripciones();
    void cargarDispositivos();
    void cargarPlanes();
  }, [cargarSuscriptor, cargarSuscripciones, cargarDispositivos, cargarPlanes]);

  const recargarTodo = () => {
    void cargarSuscriptor();
    void cargarSuscripciones();
    void cargarDispositivos();
  };

  const filasDispositivos = useMemo(
    () =>
      dispositivos.map((dispositivo) => ({
        ...dispositivo,
        suscriptorUsername: suscriptor?.username ?? null,
      })),
    [dispositivos, suscriptor],
  );

  const alternarActivacion = async () => {
    if (!suscriptor) return;
    setOcupado(true);
    try {
      if (suscriptor.status === "active") {
        await api.suspendSubscriber(suscriptor.id);
        toast.success(`${suscriptor.username} suspendido.`);
      } else {
        await api.activateSubscriber(suscriptor.id);
        toast.success(`${suscriptor.username} activado.`);
      }
      setASuspender(null);
      await cargarSuscriptor();
    } catch (err) {
      toast.error(messageForError(err));
    } finally {
      setOcupado(false);
    }
  };

  const eliminarSuscriptor = async () => {
    if (!suscriptor) return;
    setOcupado(true);
    try {
      await api.deleteSubscriber(suscriptor.id);
      toast.success(`Suscriptor ${suscriptor.username} eliminado.`);
      setConfirmarBorrado(false);
      navigate("/suscriptores");
    } catch (err) {
      toast.error(messageForError(err));
    } finally {
      setOcupado(false);
    }
  };

  const confirmarSuscripcion = async (planId: string | null, nota: string | null) => {
    if (!id || !modalSuscripcion) return;
    setOcupado(true);
    try {
      if (modalSuscripcion.modo === "crear") {
        if (!planId) return;
        await api.createSubscription(id, { plan_id: planId, renewal_note: nota });
        toast.success("Suscripcion creada.");
      } else {
        await api.renewSubscription(id, modalSuscripcion.suscripcion.id, {
          plan_id: planId,
          renewal_note: nota,
        });
        toast.success("Suscripcion renovada.");
      }
      setModalSuscripcion(null);
      await cargarSuscripciones();
      await cargarSuscriptor();
    } catch (err) {
      toast.error(messageForError(err));
    } finally {
      setOcupado(false);
    }
  };

  const cancelarSuscripcion = async () => {
    if (!id || !aCancelar) return;
    setOcupado(true);
    try {
      const respuesta = await api.cancelSubscription(id, aCancelar.id);
      toast.success(respuesta.message || "Suscripcion cancelada.");
      setACancelar(null);
      await cargarSuscripciones();
      await cargarSuscriptor();
    } catch (err) {
      toast.error(messageForError(err));
    } finally {
      setOcupado(false);
    }
  };

  const columnasSuscripciones: Column<Subscription>[] = [
    {
      key: "plan",
      header: "Plan",
      render: (fila) => {
        const precio = fila.plan ? decimalToNumber(fila.plan.price) : null;
        return (
          <div className="susc-dato">
            <span className="susc-dato-valor">{fila.plan?.name ?? fila.plan_id}</span>
            <span className="susc-dato-label">
              {fila.plan
                ? `${fila.plan.duration_days} dias${precio !== null ? ` · ${precio.toFixed(2)}` : ""}`
                : "Plan no incluido en la respuesta"}
            </span>
          </div>
        );
      },
    },
    {
      key: "inicio",
      header: "Inicio",
      width: "14%",
      render: (fila) => formatFecha(fila.starts_at),
    },
    {
      key: "caduca",
      header: "Caduca",
      width: "14%",
      render: (fila) => formatFecha(fila.expires_at),
    },
    {
      key: "estado",
      header: "Estado",
      width: "12%",
      render: (fila) => (
        <StatusBadge tone={toneForBoolean(fila.is_active)} dot>
          {fila.is_active ? "activa" : "inactiva"}
        </StatusBadge>
      ),
    },
    {
      key: "nota",
      header: "Nota",
      width: "18%",
      render: (fila) => textoOpcional(fila.renewal_note),
    },
    {
      key: "acciones",
      header: "",
      width: "190px",
      align: "right",
      render: (fila) => (
        <div className="susc-actions">
          <Button
            size="sm"
            variant="secondary"
            icon={<RotateCw size={14} />}
            onClick={() => setModalSuscripcion({ modo: "renovar", suscripcion: fila })}
          >
            Renovar
          </Button>
          {fila.is_active ? (
            <Button
              size="sm"
              variant="danger"
              icon={<XCircle size={14} />}
              onClick={() => setACancelar(fila)}
            >
              Cancelar
            </Button>
          ) : null}
        </div>
      ),
    },
  ];

  if (!id) {
    return (
      <div className="susc-page">
        <p className="susc-form-error" role="alert">
          Falta el identificador del suscriptor en la URL.
        </p>
      </div>
    );
  }

  return (
    <div className="susc-page">
      <div className="susc-detalle-top">
        <Link className="susc-back" to="/suscriptores">
          <ArrowLeft size={14} aria-hidden /> Volver a suscriptores
        </Link>
      </div>

      <header className="susc-header">
        <div>
          <h1>
            {suscriptor ? suscriptor.username : cargandoSuscriptor ? "Cargando..." : "Suscriptor"}
          </h1>
          <p className="susc-subtitle">
            {suscriptor ? (
              <StatusBadge tone={toneForSubscriberStatus(suscriptor.status)} dot>
                {suscriptor.status}
              </StatusBadge>
            ) : null}
          </p>
        </div>
        <div className="susc-actions">
          <Button
            variant="secondary"
            icon={<RefreshCw size={16} />}
            onClick={recargarTodo}
            loading={cargandoSuscriptor}
          >
            Recargar
          </Button>
          <Button
            variant="secondary"
            icon={<Pencil size={16} />}
            disabled={!suscriptor}
            onClick={() => setFormAbierto(true)}
          >
            Editar
          </Button>
          <Button
            variant="secondary"
            icon={<KeyRound size={16} />}
            disabled={!suscriptor}
            onClick={() => setPasswordAbierto(true)}
          >
            Contrasena
          </Button>
          {suscriptor?.status === "active" ? (
            <Button
              variant="secondary"
              icon={<Ban size={16} />}
              onClick={() => setASuspender(suscriptor)}
            >
              Suspender
            </Button>
          ) : (
            <Button
              variant="secondary"
              icon={<CheckCircle2 size={16} />}
              disabled={!suscriptor || ocupado}
              onClick={() => void alternarActivacion()}
            >
              Activar
            </Button>
          )}
          <Button
            variant="danger"
            icon={<Trash2 size={16} />}
            disabled={!suscriptor}
            onClick={() => setConfirmarBorrado(true)}
          >
            Eliminar
          </Button>
        </div>
      </header>

      {errorSuscriptor ? (
        <div className="susc-card">
          <p className="susc-form-error" role="alert">
            {errorSuscriptor}
          </p>
          <div>
            <Button variant="secondary" onClick={() => void cargarSuscriptor()}>
              Reintentar
            </Button>
          </div>
        </div>
      ) : null}

      <section className="susc-card">
        <div className="susc-card-head">
          <h2>Datos del suscriptor</h2>
        </div>
        {cargandoSuscriptor && !suscriptor ? (
          <p className="susc-aviso">Cargando datos...</p>
        ) : suscriptor ? (
          <Datos
            items={[
              { etiqueta: "Usuario", valor: suscriptor.username },
              { etiqueta: "Nombre", valor: textoOpcional(suscriptor.full_name) },
              { etiqueta: "Email", valor: textoOpcional(suscriptor.email) },
              { etiqueta: "Telefono", valor: textoOpcional(suscriptor.phone) },
              { etiqueta: "Cedula / ID", valor: textoOpcional(suscriptor.id_cedula) },
              {
                etiqueta: "Codigo de activacion",
                valor: (
                  <span className="susc-mono">{textoOpcional(suscriptor.activation_code)}</span>
                ),
              },
              { etiqueta: "Alta", valor: formatFechaHora(suscriptor.created_at) },
              { etiqueta: "Ultima modificacion", valor: formatFechaHora(suscriptor.updated_at) },
              { etiqueta: "Notas", valor: textoOpcional(suscriptor.notes) },
            ]}
          />
        ) : null}
      </section>

      <section className="susc-card">
        <div className="susc-card-head">
          <h2>Estado del servicio</h2>
          <p className="susc-card-note">
            Calculado por la API sobre la suscripcion vigente y las conexiones activas.
          </p>
        </div>
        {errorServicio ? (
          <p className="susc-form-error" role="alert">
            {errorServicio}
          </p>
        ) : servicio ? (
          <Datos
            items={[
              {
                etiqueta: "Servicio",
                valor: (
                  <StatusBadge tone={toneForBoolean(servicio.is_active)} dot>
                    {servicio.is_active ? "activo" : "inactivo"}
                  </StatusBadge>
                ),
              },
              {
                etiqueta: "Caduca",
                valor: formatFechaHora(servicio.subscription_expires_at),
              },
              {
                etiqueta: "Dias restantes",
                valor: servicio.days_remaining === null ? VACIO : String(servicio.days_remaining),
              },
              {
                etiqueta: "Dispositivos",
                valor: `${servicio.device_count} de ${servicio.max_devices}`,
              },
              { etiqueta: "Conexiones maximas", valor: String(servicio.max_connections) },
            ]}
          />
        ) : (
          <p className="susc-aviso">Cargando estado...</p>
        )}
      </section>

      <section className="susc-card">
        <div className="susc-card-head">
          <h2>Suscripciones</h2>
          <Button
            variant="primary"
            size="sm"
            icon={<Plus size={14} />}
            onClick={() => setModalSuscripcion({ modo: "crear" })}
          >
            Nueva suscripcion
          </Button>
        </div>
        <DataTable
          columns={columnasSuscripciones}
          rows={suscripciones}
          rowKey={(fila) => fila.id}
          loading={cargandoSuscripciones}
          error={errorSuscripciones}
          onRetry={() => void cargarSuscripciones()}
          emptyMessage="Este suscriptor no tiene ninguna suscripcion todavia."
          caption="Suscripciones del suscriptor"
        />
      </section>

      <section className="susc-card">
        <div className="susc-card-head">
          <h2>Dispositivos</h2>
          <p className="susc-card-note">
            Dispositivos registrados por este suscriptor.
          </p>
        </div>
        <DispositivosTabla
          filas={filasDispositivos}
          loading={cargandoDispositivos}
          error={errorDispositivos}
          onRetry={() => void cargarDispositivos()}
          onCambio={() => {
            void cargarDispositivos();
            void cargarSuscriptor();
          }}
          mostrarSuscriptor={false}
          emptyMessage="Este suscriptor no tiene dispositivos registrados."
          caption="Dispositivos del suscriptor"
        />
      </section>

      <SuscriptorFormModal
        open={formAbierto}
        suscriptor={suscriptor}
        onClose={() => setFormAbierto(false)}
        onGuardado={() => {
          setFormAbierto(false);
          void cargarSuscriptor();
        }}
      />

      {suscriptor ? (
        <SuscriptorPasswordModal
          open={passwordAbierto}
          subId={suscriptor.id}
          username={suscriptor.username}
          onClose={() => setPasswordAbierto(false)}
        />
      ) : null}

      <SuscripcionModal
        open={modalSuscripcion !== null}
        modo={modalSuscripcion?.modo ?? "crear"}
        planes={planes}
        planesError={errorPlanes}
        planActual={
          modalSuscripcion?.modo === "renovar" ? modalSuscripcion.suscripcion.plan : null
        }
        guardando={ocupado}
        onClose={() => setModalSuscripcion(null)}
        onConfirmar={(planId, nota) => void confirmarSuscripcion(planId, nota)}
      />

      <ConfirmDialog
        open={aCancelar !== null}
        tone="danger"
        title="Cancelar suscripcion"
        message={
          <>
            Se cancelara la suscripcion de <strong>{suscriptor?.username}</strong>
            {aCancelar?.plan ? ` al plan ${aCancelar.plan.name}` : ""} (caduca{" "}
            {formatFecha(aCancelar?.expires_at ?? null)}). El cliente se queda SIN
            servicio: se cortan al instante sus sesiones IPTV activas y deja de poder
            reproducir. El registro se conserva en el historial.
          </>
        }
        confirmLabel="Cancelar suscripcion"
        cancelLabel="Volver"
        busy={ocupado}
        onCancel={() => setACancelar(null)}
        onConfirm={() => void cancelarSuscripcion()}
      />

      <ConfirmDialog
        open={aSuspender !== null}
        title="Suspender suscriptor"
        message={
          <>
            <strong>{aSuspender?.username}</strong> pasara a estado <em>suspended</em> y
            no podra iniciar sesion ni reproducir. Es reversible.
          </>
        }
        confirmLabel="Suspender"
        busy={ocupado}
        onCancel={() => setASuspender(null)}
        onConfirm={() => void alternarActivacion()}
      />

      <ConfirmDialog
        open={confirmarBorrado}
        tone="danger"
        title="Eliminar suscriptor"
        message={
          <>
            Se eliminara <strong>{suscriptor?.username}</strong> junto con sus
            suscripciones, dispositivos y sesiones. El cliente perdera el servicio de
            inmediato y esta accion no se puede deshacer.
          </>
        }
        confirmLabel="Eliminar"
        busy={ocupado}
        onCancel={() => setConfirmarBorrado(false)}
        onConfirm={() => void eliminarSuscriptor()}
      />
    </div>
  );
}
