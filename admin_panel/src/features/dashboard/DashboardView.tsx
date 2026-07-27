import { useCallback, useEffect, useRef, useState } from "react";
import {
  Activity,
  AlertTriangle,
  Database,
  HardDrive,
  Pause,
  Play,
  Radio,
  RefreshCw,
  Server,
} from "lucide-react";

import { messageForError } from "../../api/errors";
import type {
  ActiveAlertsResponse,
  NodeHealth,
  SystemMetrics,
} from "../../api/types";
import { useApi } from "../../auth/AuthContext";
import { Button, StatusBadge } from "../../ui/primitives";
import type { BadgeTone } from "../../ui/primitives";
import "./dashboard.css";

/**
 * DASHBOARD — la pantalla que se mira cuando algo va mal.
 *
 * Endpoints (todos SIN envoltura .data):
 *   GET /api/admin/metrics       -> SystemMetrics
 *   GET /api/admin/nodes/health  -> NodeHealth[]
 *   GET /api/admin/alerts        -> { active: [...] }
 *
 * Cada bloque tiene su propio estado de carga/error: que /alerts falle no puede
 * borrar las metricas ni fingir que no hay nodos caidos.
 */

const INTERVALO_MS = 30_000;

/** Estado de un bloque. `recibidoEn` es la hora real del ultimo dato bueno. */
type Recurso<T> = {
  datos: T | null;
  cargando: boolean;
  error: string | null;
  recibidoEn: Date | null;
};

const recursoInicial = <T,>(): Recurso<T> => ({
  datos: null,
  cargando: true,
  error: null,
  recibidoEn: null,
});

/**
 * Vuelca el resultado de una peticion en el estado del bloque. Si falla,
 * CONSERVA el ultimo dato bueno y su hora: la pantalla nunca debe aparentar
 * que unos datos viejos son de ahora mismo.
 */
const aplicar = <T, R>(
  previo: Recurso<R>,
  resultado: PromiseSettledResult<T>,
  ahora: Date,
  mapear: (valor: T) => R,
): Recurso<R> =>
  resultado.status === "fulfilled"
    ? { datos: mapear(resultado.value), cargando: false, error: null, recibidoEn: ahora }
    : {
        datos: previo.datos,
        cargando: false,
        error: messageForError(resultado.reason),
        recibidoEn: previo.recibidoEn,
      };

/** Alerta operativa de /alerts. La API no la tipa, asi que se normaliza aqui. */
type AlertaActiva = {
  clave: string;
  tipo: string;
  identificador: string;
  estado: string;
  detalle: string | null;
};

const normalizarAlerta = (valor: unknown, indice: number): AlertaActiva => {
  const bruto = (valor && typeof valor === "object" ? valor : {}) as Record<string, unknown>;
  const tipo = typeof bruto.kind === "string" ? bruto.kind : "desconocido";
  const identificador = typeof bruto.id === "string" ? bruto.id : "";
  return {
    clave: `${tipo}:${identificador}:${indice}`,
    tipo,
    identificador,
    estado: typeof bruto.status === "string" ? bruto.status : "activa",
    detalle: typeof bruto.detail === "string" && bruto.detail.trim() ? bruto.detail : null,
  };
};

/** `playback` es un dict libre de contadores: se lee con cuidado, sin asumir. */
const numeroDe = (fuente: Record<string, unknown>, clave: string): number | null => {
  const valor = fuente[clave];
  if (typeof valor === "number" && Number.isFinite(valor)) return valor;
  if (typeof valor === "string") {
    const convertido = Number.parseFloat(valor);
    return Number.isFinite(convertido) ? convertido : null;
  }
  return null;
};

const razonesDeFallo = (fuente: Record<string, unknown>): Array<[string, number]> => {
  const bruto = fuente.failure_by_reason;
  if (!bruto || typeof bruto !== "object" || Array.isArray(bruto)) return [];
  return Object.entries(bruto as Record<string, unknown>)
    .map(([razon, veces]): [string, number] => [
      razon,
      typeof veces === "number" ? veces : Number.parseInt(String(veces), 10) || 0,
    ])
    .sort((a, b) => b[1] - a[1]);
};

const formatearHora = (fecha: Date) =>
  fecha.toLocaleTimeString("es-ES", { hour: "2-digit", minute: "2-digit", second: "2-digit" });

const formatearIso = (iso: string) => {
  const fecha = new Date(iso);
  return Number.isNaN(fecha.getTime()) ? iso : fecha.toLocaleString("es-ES");
};

const formatearNumero = (valor: number | null) =>
  valor === null ? "sin dato" : valor.toLocaleString("es-ES");

