import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Info, RefreshCw, Search, ShieldOff } from "lucide-react";

import { messageForError } from "../../api/errors";
import type { LiveSession } from "../../api/types";
import { useApi } from "../../auth/AuthContext";
import {
  Button,
  ConfirmDialog,
  DataTable,
  StatusBadge,
  TextInput,
  useToast,
} from "../../ui/primitives";
import type { Column } from "../../ui/primitives";
import "./sesiones.css";

/**
 * SESIONES EN VIVO
 *
 * Endpoints:
 *   GET    /api/admin/sessions/live                -> LiveSessionOut[] (array desnudo, max 200)
 *   DELETE /api/admin/sessions/subscriber/{sub_id} -> MessageResponse
 *
 * HUECO CONOCIDO — revocacion individual imposible:
 *   DELETE /api/admin/sessions/{jti} busca por `Session.access_token_jti`, pero
 *   `LiveSessionOut.session_id` es el `Session.id` de la base de datos y ningun
 *   esquema admin expone el jti. Mandar el session_id ahi no revoca nada y la
 *   API responde 200 con "Session not found or already revoked": un fallo
 *   silencioso. Por eso NO hay boton de cortar una sesion suelta; solo se
 *   ofrece la revocacion por suscriptor, que si recibe un dato que la UI tiene.
 */

/** Suscriptor con sus sesiones activas, para la accion de revocar en bloque. */
type ObjetivoRevocacion = {
  subscriberId: string;
  username: string;
  sesiones: LiveSession[];
};

const formatearFecha = (iso: string | null) => {
  if (!iso) return "sin dato";
  const fecha = new Date(iso);
  return Number.isNaN(fecha.getTime()) ? iso : fecha.toLocaleString("es-ES");
};

const formatearHora = (fecha: Date) =>
  fecha.toLocaleTimeString("es-ES", { hour: "2-digit", minute: "2-digit", second: "2-digit" });

/** Distancia en texto respecto al momento en que se cargaron los datos. */
const distancia = (iso: string | null, referencia: number) => {
  if (!iso) return "sin dato";
  const instante = new Date(iso).getTime();
  if (Number.isNaN(instante)) return "sin dato";

  const diferencia = referencia - instante;
  const futuro = diferencia < 0;
  const segundos = Math.floor(Math.abs(diferencia) / 1000);
  const minutos = Math.floor(segundos / 60);
  const horas = Math.floor(minutos / 60);
  const dias = Math.floor(horas / 24);

  let cantidad: string;
  if (segundos < 60) cantidad = `${segundos} s`;
  else if (minutos < 60) cantidad = `${minutos} min`;
  else if (horas < 24) cantidad = `${horas} h ${minutos % 60} min`;
  else cantidad = `${dias} d ${horas % 24} h`;

  return futuro ? `en ${cantidad}` : `hace ${cantidad}`;
};

