import { useCallback, useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Ban, CheckCircle2, Pencil, Plus, RefreshCw, Trash2 } from "lucide-react";

import { messageForError } from "../../api/errors";
import { SUBSCRIBER_STATUSES } from "../../api/types";
import type { Subscriber, SubscriberStatus } from "../../api/types";
import { useApi } from "../../auth/AuthContext";
import {
  Button,
  ConfirmDialog,
  DataTable,
  Select,
  StatusBadge,
  TextInput,
  toneForSubscriberStatus,
  useToast,
} from "../../ui/primitives";
import type { Column } from "../../ui/primitives";
import { SuscriptorFormModal } from "./SuscriptorFormModal";
import { formatFecha, textoOpcional } from "./formato";
import "./suscriptores.css";

const TAMANOS_PAGINA = [25, 50, 100, 200];

/** Espera antes de consultar para no lanzar una peticion por tecla pulsada. */
const RETARDO_BUSQUEDA_MS = 350;

type FiltroEstado = SubscriberStatus | "";

/**
 * Listado de suscriptores.
 *
 * GET /api/admin/subscribers?page=&page_size=&status=&q=  -> PaginatedResponse
 *
 * Paginacion, estado y BUSQUEDA los resuelve la API. `q` recorre toda la base
 * (username, full_name, email e id_cedula, SubscriberService.list_subscribers),
 * no la pagina cargada, asi que el total y las paginas que se muestran ya son
 * los del resultado de la busqueda.
 */
