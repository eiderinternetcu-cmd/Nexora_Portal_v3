import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { RefreshCw } from "lucide-react";

import { messageForError } from "../../api/errors";
import { SUBSCRIBER_STATUSES } from "../../api/types";
import type { Subscriber, SubscriberStatus } from "../../api/types";
import { useApi } from "../../auth/AuthContext";
import { Button, Select, TextInput } from "../../ui/primitives";
import { DispositivosTabla } from "./DispositivosTabla";
import type { FilaDispositivo } from "./DispositivosTabla";
import "./dispositivos.css";

const TAMANOS_PAGINA = [10, 25, 50];

/** Peticiones de dispositivos en paralelo por tanda. */
const CONCURRENCIA = 6;

type FiltroEstadoDispositivo = "" | "bloqueado" | "activo" | "pending" | "revoked";

const trocear = <T,>(items: T[], tamano: number): T[][] => {
  const trozos: T[][] = [];
  for (let i = 0; i < items.length; i += tamano) trozos.push(items.slice(i, i + tamano));
  return trozos;
};

/**
 * Listado global de dispositivos.
 *
 * HUECO DE LA API: no existe ningun endpoint que liste dispositivos de todos
 * los suscriptores. app/api/v1/devices.py solo expone
 * GET /devices/subscriber/{sub_id}. Asi que esta pantalla se compone:
 *
 *   1. GET /api/admin/subscribers?page=&page_size=&status=   (paginacion real)
 *   2. GET /api/admin/devices/subscriber/{sub_id}            (uno por suscriptor)
 *
 * Es decir, la paginacion es de SUSCRIPTORES, no de dispositivos, y el numero
 * de peticiones crece con el tamano de pagina; por eso los tamanos son
 * pequenos y las llamadas van en tandas de CONCURRENCIA. Con un endpoint
 * `GET /devices` paginado esta pantalla seria una sola peticion.
 */