export function SesionesView() {
  const api = useApi();
  const toast = useToast();

  const [sesiones, setSesiones] = useState<LiveSession[]>([]);
  const [cargando, setCargando] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [cargadoEn, setCargadoEn] = useState<Date | null>(null);
  const [filtro, setFiltro] = useState("");
  const [objetivo, setObjetivo] = useState<ObjetivoRevocacion | null>(null);
  const [revocando, setRevocando] = useState(false);

  const montado = useRef(true);
  useEffect(() => {
    montado.current = true;
    return () => {
      montado.current = false;
    };
  }, []);

  const cargar = useCallback(async () => {
    setCargando(true);
    try {
      // /sessions/live devuelve el array DESNUDO, sin envoltura .data.
      const filas = await api.listLiveSessions();
      if (!montado.current) return;
      setSesiones(filas);
      setError(null);
      setCargadoEn(new Date());
    } catch (err) {
      if (!montado.current) return;
      // El error NO se confunde con "no hay nadie viendo": las filas se vacian
      // y la tabla pinta el estado de error con su reintento.
      setSesiones([]);
      setError(messageForError(err));
    } finally {
      if (montado.current) setCargando(false);
    }
  }, [api]);

  useEffect(() => {
    void cargar();
  }, [cargar]);

  /** Cuantas sesiones activas tiene cada suscriptor (para avisar del alcance). */
  const porSuscriptor = useMemo(() => {
    const mapa = new Map<string, LiveSession[]>();
    for (const sesion of sesiones) {
      const actuales = mapa.get(sesion.subscriber_id);
      if (actuales) actuales.push(sesion);
      else mapa.set(sesion.subscriber_id, [sesion]);
    }
    return mapa;
  }, [sesiones]);

  const visibles = useMemo(() => {
    const busqueda = filtro.trim().toLowerCase();
    if (!busqueda) return sesiones;
    return sesiones.filter((sesion) =>
      [sesion.subscriber_username, sesion.ip_address, sesion.device_id, sesion.subscriber_id]
        .filter((valor): valor is string => typeof valor === "string")
        .some((valor) => valor.toLowerCase().includes(busqueda)),
    );
  }, [sesiones, filtro]);

  const referencia = (cargadoEn ?? new Date()).getTime();

  const revocarSuscriptor = async () => {
    if (!objetivo) return;
    setRevocando(true);
    try {
      const respuesta = await api.revokeSubscriberSessions(objetivo.subscriberId);
      // La API responde "{n} session(s) revoked": si n es 0 no se corto nada.
      const revocadas = Number.parseInt(respuesta.message, 10);
      if (Number.isFinite(revocadas) && revocadas === 0) {
        toast.info(`No habia sesiones activas de ${objetivo.username} que revocar.`);
      } else {
        toast.success(`Sesiones de ${objetivo.username} revocadas: ${respuesta.message}`);
      }
      setObjetivo(null);
      await cargar();
    } catch (err) {
      toast.error(messageForError(err));
    } finally {
      if (montado.current) setRevocando(false);
    }
  };

  const columnas: Column<LiveSession>[] = [
    {
      key: "suscriptor",
      header: "Suscriptor",
      render: (fila) => (
        <span className="ses-usuario">
          <strong>{fila.subscriber_username}</strong>
          <span className="ses-id">{fila.subscriber_id}</span>
        </span>
      ),
    },
    {
      key: "ip",
      header: "IP",
      width: "140px",
      render: (fila) =>
        fila.ip_address ? (
          <span className="ses-mono">{fila.ip_address}</span>
        ) : (
          <span className="ses-vacio">sin registrar</span>
        ),
    },
    {
      key: "dispositivo",
      header: "Dispositivo",
      width: "150px",
      render: (fila) =>
        fila.device_id ? (
          <span className="ses-id" title={fila.device_id}>
            {fila.device_id.slice(0, 8)}...
          </span>
        ) : (
          <span className="ses-vacio">sin dispositivo</span>
        ),
    },
    {
      key: "inicio",
      header: "Viendo desde",
      width: "170px",
      render: (fila) => (
        <span className="ses-tiempo">
          <span className="ses-tiempo-relativo">{distancia(fila.created_at, referencia)}</span>
          <span className="ses-tiempo-absoluto">{formatearFecha(fila.created_at)}</span>
        </span>
      ),
    },
    {
      key: "latido",
      header: "Ultimo latido",
      width: "170px",
      render: (fila) =>
        fila.last_heartbeat_at ? (
          <span className="ses-tiempo">
            <span className="ses-tiempo-relativo">
              {distancia(fila.last_heartbeat_at, referencia)}
            </span>
            <span className="ses-tiempo-absoluto">{formatearFecha(fila.last_heartbeat_at)}</span>
          </span>
        ) : (
          <span className="ses-vacio">nunca</span>
        ),
    },
    {
      key: "expira",
      header: "Expira",
      width: "170px",
      render: (fila) => (
        <span className="ses-tiempo">
          <span className="ses-tiempo-relativo">{distancia(fila.expires_at, referencia)}</span>
          <span className="ses-tiempo-absoluto">{formatearFecha(fila.expires_at)}</span>
        </span>
      ),
    },
    {
      key: "acciones",
      header: "Acciones",
      width: "210px",
      align: "right",
      render: (fila) => {
        const delSuscriptor = porSuscriptor.get(fila.subscriber_id) ?? [fila];
        return (
          <Button
            variant="danger"
            size="sm"
            icon={<ShieldOff size={15} />}
            onClick={() =>
              setObjetivo({
                subscriberId: fila.subscriber_id,
                username: fila.subscriber_username,
                sesiones: delSuscriptor,
              })
            }
          >
            {`Revocar las ${delSuscriptor.length} del suscriptor`}
          </Button>
        );
      },
    },
  ];

  return (
    <div className="ses">
      <div className="ses-barra">
        <div className="ses-barra-izq">
          <div className="ses-buscador">
            <TextInput
              type="search"
              value={filtro}
              onChange={(evento) => setFiltro(evento.target.value)}
              placeholder="Filtrar por usuario, IP o dispositivo"
              aria-label="Filtrar sesiones"
            />
          </div>
          <Search size={16} aria-hidden />
          <StatusBadge tone={error ? "neutral" : "info"} dot>
            {error ? "sin datos" : `${visibles.length} de ${sesiones.length} sesiones`}
          </StatusBadge>
        </div>

        <div className="ses-barra-der">
          <span className="ses-frescura">
            <span className="ses-frescura-etiqueta">Ultimo dato</span>
            <strong>{cargadoEn ? formatearHora(cargadoEn) : "sin datos todavia"}</strong>
          </span>
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
      </div>

      <p className="ses-nota">
        <Info size={16} aria-hidden />
        <span>
          La API solo permite revocar <strong>todas</strong> las sesiones de un suscriptor:
          el listado no expone el identificador que necesita el borrado individual. El
          listado devuelve como maximo 200 sesiones, las mas recientes primero.
        </span>
      </p>

      <DataTable
        columns={columnas}
        rows={visibles}
        rowKey={(fila) => fila.session_id}
        loading={cargando && sesiones.length === 0}
        error={error}
        onRetry={() => void cargar()}
        caption="Sesiones IPTV activas"
        emptyMessage={
          sesiones.length > 0
            ? "Ninguna sesion coincide con el filtro."
            : "No hay sesiones activas ahora mismo."
        }
      />

      <ConfirmDialog
        open={objetivo !== null}
        tone="danger"
        title="Revocar sesiones del suscriptor"
        confirmLabel="Revocar ahora"
        busy={revocando}
        onCancel={() => setObjetivo(null)}
        onConfirm={() => void revocarSuscriptor()}
        message={
          objetivo ? (
            <>
              Se cortara la reproduccion AHORA MISMO a <strong>{objetivo.username}</strong> en{" "}
              {objetivo.sesiones.length} sesion(es) activa(s):
              <ul className="ses-confirm-lista">
                {objetivo.sesiones.map((sesion) => (
                  <li key={sesion.session_id}>
                    {sesion.ip_address ?? "IP sin registrar"} ·{" "}
                    {sesion.device_id ? `dispositivo ${sesion.device_id.slice(0, 8)}` : "sin dispositivo"}{" "}
                    · {distancia(sesion.created_at, referencia)}
                  </li>
                ))}
              </ul>
            </>
          ) : (
            ""
          )
        }
      />
    </div>
  );
}