/** Aviso de estado dentro de un panel: error, vacio y carga NO se parecen. */
function AvisoPanel({
  tono,
  mensaje,
  onReintentar,
}: {
  tono: "cargando" | "vacio" | "error";
  mensaje: string;
  onReintentar?: () => void;
}) {
  return (
    <div
      className={`dash-aviso dash-aviso-${tono}`}
      role={tono === "error" ? "alert" : undefined}
    >
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

/** Franja que avisa de que lo pintado es el ultimo dato bueno, no el de ahora. */
function FranjaObsoleta({ error, recibidoEn }: { error: string; recibidoEn: Date | null }) {
  return (
    <div className="dash-obsoleto" role="status">
      <AlertTriangle size={16} aria-hidden />
      <span>
        No se pudo actualizar ({error}). Mostrando el ultimo dato
        {recibidoEn ? ` de las ${formatearHora(recibidoEn)}` : " disponible"}.
      </span>
    </div>
  );
}

export function DashboardView() {
  const api = useApi();

  const [metricas, setMetricas] = useState<Recurso<SystemMetrics>>(recursoInicial);
  const [nodos, setNodos] = useState<Recurso<NodeHealth[]>>(recursoInicial);
  const [alertas, setAlertas] = useState<Recurso<AlertaActiva[]>>(recursoInicial);
  const [autoRefresco, setAutoRefresco] = useState(true);

  const montado = useRef(true);
  useEffect(() => {
    montado.current = true;
    return () => {
      montado.current = false;
    };
  }, []);

  const cargar = useCallback(async () => {
    setMetricas((previo) => ({ ...previo, cargando: true }));
    setNodos((previo) => ({ ...previo, cargando: true }));
    setAlertas((previo) => ({ ...previo, cargando: true }));

    const [resMetricas, resNodos, resAlertas] = await Promise.allSettled([
      api.getMetrics(),
      api.getNodesHealth(),
      api.getAlerts(),
    ]);

    // Tras un await el componente puede estar ya desmontado.
    if (!montado.current) return;

    const ahora = new Date();
    setMetricas((previo) => aplicar(previo, resMetricas, ahora, (valor: SystemMetrics) => valor));
    setNodos((previo) => aplicar(previo, resNodos, ahora, (valor: NodeHealth[]) => valor));
    setAlertas((previo) =>
      aplicar(previo, resAlertas, ahora, (valor: ActiveAlertsResponse) =>
        (valor.active ?? []).map(normalizarAlerta),
      ),
    );
  }, [api]);

  useEffect(() => {
    void cargar();
  }, [cargar]);

  // Refresco automatico. El intervalo se limpia al desmontar y al pausar:
  // un intervalo huerfano seguiria golpeando la API cada 30s.
  useEffect(() => {
    if (!autoRefresco) return;
    const identificador = window.setInterval(() => {
      void cargar();
    }, INTERVALO_MS);
    return () => window.clearInterval(identificador);
  }, [autoRefresco, cargar]);

  const cargando = metricas.cargando || nodos.cargando || alertas.cargando;

  // La frescura que se anuncia es la del dato mas viejo de la pantalla.
  const recibidos = [metricas.recibidoEn, nodos.recibidoEn, alertas.recibidoEn].filter(
    (fecha): fecha is Date => fecha !== null,
  );
  const ultimoDato =
    recibidos.length > 0
      ? new Date(Math.min(...recibidos.map((fecha) => fecha.getTime())))
      : null;
  const hayFallo = Boolean(metricas.error || nodos.error || alertas.error);

  const m = metricas.datos;
  const listaNodos = nodos.datos ?? [];
  const nodosCaidos = listaNodos.filter((nodo) => nodo.configured && !nodo.reachable);
  const listaAlertas = alertas.datos ?? [];

  const tonoFlussonic: BadgeTone = !m
    ? "neutral"
    : !m.flussonic_configured
      ? "neutral"
      : m.flussonic_reachable === true
        ? "success"
        : m.flussonic_reachable === false
          ? "danger"
          : "warning";

  const playback = m?.playback ?? {};
  const tasaFallo = numeroDe(playback, "failure_rate");
  const razones = razonesDeFallo(playback);

  return (
    <div className="dash">
      <header className="dash-barra">
        <div className="dash-frescura">
          <span className="dash-frescura-etiqueta">Ultimo dato</span>
          <strong>{ultimoDato ? formatearHora(ultimoDato) : "sin datos todavia"}</strong>
          {hayFallo ? (
            <StatusBadge tone="warning" dot>
              Actualizacion fallida
            </StatusBadge>
          ) : null}
          {m ? (
            <span className="dash-frescura-servidor">
              Snapshot del servidor: {formatearIso(m.timestamp)}
            </span>
          ) : null}
        </div>

        <div className="dash-acciones">
          <StatusBadge tone={autoRefresco ? "success" : "neutral"} dot>
            {autoRefresco ? "Auto 30 s" : "Auto en pausa"}
          </StatusBadge>
          <Button
            variant="ghost"
            size="sm"
            icon={autoRefresco ? <Pause size={16} /> : <Play size={16} />}
            onClick={() => setAutoRefresco((activo) => !activo)}
          >
            {autoRefresco ? "Pausar" : "Reanudar"}
          </Button>
          <Button
            variant="secondary"
            size="sm"
            icon={<RefreshCw size={16} />}
            loading={cargando}
            onClick={() => void cargar()}
          >
            Actualizar
          </Button>
        </div>
      </header>

      <section className="dash-panel" aria-labelledby="dash-alertas-titulo">
        <div className="dash-panel-cabecera">
          <h2 className="dash-panel-titulo" id="dash-alertas-titulo">
            <AlertTriangle size={16} aria-hidden />
            Alertas activas
          </h2>
          {listaAlertas.length > 0 ? (
            <StatusBadge tone="danger" dot>
              {listaAlertas.length}
            </StatusBadge>
          ) : null}
        </div>

        {alertas.error && !alertas.datos ? (
          <AvisoPanel
            tono="error"
            mensaje={`No se pudieron leer las alertas: ${alertas.error}`}
            onReintentar={() => void cargar()}
          />
        ) : (
          <>
            {alertas.error ? (
              <FranjaObsoleta error={alertas.error} recibidoEn={alertas.recibidoEn} />
            ) : null}
            {!alertas.datos && alertas.cargando ? (
              <AvisoPanel tono="cargando" mensaje="Cargando alertas..." />
            ) : listaAlertas.length === 0 ? (
              <AvisoPanel tono="vacio" mensaje="Sin alertas activas." />
            ) : (
              <div className="dash-alertas">
                {listaAlertas.map((alerta) => (
                  <article className="dash-alerta" key={alerta.clave}>
                    <AlertTriangle size={18} aria-hidden />
                    <div className="dash-alerta-texto">
                      <span className="dash-alerta-titulo">
                        {alerta.tipo}
                        {alerta.identificador ? ` ${alerta.identificador}` : ""} — {alerta.estado}
                      </span>
                      {alerta.detalle ? (
                        <span className="dash-alerta-detalle">{alerta.detalle}</span>
                      ) : null}
                    </div>
                  </article>
                ))}
              </div>
            )}
          </>
        )}
      </section>

      <section className="dash-panel" aria-labelledby="dash-nodos-titulo">
        <div className="dash-panel-cabecera">
          <h2 className="dash-panel-titulo" id="dash-nodos-titulo">
            <Server size={16} aria-hidden />
            Salud de nodos
          </h2>
          <span className="dash-panel-nota">
            Solo aparecen los nodos configurados en la API.
          </span>
        </div>

        {nodos.error && !nodos.datos ? (
          <AvisoPanel
            tono="error"
            mensaje={`No se pudo leer la salud de los nodos: ${nodos.error}`}
            onReintentar={() => void cargar()}
          />
        ) : (
          <>
            {nodos.error ? (
              <FranjaObsoleta error={nodos.error} recibidoEn={nodos.recibidoEn} />
            ) : null}
            {nodosCaidos.length > 0 ? (
              <p className="dash-resumen-caidos" role="alert">
                <AlertTriangle size={16} aria-hidden />
                {nodosCaidos.length} de {listaNodos.length} nodos no responden:{" "}
                {nodosCaidos.map((nodo) => nodo.node_id).join(", ")}
              </p>
            ) : null}
            {!nodos.datos && nodos.cargando ? (
              <AvisoPanel tono="cargando" mensaje="Consultando nodos..." />
            ) : listaNodos.length === 0 ? (
              <AvisoPanel
                tono="vacio"
                mensaje="La API no devuelve ningun nodo configurado."
              />
            ) : (
              <div className="dash-nodos">
                {listaNodos.map((nodo) => {
                  const caido = nodo.configured && !nodo.reachable;
                  const clase = !nodo.configured
                    ? "dash-nodo-sin-configurar"
                    : caido
                      ? "dash-nodo-caido"
                      : "dash-nodo-ok";
                  return (
                    <article className={`dash-nodo ${clase}`} key={nodo.node_id}>
                      <div className="dash-nodo-cabecera">
                        <span className="dash-nodo-id">{nodo.node_id}</span>
                        <StatusBadge
                          tone={!nodo.configured ? "neutral" : caido ? "danger" : "success"}
                          dot
                        >
                          {!nodo.configured ? "sin configurar" : caido ? "CAIDO" : "en linea"}
                        </StatusBadge>
                      </div>
                      <span className="dash-nodo-host">
                        {nodo.host || "sin host"}
                        {nodo.region ? ` · ${nodo.region}` : ""}
                      </span>
                      <div className="dash-nodo-datos">
                        <span>
                          Latencia{" "}
                          <strong>
                            {nodo.latency_ms === null ? "sin dato" : `${nodo.latency_ms} ms`}
                          </strong>
                        </span>
                        <span>
                          Streams{" "}
                          <strong>
                            {nodo.stream_count === null ? "sin dato" : nodo.stream_count}
                          </strong>
                        </span>
                      </div>
                    </article>
                  );
                })}
              </div>
            )}
          </>
        )}
      </section>

      <section className="dash-panel" aria-labelledby="dash-metricas-titulo">
        <div className="dash-panel-cabecera">
          <h2 className="dash-panel-titulo" id="dash-metricas-titulo">
            <Activity size={16} aria-hidden />
            Metricas del sistema
          </h2>
        </div>

        {metricas.error && !metricas.datos ? (
          <AvisoPanel
            tono="error"
            mensaje={`No se pudieron leer las metricas: ${metricas.error}`}
            onReintentar={() => void cargar()}
          />
        ) : !m ? (
          <AvisoPanel tono="cargando" mensaje="Cargando metricas..." />
        ) : (
          <>
            {metricas.error ? (
              <FranjaObsoleta error={metricas.error} recibidoEn={metricas.recibidoEn} />
            ) : null}

            <div className="dash-tarjetas">
              <article className="dash-tarjeta">
                <span className="dash-tarjeta-titulo">
                  <Activity size={14} aria-hidden />
                  Sesiones IPTV activas
                </span>
                <span className="dash-tarjeta-valor">
                  {m.active_iptv_sessions.toLocaleString("es-ES")}
                </span>
                <span className="dash-tarjeta-pie">No revocadas y sin expirar</span>
              </article>

              <article className="dash-tarjeta">
                <span className="dash-tarjeta-titulo">
                  <HardDrive size={14} aria-hidden />
                  Redis
                </span>
                <span className="dash-tarjeta-valor">
                  <StatusBadge tone={m.redis_healthy ? "success" : "danger"} dot>
                    {m.redis_healthy ? "OK" : "CAIDO"}
                  </StatusBadge>
                </span>
                <span className="dash-tarjeta-pie">Latencia {m.redis_latency_ms} ms</span>
              </article>

              <article className="dash-tarjeta">
                <span className="dash-tarjeta-titulo">
                  <Database size={14} aria-hidden />
                  PostgreSQL
                </span>
                <span className="dash-tarjeta-valor">
                  <StatusBadge tone={m.postgres_healthy ? "success" : "danger"} dot>
                    {m.postgres_healthy ? "OK" : "CAIDO"}
                  </StatusBadge>
                </span>
                <span className="dash-tarjeta-pie">Consulta de sesiones respondida</span>
              </article>

              <article className="dash-tarjeta">
                <span className="dash-tarjeta-titulo">
                  <Radio size={14} aria-hidden />
                  Flussonic
                </span>
                <span className="dash-tarjeta-valor">
                  <StatusBadge tone={tonoFlussonic} dot>
                    {!m.flussonic_configured
                      ? "sin configurar"
                      : m.flussonic_reachable === true
                        ? "alcanzable"
                        : m.flussonic_reachable === false
                          ? "NO RESPONDE"
                          : "sin comprobar"}
                  </StatusBadge>
                </span>
                <span className="dash-tarjeta-pie">Nodo principal</span>
              </article>

              <article className="dash-tarjeta">
                <span className="dash-tarjeta-titulo">Autorizaciones de playback</span>
                <span className="dash-tarjeta-valor">
                  {formatearNumero(numeroDe(playback, "authorize_total"))}
                </span>
                <span className="dash-tarjeta-pie">
                  {formatearNumero(numeroDe(playback, "authorize_success"))} ok ·{" "}
                  {formatearNumero(numeroDe(playback, "authorize_failure"))} fallidas
                </span>
              </article>

              <article className="dash-tarjeta">
                <span className="dash-tarjeta-titulo">Tasa de fallo</span>
                <span className="dash-tarjeta-valor">
                  {tasaFallo === null ? "sin dato" : `${(tasaFallo * 100).toFixed(2)} %`}
                </span>
                <span className="dash-tarjeta-pie">Contadores acumulados, no por minuto</span>
              </article>
            </div>

            <div className="dash-razones">
              <h3>Fallos de autorizacion por motivo</h3>
              {razones.length === 0 ? (
                <AvisoPanel tono="vacio" mensaje="Sin fallos registrados." />
              ) : (
                razones.map(([razon, veces]) => (
                  <div className="dash-razon" key={razon}>
                    <code>{razon}</code>
                    <strong>{veces.toLocaleString("es-ES")}</strong>
                  </div>
                ))
              )}
            </div>
          </>
        )}
      </section>
    </div>
  );
}
