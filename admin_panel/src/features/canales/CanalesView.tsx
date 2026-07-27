import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { AlertTriangle, Eye, EyeOff, Info, Radio, RefreshCw, Satellite } from "lucide-react";

import { ApiError, messageForError } from "../../api/errors";
import type { Channel, StreamStatus } from "../../api/types";
import { useApi } from "../../auth/AuthContext";
import {
  Button,
  DataTable,
  Modal,
  Select,
  StatusBadge,
  TextInput,
  toneForBoolean,
} from "../../ui/primitives";
import type { Column } from "../../ui/primitives";
import "./canales.css";

/**
 * CANALES — pantalla CONSULTIVA.
 *
 * Endpoints (canales y flussonic vienen SIN envoltura .data):
 *   GET /api/admin/channels                     -> ChannelAdminOut[]
 *   GET /api/admin/channels/{id}/stream-status  -> StreamStatusOut
 *   GET /api/admin/flussonic/health             -> FlussonicHealthOut
 *   GET /api/admin/flussonic/streams            -> FlussonicStreamItem[]
 *
 * SOLO LECTURA: la API admin no expone crear, editar ni borrar canales
 * (app/api/admin/channels.py solo declara GET). No se simula edicion.
 *
 * CAMPOS SENSIBLES:
 *   - `stream_key` va oculto tras un boton de revelar y nunca en el listado:
 *     con el host de Flussonic permite construir una URL de reproduccion
 *     saltandose el gate de entitlements.
 *   - `source_url` NO se pinta nunca: en fuentes de tipo pull suele llevar
 *     usuario y contrasena del origen embebidos. Solo se indica si esta puesta.
 */

/** `FlussonicHealthOut` (app/api/admin/flussonic.py). No existe en src/api/types.ts. */
type FlussonicHealth = {
  configured: boolean;
  reachable: boolean;
  /** Solo host:puerto; la API se encarga de no incluir credenciales. */
  base_url_host: string;
};

/** `FlussonicStreamItem` (app/api/admin/flussonic.py). Tampoco esta en types.ts. */
type FlussonicStream = {
  name: string;
  alive: boolean;
  client_count: number;
  hls_url: string;
};

type Recurso<T> = {
  datos: T | null;
  cargando: boolean;
  error: string | null;
};

const recursoInicial = <T,>(): Recurso<T> => ({ datos: null, cargando: true, error: null });

/**
 * 503 y 404 de los endpoints de Flussonic tienen un significado concreto que
 * `messageForError` convierte en un generico poco util.
 */
const mensajeFlussonic = (error: unknown) => {
  if (error instanceof ApiError) {
    if (error.status === 503) return "Flussonic no esta configurado en la API.";
    if (error.status === 502) return "La API no pudo hablar con Flussonic.";
    if (error.status === 404) return "Flussonic no conoce ese stream.";
  }
  return messageForError(error);
};

const formatearFecha = (iso: string) => {
  const fecha = new Date(iso);
  return Number.isNaN(fecha.getTime()) ? iso : fecha.toLocaleString("es-ES");
};

function Aviso({
  tono,
  mensaje,
  onReintentar,
}: {
  tono: "cargando" | "vacio" | "error";
  mensaje: string;
  onReintentar?: () => void;
}) {
  return (
    <div className={`can-aviso can-aviso-${tono}`} role={tono === "error" ? "alert" : undefined}>
      {tono === "error" ? <AlertTriangle size={18} aria-hidden /> : null}
      <span>{mensaje}</span>
      {tono === "error" && onReintentar ? (
        <Button size="sm" onClick={onReintentar}>
          Reintentar
        </Button>
      ) : null}
    </div>
  );
}

