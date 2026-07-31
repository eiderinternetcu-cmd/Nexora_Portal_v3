import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { AlertTriangle, Eye, EyeOff, Info, Radio, RefreshCw, Satellite } from "lucide-react";

import { ApiError, messageForError } from "../../api/errors";
import type {
  Channel,
  ChannelSecrets,
  FlussonicHealthOut,
  FlussonicStreamItem,
  StreamStatus,
} from "../../api/types";
import { useApi, useAuth } from "../../auth/AuthContext";
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
 *   GET /api/admin/channels                     -> ChannelAdminOut[]  (enmascarado)
 *   GET /api/admin/channels/{id}/secrets        -> ChannelSecretsOut   (admin, auditado)
 *   GET /api/admin/channels/{id}/stream-status  -> StreamStatusOut
 *   GET /api/admin/flussonic/health             -> FlussonicHealthOut
 *   GET /api/admin/flussonic/streams            -> FlussonicStreamItem[]  (admin)
 *
 * SOLO LECTURA: la API admin no expone crear, editar ni borrar canales
 * (app/api/admin/channels.py solo declara GET). No se simula edicion.
 *
 * CAMPOS SENSIBLES
 * ----------------
 * Antes esta vista se limitaba a NO PINTAR `stream_key` y `source_url`, pero la
 * respuesta de red si los llevaba: cualquiera con acceso al panel los veia en
 * devtools. Ya no: el listado y el detalle llegan enmascarados desde la API
 * (`stream_key_masked`, `source_url_masked`).
 *
 * Los valores reales se piden a /secrets y SOLO al pulsar "Revelar":
 *   - es admin (un reseller recibe 403),
 *   - queda un evento `channel.secrets_reveal` en el log de auditoria.
 * Por eso la llamada no puede colgarse de un efecto de carga: cada revelado es
 * una accion deliberada de una persona, y asi es como se lee luego el rastro.
 */

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
  const { isAdmin } = useAuth();

  const [canales, setCanales] = useState<Channel[]>([]);
  const [cargandoCanales, setCargandoCanales] = useState(true);
  const [errorCanales, setErrorCanales] = useState<string | null>(null);

  const [salud, setSalud] = useState<Recurso<FlussonicHealthOut>>(recursoInicial);
  const [streams, setStreams] = useState<Recurso<FlussonicStreamItem[]>>(recursoInicial);

  const [busqueda, setBusqueda] = useState("");
  const [filtroEstado, setFiltroEstado] = useState<"todos" | "activos" | "inactivos">("todos");

  const [seleccionado, setSeleccionado] = useState<Channel | null>(null);
  /** Valores reales del canal abierto. Solo se rellena al pulsar "Revelar". */
  const [secretos, setSecretos] = useState<Recurso<ChannelSecrets>>({
    datos: null,
    cargando: false,
    error: null,
  });
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

    // /flussonic/streams es admin: cada fila trae el nombre del stream (que es
    // el stream_key) y una hls_url ya reproducible. Para un reseller ni se pide,
    // porque un 403 esperado no es un error que mostrarle.
    if (!isAdmin) {
      setStreams({ datos: null, cargando: false, error: null });
    } else {
      setStreams((previo) => ({ ...previo, cargando: true }));
    }

    const [resSalud, resStreams] = await Promise.allSettled([
      api.get<FlussonicHealthOut>("/flussonic/health"),
      isAdmin
        ? api.get<FlussonicStreamItem[]>("/flussonic/streams")
        : Promise.resolve(null),
    ]);
    if (!montado.current) return;

    setSalud(
      resSalud.status === "fulfilled"
        ? { datos: resSalud.value, cargando: false, error: null }
        : { datos: null, cargando: false, error: mensajeFlussonic(resSalud.reason) },
    );
    if (isAdmin) {
      setStreams(
        resStreams.status === "fulfilled"
          ? {
              datos: (resStreams.value as FlussonicStreamItem[]) ?? [],
              cargando: false,
              error: null,
            }
          : { datos: null, cargando: false, error: mensajeFlussonic(resStreams.reason) },
      );
    }
  }, [api, isAdmin]);

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
    // Abrir la ficha NO revela nada: no se llama a /secrets aqui a proposito.
    setSecretos({ datos: null, cargando: false, error: null });
    setEstadoStream({ datos: null, cargando: false, error: null });
  };

  const cerrarDetalle = () => {
    setSeleccionado(null);
    setSecretos({ datos: null, cargando: false, error: null });
    setEstadoStream({ datos: null, cargando: false, error: null });
  };

  /**
   * Pide los valores reales. Cada llamada deja un evento en el log de auditoria,
   * asi que solo se dispara desde el boton, nunca desde un efecto.
   */
  const revelarSecretos = async (canal: Channel) => {
    setSecretos({ datos: null, cargando: true, error: null });
    try {
      const datos = await api.getChannelSecrets(canal.id);
      if (!montado.current) return;
      setSecretos({ datos, cargando: false, error: null });
    } catch (err) {
      if (!montado.current) return;
      const mensaje =
        err instanceof ApiError && err.status === 403
          ? "Solo un administrador puede ver estos valores."
          : messageForError(err);
      setSecretos({ datos: null, cargando: false, error: mensaje });
    }
  };

  const ocultarSecretos = () => setSecretos({ datos: null, cargando: false, error: null });

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

  const columnasStreams: Column<FlussonicStreamItem>[] = [
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
          (no hay alta, edicion ni baja). La clave de stream y la URL de origen llegan
          enmascaradas desde la API —ni siquiera viajan en la respuesta— porque la URL
          puede llevar credenciales del proveedor y la clave permite reproducir saltandose
          el control de suscripciones. Un administrador puede revelarlas canal a canal, y
          queda registrado.
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

      {/*
        Inventario de streams: solo admin. Cada fila lleva el nombre del stream
        (que es el stream_key) y una URL HLS reproducible, o sea el mismo secreto
        que el listado de canales enmascara — taparlo alli y dejarlo abierto aqui
        no serviria de nada.
      */}
      {isAdmin ? (
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
      ) : null}

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
                <span className="can-detalle-valor can-mono">
                  {secretos.datos ? (
                    (secretos.datos.source_url ?? (
                      <span className="can-vacio">sin configurar</span>
                    ))
                  ) : seleccionado.source_url_masked === null ? (
                    <span className="can-vacio">sin configurar</span>
                  ) : (
                    seleccionado.source_url_masked
                  )}
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
                  {secretos.datos ? (
                    <span className="can-mono">{secretos.datos.stream_key}</span>
                  ) : (
                    <span className="can-secreto-oculto">
                      {seleccionado.stream_key_masked || "(vacia)"}
                    </span>
                  )}
                  {isAdmin ? (
                    <Button
                      size="sm"
                      variant="ghost"
                      loading={secretos.cargando}
                      icon={secretos.datos ? <EyeOff size={15} /> : <Eye size={15} />}
                      onClick={() =>
                        secretos.datos ? ocultarSecretos() : void revelarSecretos(seleccionado)
                      }
                    >
                      {secretos.datos ? "Ocultar" : "Revelar"}
                    </Button>
                  ) : null}
                </span>
              </div>
            </div>

            {isAdmin ? (
              <p className="can-nota">
                <Info size={16} aria-hidden />
                <span>
                  {secretos.datos
                    ? "Revelado. Este acceso ha quedado registrado en el log de auditoria con tu usuario, el canal y la hora."
                    : "La clave de stream y la URL de origen se muestran enmascaradas. Revelarlas queda registrado en el log de auditoria."}
                </span>
              </p>
            ) : (
              <p className="can-nota">
                <Info size={16} aria-hidden />
                <span>
                  La clave de stream y la URL de origen solo las puede ver un
                  administrador: con ellas se puede construir una URL de reproduccion
                  que se salta el control de suscripciones.
                </span>
              </p>
            )}

            {secretos.error ? (
              <Aviso tono="error" mensaje={secretos.error} />
            ) : null}

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