export function DispositivosView() {
  const api = useApi();

  const [filas, setFilas] = useState<FilaDispositivo[]>([]);
  const [totalSuscriptores, setTotalSuscriptores] = useState(0);
  const [paginas, setPaginas] = useState(0);
  const [pagina, setPagina] = useState(1);
  const [tamano, setTamano] = useState(25);
  const [estadoSuscriptor, setEstadoSuscriptor] = useState<SubscriberStatus | "">("");
  const [estadoDispositivo, setEstadoDispositivo] = useState<FiltroEstadoDispositivo>("");
  const [busqueda, setBusqueda] = useState("");

  const [cargando, setCargando] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [fallidos, setFallidos] = useState(0);

  const generacion = useRef(0);

  const cargar = useCallback(async () => {
    const actual = ++generacion.current;
    setCargando(true);
    setError(null);
    setFallidos(0);
    try {
      const respuesta = await api.listSubscribers({
        page: pagina,
        page_size: tamano,
        status: estadoSuscriptor || undefined,
      });
      if (actual !== generacion.current) return;
      setTotalSuscriptores(respuesta.total);
      setPaginas(respuesta.pages);

      const suscriptores: Subscriber[] = respuesta.data;
      const acumuladas: FilaDispositivo[] = [];
      let errores = 0;

      for (const tanda of trocear(suscriptores, CONCURRENCIA)) {
        const resultados = await Promise.allSettled(
          tanda.map((suscriptor) => api.listDevices(suscriptor.id)),
        );
        if (actual !== generacion.current) return;
        resultados.forEach((resultado, indice) => {
          const suscriptor = tanda[indice];
          if (resultado.status === "rejected") {
            errores += 1;
            return;
          }
          for (const dispositivo of resultado.value.data ?? []) {
            acumuladas.push({ ...dispositivo, suscriptorUsername: suscriptor.username });
          }
        });
      }

      if (actual !== generacion.current) return;
      acumuladas.sort((a, b) => (b.last_seen_at ?? "").localeCompare(a.last_seen_at ?? ""));
      setFilas(acumuladas);
      setFallidos(errores);
    } catch (err) {
      if (actual !== generacion.current) return;
      setFilas([]);
      setTotalSuscriptores(0);
      setPaginas(0);
      setError(messageForError(err));
    } finally {
      if (actual === generacion.current) setCargando(false);
    }
  }, [api, pagina, tamano, estadoSuscriptor]);

  useEffect(() => {
    void cargar();
  }, [cargar]);

  const visibles = useMemo(() => {
    const texto = busqueda.trim().toLowerCase();
    return filas.filter((fila) => {
      if (estadoDispositivo === "bloqueado" && !fila.is_blocked) return false;
      if (estadoDispositivo === "activo" && (fila.is_blocked || fila.status !== "active")) {
        return false;
      }
      if (estadoDispositivo === "pending" && fila.status !== "pending") return false;
      if (estadoDispositivo === "revoked" && fila.status !== "revoked") return false;
      if (!texto) return true;
      return [
        fila.suscriptorUsername,
        fila.device_id,
        fila.mac_address,
        fila.brand,
        fila.model,
        fila.device_type,
        fila.last_ip,
      ]
        .filter((valor): valor is string => Boolean(valor))
        .some((valor) => valor.toLowerCase().includes(texto));
    });
  }, [filas, busqueda, estadoDispositivo]);

  const desde = totalSuscriptores === 0 ? 0 : (pagina - 1) * tamano + 1;
  const hasta = Math.min(pagina * tamano, totalSuscriptores);

  return (
    <div className="disp-page">
      <header className="disp-header">
        <div>
          <h1>Dispositivos</h1>
          <p className="disp-subtitle">
            Dispositivos registrados por los suscriptores de la pagina actual.
          </p>
        </div>
        <Button
          variant="secondary"
          icon={<RefreshCw size={16} />}
          onClick={() => void cargar()}
          loading={cargando}
        >
          Recargar
        </Button>
      </header>

      <div className="disp-toolbar">
        <div className="disp-toolbar-field disp-grow">
          <label className="disp-toolbar-label" htmlFor="disp-busqueda">
            Buscar
          </label>
          <TextInput
            id="disp-busqueda"
            value={busqueda}
            placeholder="Suscriptor, device_id, MAC, marca, modelo o IP"
            onChange={(evento) => setBusqueda(evento.target.value)}
          />
        </div>
        <div className="disp-toolbar-field">
          <label className="disp-toolbar-label" htmlFor="disp-estado">
            Estado del dispositivo
          </label>
          <Select
            id="disp-estado"
            value={estadoDispositivo}
            onChange={(evento) =>
              setEstadoDispositivo(evento.target.value as FiltroEstadoDispositivo)
            }
          >
            <option value="">Todos</option>
            <option value="activo">Activos</option>
            <option value="bloqueado">Bloqueados</option>
            <option value="pending">Pendientes de activar</option>
            <option value="revoked">Revocados</option>
          </Select>
        </div>
        <div className="disp-toolbar-field">
          <label className="disp-toolbar-label" htmlFor="disp-estado-suscriptor">
            Estado del suscriptor
          </label>
          <Select
            id="disp-estado-suscriptor"
            value={estadoSuscriptor}
            onChange={(evento) => {
              setEstadoSuscriptor(evento.target.value as SubscriberStatus | "");
              setPagina(1);
            }}
          >
            <option value="">Todos</option>
            {SUBSCRIBER_STATUSES.map((valor) => (
              <option key={valor} value={valor}>
                {valor}
              </option>
            ))}
          </Select>
        </div>
        <div className="disp-toolbar-field">
          <label className="disp-toolbar-label" htmlFor="disp-tamano">
            Suscriptores por pagina
          </label>
          <Select
            id="disp-tamano"
            value={tamano}
            onChange={(evento) => {
              setTamano(Number(evento.target.value));
              setPagina(1);
            }}
          >
            {TAMANOS_PAGINA.map((valor) => (
              <option key={valor} value={valor}>
                {valor}
              </option>
            ))}
          </Select>
        </div>
        <p className="disp-toolbar-hint">
          La API no tiene listado global de dispositivos: esta pantalla recorre los
          suscriptores pagina a pagina y pide los dispositivos de cada uno. Por eso la
          paginacion cuenta suscriptores y la busqueda filtra lo ya cargado.
        </p>
      </div>

      {fallidos > 0 ? (
        <p className="disp-form-error" role="alert">
          No se pudieron cargar los dispositivos de {fallidos} suscriptor(es) de esta
          pagina. La lista esta incompleta.
        </p>
      ) : null}

      <DispositivosTabla
        filas={visibles}
        loading={cargando}
        error={error}
        onRetry={() => void cargar()}
        onCambio={() => void cargar()}
        caption="Dispositivos"
        emptyMessage={
          busqueda.trim() || estadoDispositivo
            ? "Ningun dispositivo de esta pagina coincide con los filtros."
            : "Los suscriptores de esta pagina no tienen dispositivos registrados."
        }
        footer={
          <div className="disp-pager">
            <span className="disp-pager-info">
              {totalSuscriptores === 0
                ? "Sin suscriptores"
                : `Suscriptores ${desde}-${hasta} de ${totalSuscriptores} · pagina ${pagina} de ${Math.max(paginas, 1)}`}
              {` · ${visibles.length} dispositivo(s)`}
            </span>
            <div className="disp-pager-buttons">
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
    </div>
  );
}