export function CanalesView() {
  const api = useApi();

  const [canales, setCanales] = useState<Channel[]>([]);
  const [cargandoCanales, setCargandoCanales] = useState(true);
  const [errorCanales, setErrorCanales] = useState<string | null>(null);

  const [salud, setSalud] = useState<Recurso<FlussonicHealth>>(recursoInicial);
  const [streams, setStreams] = useState<Recurso<FlussonicStream[]>>(recursoInicial);

  const [busqueda, setBusqueda] = useState("");
  const [filtroEstado, setFiltroEstado] = useState<"todos" | "activos" | "inactivos">("todos");

  const [seleccionado, setSeleccionado] = useState<Channel | null>(null);
  const [claveVisible, setClaveVisible] = useState(false);
  const [estadoStream, setEstadoStream] = useState<Recurso<StreamStatus>>({
    datos: null,
    cargando: false,
    error: null,
  });

  const montado = useRef(true);
  useEffect(() => {
    montado.current = true;
    return () => {
      montado.current = false;
    };
  }, []);

  const cargarCanales = useCallback(async () => {
    setCargandoCanales(true);
    try {
      // Array desnudo: sin .data.
      const filas = await api.listChannels();
      if (!montado.current) return;
      setCanales(filas);
      setErrorCanales(null);
    } catch (err) {
      if (!montado.current) return;
      setCanales([]);
      setErrorCanales(messageForError(err));
    } finally {
      if (montado.current) setCargandoCanales(false);
    }
  }, [api]);

  const cargarFlussonic = useCallback(async () => {
    setSalud((previo) => ({ ...previo, cargando: true }));
    setStreams((previo) => ({ ...previo, cargando: true }));

    const [resSalud, resStreams] = await Promise.allSettled([
      api.get<FlussonicHealth>("/flussonic/health"),
      api.get<FlussonicStream[]>("/flussonic/streams"),
    ]);
    if (!montado.current) return;

    setSalud(
      resSalud.status === "fulfilled"
        ? { datos: resSalud.value, cargando: false, error: null }
        : { datos: null, cargando: false, error: mensajeFlussonic(resSalud.reason) },
    );
    setStreams(
      resStreams.status === "fulfilled"
        ? { datos: resStreams.value, cargando: false, error: null }
        : { datos: null, cargando: false, error: mensajeFlussonic(resStreams.reason) },
    );
  }, [api]);

  useEffect(() => {
    void cargarCanales();
  }, [cargarCanales]);

  useEffect(() => {
    void cargarFlussonic();
  }, [cargarFlussonic]);

  const visibles = useMemo(() => {
    const texto = busqueda.trim().toLowerCase();
    return canales.filter((canal) => {
      if (filtroEstado === "activos" && !canal.is_active) return false;
      if (filtroEstado === "inactivos" && canal.is_active) return false;
      if (!texto) return true;
      return [canal.name, canal.channel_key, canal.category ?? "", String(canal.number)].some(
        (valor) => valor.toLowerCase().includes(texto),
      );
    });
  }, [canales, busqueda, filtroEstado]);

  const abrirDetalle = (canal: Channel) => {
    setSeleccionado(canal);
    setClaveVisible(false);
    setEstadoStream({ datos: null, cargando: false, error: null });
  };

  const cerrarDetalle = () => {
    setSeleccionado(null);
    setClaveVisible(false);
    setEstadoStream({ datos: null, cargando: false, error: null });
  };

  const consultarEstadoStream = async (canal: Channel) => {
    setEstadoStream({ datos: null, cargando: true, error: null });
    try {
      const estado = await api.getChannelStreamStatus(canal.id);
      if (!montado.current) return;
      setEstadoStream({ datos: estado, cargando: false, error: null });
    } catch (err) {
      if (!montado.current) return;
      setEstadoStream({ datos: null, cargando: false, error: mensajeFlussonic(err) });
    }
  };

  const columnasCanales: Column<Channel>[] = [
    {
      key: "numero",
      header: "N.",
      width: "70px",
      align: "right",
      render: (fila) => <span className="can-numero">{fila.number}</span>,
    },
    { key: "nombre", header: "Canal", render: (fila) => fila.name },
    {
      key: "clave",
      header: "Clave publica",
      width: "180px",
      render: (fila) => <span className="can-mono">{fila.channel_key}</span>,
    },
    {
      key: "categoria",
      header: "Categoria",
      width: "150px",
      render: (fila) =>
        fila.category ? fila.category : <span className="can-vacio">sin categoria</span>,
    },
    {
      key: "origen",
      header: "Origen",
      width: "120px",
      render: (fila) => <span className="can-mono">{fila.source_type}</span>,
    },
    {
      key: "suscripcion",
      header: "Suscripcion",
      width: "130px",
      render: (fila) => (
        <StatusBadge tone={fila.requires_subscription ? "info" : "neutral"}>
          {fila.requires_subscription ? "requerida" : "libre"}
        </StatusBadge>
      ),
    },
    {
      key: "estado",
      header: "Estado",
      width: "110px",
      render: (fila) => (
        <StatusBadge tone={toneForBoolean(fila.is_active)} dot>
          {fila.is_active ? "activo" : "inactivo"}
        </StatusBadge>
      ),
    },
  ];

  const columnasStreams: Column<FlussonicStream>[] = [
    { key: "nombre", header: "Stream", render: (fila) => <span className="can-mono">{fila.name}</span> },
    {
      key: "vivo",
      header: "Emitiendo",
      width: "130px",
      render: (fila) => (
        <StatusBadge tone={toneForBoolean(fila.alive)} dot>
          {fila.alive ? "en vivo" : "caido"}
        </StatusBadge>
      ),
    },
    {
      key: "clientes",
      header: "Clientes",
      width: "110px",
      align: "right",
      render: (fila) => <span className="can-numero">{fila.client_count}</span>,
    },
    {
      key: "hls",
      header: "URL HLS",
      render: (fila) => <span className="can-mono">{fila.hls_url}</span>,
    },
  ];

  const streamsFlussonic = streams.datos ?? [];

  return (
    <div className="can">
      <div className="can-barra">
        <div className="can-barra-izq">
          <div className="can-buscador">
            <TextInput
              type="search"
              value={busqueda}
              onChange={(evento) => setBusqueda(evento.target.value)}
              placeholder="Buscar por nombre, clave, categoria o numero"
              aria-label="Buscar canales"
            />
          </div>
          <Select
            value={filtroEstado}
            onChange={(evento) =>
              setFiltroEstado(evento.target.value as "todos" | "activos" | "inactivos")
            }
            aria-label="Filtrar por estado"
          >
            <option value="todos">Todos</option>
            <option value="activos">Solo activos</option>
            <option value="inactivos">Solo inactivos</option>
          </Select>
          <StatusBadge tone={errorCanales ? "neutral" : "info"} dot>
            {errorCanales ? "sin datos" : `${visibles.length} de ${canales.length} canales`}
          </StatusBadge>
        </div>

        <div className="can-barra-der">
          <Button
            variant="secondary"
            size="sm"
            icon={<RefreshCw size={16} />}
            loading={cargandoCanales || salud.cargando || streams.cargando}
            onClick={() => {
              void cargarCanales();
              void cargarFlussonic();
            }}
          >
            Actualizar
          </Button>
        </div>
      </div>

      <p className="can-nota">
        <Info size={16} aria-hidden />
        <span>
          Pantalla de consulta: la API de administracion solo expone lectura de canales
          (no hay alta, edicion ni baja). La clave de stream se oculta por defecto y la
          URL de origen no se muestra porque puede llevar credenciales del proveedor.
        </span>
      </p>

      <DataTable
        columns={columnasCanales}
        rows={visibles}
        rowKey={(fila) => fila.id}
        loading={cargandoCanales && canales.length === 0}
        error={errorCanales}
        onRetry={() => void cargarCanales()}
        onRowClick={abrirDetalle}
        caption="Catalogo de canales"
        emptyMessage={
          canales.length > 0
            ? "Ningun canal coincide con el filtro."
            : "El catalogo no tiene canales."
        }
      />

      <section className="can-panel" aria-labelledby="can-flussonic-titulo">
        <div className="can-panel-cabecera">
          <h2 className="can-panel-titulo" id="can-flussonic-titulo">
            <Satellite size={16} aria-hidden />
            Estado de Flussonic
          </h2>
        </div>

        {salud.error ? (
          <Aviso tono="error" mensaje={salud.error} onReintentar={() => void cargarFlussonic()} />
        ) : salud.cargando && !salud.datos ? (
          <Aviso tono="cargando" mensaje="Consultando Flussonic..." />
        ) : salud.datos ? (
          <div className="can-salud">
            <span className="can-salud-dato">
              <span className="can-salud-etiqueta">Configurado</span>
              <StatusBadge tone={salud.datos.configured ? "success" : "neutral"} dot>
                {salud.datos.configured ? "si" : "no"}
              </StatusBadge>
            </span>
            <span className="can-salud-dato">
              <span className="can-salud-etiqueta">Alcanzable</span>
              <StatusBadge
                tone={
                  !salud.datos.configured
                    ? "neutral"
                    : salud.datos.reachable
                      ? "success"
                      : "danger"
                }
                dot
              >
                {!salud.datos.configured ? "sin comprobar" : salud.datos.reachable ? "si" : "NO"}
              </StatusBadge>
            </span>
            <span className="can-salud-dato">
              <span className="can-salud-etiqueta">Host</span>
              <span className="can-mono">{salud.datos.base_url_host || "sin host"}</span>
            </span>
          </div>
        ) : null}
      </section>

      <section className="can-panel" aria-labelledby="can-streams-titulo">
        <div className="can-panel-cabecera">
          <h2 className="can-panel-titulo" id="can-streams-titulo">
            <Radio size={16} aria-hidden />
            Streams en Flussonic
          </h2>
          <span className="can-vacio">
            {streams.error ? "" : `${streamsFlussonic.length} streams`}
          </span>
        </div>

        <DataTable
          columns={columnasStreams}
          rows={streamsFlussonic}
          rowKey={(fila) => fila.name}
          loading={streams.cargando && !streams.datos}
          error={streams.error}
          onRetry={() => void cargarFlussonic()}
          caption="Streams publicados en Flussonic"
          emptyMessage="Flussonic no reporta ningun stream."
        />
      </section>

      <Modal
        open={seleccionado !== null}
        onClose={cerrarDetalle}
        size="lg"
        title={seleccionado ? `${seleccionado.number} · ${seleccionado.name}` : "Canal"}
        footer={
          <Button variant="ghost" onClick={cerrarDetalle}>
            Cerrar
          </Button>
        }
      >
        {seleccionado ? (
          <>
            <div className="can-detalle">
              <div className="can-detalle-fila">
                <span className="can-detalle-etiqueta">Clave publica (channel_key)</span>
                <span className="can-detalle-valor can-mono">{seleccionado.channel_key}</span>
              </div>
              <div className="can-detalle-fila">
                <span className="can-detalle-etiqueta">Identificador</span>
                <span className="can-detalle-valor can-mono">{seleccionado.id}</span>
              </div>
              <div className="can-detalle-fila">
                <span className="can-detalle-etiqueta">Categoria</span>
                <span className="can-detalle-valor">
                  {seleccionado.category ?? <span className="can-vacio">sin categoria</span>}
                </span>
              </div>
              <div className="can-detalle-fila">
                <span className="can-detalle-etiqueta">Estado</span>
                <span className="can-detalle-valor">
                  <StatusBadge tone={toneForBoolean(seleccionado.is_active)} dot>
                    {seleccionado.is_active ? "activo" : "inactivo"}
                  </StatusBadge>
                </span>
              </div>
              <div className="can-detalle-fila">
                <span className="can-detalle-etiqueta">Requiere suscripcion</span>
                <span className="can-detalle-valor">
                  {seleccionado.requires_subscription ? "si" : "no"}
                </span>
              </div>
              <div className="can-detalle-fila">
                <span className="can-detalle-etiqueta">Tipo de origen</span>
                <span className="can-detalle-valor can-mono">{seleccionado.source_type}</span>
              </div>
              <div className="can-detalle-fila">
                <span className="can-detalle-etiqueta">URL de origen</span>
                <span className="can-detalle-valor can-vacio">
                  {seleccionado.source_url
                    ? "configurada (no se muestra: puede contener credenciales)"
                    : "sin configurar"}
                </span>
              </div>
              <div className="can-detalle-fila">
                <span className="can-detalle-etiqueta">EPG</span>
                <span className="can-detalle-valor can-mono">
                  {seleccionado.epg_id ?? <span className="can-vacio">sin EPG</span>}
                </span>
              </div>
              <div className="can-detalle-fila">
                <span className="can-detalle-etiqueta">Alta</span>
                <span className="can-detalle-valor">{formatearFecha(seleccionado.created_at)}</span>
              </div>
              <div className="can-detalle-fila">
                <span className="can-detalle-etiqueta">Ultima modificacion</span>
                <span className="can-detalle-valor">{formatearFecha(seleccionado.updated_at)}</span>
              </div>
              <div className="can-detalle-fila">
                <span className="can-detalle-etiqueta">Clave de stream</span>
                <span className="can-detalle-valor can-secreto">
                  {claveVisible ? (
                    <span className="can-mono">{seleccionado.stream_key}</span>
                  ) : (
                    <span className="can-secreto-oculto">••••••••••</span>
                  )}
                  <Button
                    size="sm"
                    variant="ghost"
                    icon={claveVisible ? <EyeOff size={15} /> : <Eye size={15} />}
                    onClick={() => setClaveVisible((visible) => !visible)}
                  >
                    {claveVisible ? "Ocultar" : "Mostrar"}
                  </Button>
                </span>
              </div>
            </div>

            <div className="can-bloque">
              <h3>Estado del stream en Flussonic</h3>
              {estadoStream.error ? (
                <Aviso
                  tono="error"
                  mensaje={estadoStream.error}
                  onReintentar={() => void consultarEstadoStream(seleccionado)}
                />
              ) : estadoStream.datos ? (
                <div className="can-estado-stream">
                  <span className="can-salud-dato">
                    <span className="can-salud-etiqueta">Emitiendo</span>
                    <StatusBadge tone={toneForBoolean(estadoStream.datos.alive)} dot>
                      {estadoStream.datos.alive ? "en vivo" : "caido"}
                    </StatusBadge>
                  </span>
                  <span className="can-salud-dato">
                    <span className="can-salud-etiqueta">Entrada</span>
                    <StatusBadge tone={toneForBoolean(estadoStream.datos.input_alive)} dot>
                      {estadoStream.datos.input_alive ? "recibiendo" : "sin senal"}
                    </StatusBadge>
                  </span>
                  <span className="can-salud-dato">
                    <span className="can-salud-etiqueta">Clientes</span>
                    <span className="can-numero">{estadoStream.datos.client_count}</span>
                  </span>
                </div>
              ) : (
                <Button
                  size="sm"
                  loading={estadoStream.cargando}
                  onClick={() => void consultarEstadoStream(seleccionado)}
                >
                  Consultar estado
                </Button>
              )}
            </div>
          </>
        ) : null}
      </Modal>
    </div>
  );
}