export function SuscriptoresView() {
  const api = useApi();
  const toast = useToast();
  const navigate = useNavigate();

  const [filas, setFilas] = useState<Subscriber[]>([]);
  const [total, setTotal] = useState(0);
  const [paginas, setPaginas] = useState(0);
  const [pagina, setPagina] = useState(1);
  const [tamano, setTamano] = useState(50);
  const [estado, setEstado] = useState<FiltroEstado>("");
  /** Lo que se esta escribiendo. */
  const [busqueda, setBusqueda] = useState("");
  /** Lo que ya se ha mandado a la API (busqueda con retardo aplicado). */
  const [consulta, setConsulta] = useState("");

  const [cargando, setCargando] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [formAbierto, setFormAbierto] = useState(false);
  const [enEdicion, setEnEdicion] = useState<Subscriber | null>(null);
  const [aEliminar, setAEliminar] = useState<Subscriber | null>(null);
  const [aSuspender, setASuspender] = useState<Subscriber | null>(null);
  const [ocupado, setOcupado] = useState(false);

  // Descarta respuestas de peticiones que ya no corresponden al filtro actual.
  const generacion = useRef(0);

  const cargar = useCallback(async () => {
    const actual = ++generacion.current;
    setCargando(true);
    setError(null);
    try {
      const respuesta = await api.listSubscribers({
        page: pagina,
        page_size: tamano,
        status: estado || undefined,
        q: consulta || undefined,
      });
      if (actual !== generacion.current) return;
      setFilas(respuesta.data);
      setTotal(respuesta.total);
      setPaginas(respuesta.pages);
    } catch (err) {
      if (actual !== generacion.current) return;
      setFilas([]);
      setTotal(0);
      setPaginas(0);
      setError(messageForError(err));
    } finally {
      if (actual === generacion.current) setCargando(false);
    }
  }, [api, pagina, tamano, estado, consulta]);

  useEffect(() => {
    void cargar();
  }, [cargar]);

  // Retardo del buscador: se consulta cuando el usuario deja de escribir. Al
  // cambiar el texto se vuelve a la pagina 1, porque el numero de paginas del
  // resultado anterior no tiene nada que ver con el de la busqueda nueva.
  useEffect(() => {
    const temporizador = window.setTimeout(() => {
      setConsulta(busqueda.trim());
      setPagina(1);
    }, RETARDO_BUSQUEDA_MS);
    return () => window.clearTimeout(temporizador);
  }, [busqueda]);

  const buscando = busqueda.trim() !== consulta;

  const cambiarEstado = (valor: FiltroEstado) => {
    setEstado(valor);
    setPagina(1);
  };

  const cambiarTamano = (valor: number) => {
    setTamano(valor);
    setPagina(1);
  };

  const alternarActivacion = async (suscriptor: Subscriber) => {
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
      await cargar();
    } catch (err) {
      toast.error(messageForError(err));
    } finally {
      setOcupado(false);
    }
  };

  const eliminar = async () => {
    if (!aEliminar) return;
    setOcupado(true);
    try {
      await api.deleteSubscriber(aEliminar.id);
      toast.success(`Suscriptor ${aEliminar.username} eliminado.`);
      setAEliminar(null);
      await cargar();
    } catch (err) {
      toast.error(messageForError(err));
    } finally {
      setOcupado(false);
    }
  };

  const columnas: Column<Subscriber>[] = [
    {
      key: "usuario",
      header: "Usuario",
      render: (fila) => (
        <div className="susc-dato">
          <span className="susc-dato-valor">{fila.username}</span>
          <span className="susc-dato-label">{textoOpcional(fila.full_name)}</span>
        </div>
      ),
    },
    {
      key: "contacto",
      header: "Contacto",
      width: "22%",
      render: (fila) => (
        <div className="susc-dato">
          <span className="susc-dato-valor">{textoOpcional(fila.email)}</span>
          <span className="susc-dato-label">{textoOpcional(fila.phone)}</span>
        </div>
      ),
    },
    {
      key: "cedula",
      header: "Cedula",
      width: "12%",
      render: (fila) => <span className="susc-mono">{textoOpcional(fila.id_cedula)}</span>,
    },
    {
      key: "estado",
      header: "Estado",
      width: "12%",
      render: (fila) => (
        <StatusBadge tone={toneForSubscriberStatus(fila.status)} dot>
          {fila.status}
        </StatusBadge>
      ),
    },
    {
      key: "alta",
      header: "Alta",
      width: "12%",
      render: (fila) => formatFecha(fila.created_at),
    },
    {
      key: "acciones",
      header: "",
      width: "210px",
      align: "right",
      render: (fila) => (
        <div
          className="susc-actions"
          onClick={(evento) => evento.stopPropagation()}
          role="presentation"
        >
          <Button
            size="sm"
            variant="secondary"
            icon={<Pencil size={14} />}
            aria-label={`Editar ${fila.username}`}
            onClick={() => {
              setEnEdicion(fila);
              setFormAbierto(true);
            }}
          />
          {fila.status === "active" ? (
            <Button
              size="sm"
              variant="secondary"
              icon={<Ban size={14} />}
              aria-label={`Suspender ${fila.username}`}
              onClick={() => setASuspender(fila)}
            />
          ) : (
            <Button
              size="sm"
              variant="secondary"
              icon={<CheckCircle2 size={14} />}
              aria-label={`Activar ${fila.username}`}
              disabled={ocupado}
              onClick={() => void alternarActivacion(fila)}
            />
          )}
          <Button
            size="sm"
            variant="danger"
            icon={<Trash2 size={14} />}
            aria-label={`Eliminar ${fila.username}`}
            onClick={() => setAEliminar(fila)}
          />
        </div>
      ),
    },
  ];

  const desde = total === 0 ? 0 : (pagina - 1) * tamano + 1;
  const hasta = Math.min(pagina * tamano, total);

  return (
    <div className="susc-page">
      <header className="susc-header">
        <div>
          <h1>Suscriptores</h1>
          <p className="susc-subtitle">
            Clientes del servicio. Pulsa una fila para ver su detalle, suscripciones y
            dispositivos.
          </p>
        </div>
        <div className="susc-actions">
          <Button
            variant="secondary"
            icon={<RefreshCw size={16} />}
            onClick={() => void cargar()}
            loading={cargando}
          >
            Recargar
          </Button>
          <Button
            variant="primary"
            icon={<Plus size={16} />}
            onClick={() => {
              setEnEdicion(null);
              setFormAbierto(true);
            }}
          >
            Nuevo suscriptor
          </Button>
        </div>
      </header>

      <div className="susc-toolbar">
        <div className="susc-toolbar-field susc-grow">
          <label className="susc-toolbar-label" htmlFor="susc-busqueda">
            Buscar
          </label>
          <TextInput
            id="susc-busqueda"
            type="search"
            value={busqueda}
            placeholder="Usuario, nombre, email o cedula"
            maxLength={128}
            onChange={(evento) => setBusqueda(evento.target.value)}
          />
        </div>
        <div className="susc-toolbar-field">
          <label className="susc-toolbar-label" htmlFor="susc-estado">
            Estado
          </label>
          <Select
            id="susc-estado"
            value={estado}
            onChange={(evento) => cambiarEstado(evento.target.value as FiltroEstado)}
          >
            <option value="">Todos</option>
            {SUBSCRIBER_STATUSES.map((valor) => (
              <option key={valor} value={valor}>
                {valor}
              </option>
            ))}
          </Select>
        </div>
        <div className="susc-toolbar-field">
          <label className="susc-toolbar-label" htmlFor="susc-tamano">
            Por pagina
          </label>
          <Select
            id="susc-tamano"
            value={tamano}
            onChange={(evento) => cambiarTamano(Number(evento.target.value))}
          >
            {TAMANOS_PAGINA.map((valor) => (
              <option key={valor} value={valor}>
                {valor}
              </option>
            ))}
          </Select>
        </div>
        <p className="susc-toolbar-hint">
          La busqueda, el estado y la paginacion los resuelve la API: se busca sobre
          <strong> todos</strong> los suscriptores por usuario, nombre, email y cedula.
          El telefono no entra en la busqueda del servidor.
        </p>
      </div>

      <DataTable
        columns={columnas}
        rows={filas}
        rowKey={(fila) => fila.id}
        loading={cargando}
        error={error}
        onRetry={() => void cargar()}
        caption="Suscriptores"
        emptyMessage={
          consulta
            ? `Ningun suscriptor coincide con "${consulta}".`
            : "No hay suscriptores con estos filtros."
        }
        onRowClick={(fila) => navigate(`/suscriptores/${fila.id}`)}
        footer={
          <div className="susc-pager">
            <span className="susc-pager-info">
              {total === 0
                ? "Sin resultados"
                : `${desde}-${hasta} de ${total} · pagina ${pagina} de ${Math.max(paginas, 1)}`}
              {buscando
                ? " · actualizando busqueda..."
                : consulta
                  ? ` · filtrado por "${consulta}"`
                  : ""}
            </span>
            <div className="susc-pager-buttons">
              <Button
                size="sm"
                variant="secondary"
                disabled={pagina <= 1 || cargando}
                onClick={() => setPagina((valor) => Math.max(1, valor - 1))}
              >
                Anterior
              </Button>
              <Button
                size="sm"
                variant="secondary"
                disabled={cargando || pagina >= paginas}
                onClick={() => setPagina((valor) => valor + 1)}
              >
                Siguiente
              </Button>
            </div>
          </div>
        }
      />

      <SuscriptorFormModal
        open={formAbierto}
        suscriptor={enEdicion}
        onClose={() => setFormAbierto(false)}
        onGuardado={() => {
          setFormAbierto(false);
          void cargar();
        }}
      />

      <ConfirmDialog
        open={aSuspender !== null}
        title="Suspender suscriptor"
        message={
          <>
            <strong>{aSuspender?.username}</strong> pasara a estado <em>suspended</em> y
            no podra iniciar sesion ni reproducir. Es reversible: puedes reactivarlo
            despues.
          </>
        }
        confirmLabel="Suspender"
        busy={ocupado}
        onCancel={() => setASuspender(null)}
        onConfirm={() => {
          if (aSuspender) void alternarActivacion(aSuspender);
        }}
      />

      <ConfirmDialog
        open={aEliminar !== null}
        tone="danger"
        title="Eliminar suscriptor"
        message={
          <>
            Se eliminara <strong>{aEliminar?.username}</strong> junto con sus
            suscripciones, dispositivos y sesiones. El cliente perdera el servicio de
            inmediato y esta accion no se puede deshacer.
          </>
        }
        confirmLabel="Eliminar"
        busy={ocupado}
        onCancel={() => setAEliminar(null)}
        onConfirm={() => void eliminar()}
      />
    </div>
  );
}
