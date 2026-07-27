import { useEffect, useState } from "react";
import { Info, RefreshCw, Search } from "lucide-react";

import { messageForError } from "../../api/errors";
import type { AuditEntry } from "../../api/types";
import { useApi } from "../../auth/AuthContext";
import {
  Button,
  DataTable,
  Field,
  Modal,
  Select,
  StatusBadge,
  TextInput,
} from "../../ui/primitives";
import type { Column } from "../../ui/primitives";
import "./auditoria.css";

/**
 * AUDITORIA — la pantalla de "quien hizo esto y cuando".
 *
 * Endpoint:
 *   GET /api/admin/audit?action=&actor=&limit=&offset=  -> AuditEntry[] (array desnudo)
 *
 * Lo que soporta la API (app/api/admin/metrics.py:119 y app/services/audit_service.py):
 *   - `action` y `actor` son coincidencia EXACTA (comparacion ==), no busqueda parcial.
 *   - `limit` se recorta al rango 1..200 en el servidor.
 *   - No devuelve total ni numero de paginas: la paginacion es por `offset` y solo
 *     se sabe que hay pagina siguiente si la actual vino llena.
 */

const LIMITES = [25, 50, 100, 200];

/** Acciones que registra hoy la API (grep de AuditService.log en app/). */
const ACCIONES_CONOCIDAS = [
  "auth.login",
  "device.block",
  "device.delete",
  "device.register",
  "device.unblock",
  "plan.create",
  "plan.delete",
  "plan.update",
  "subscriber.activate",
  "subscriber.create",
  "subscriber.delete",
  "subscriber.password_change",
  "subscriber.suspend",
  "subscriber.update",
  "subscription.cancel",
  "subscription.create",
  "subscription.renew",
  "user.create",
  "user.delete",
  "user.update",
];

type Consulta = {
  accion: string;
  actor: string;
  limite: number;
  desplazamiento: number;
};

const CONSULTA_INICIAL: Consulta = { accion: "", actor: "", limite: 50, desplazamiento: 0 };

const partesFecha = (iso: string | null) => {
  if (!iso) return { hora: "sin fecha", dia: "" };
  const fecha = new Date(iso);
  if (Number.isNaN(fecha.getTime())) return { hora: iso, dia: "" };
  return {
    hora: fecha.toLocaleTimeString("es-ES", {
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
    }),
    dia: fecha.toLocaleDateString("es-ES", { day: "2-digit", month: "2-digit", year: "numeric" }),
  };
};

export function AuditoriaView() {
  const api = useApi();

  const [entradas, setEntradas] = useState<AuditEntry[]>([]);
  const [cargando, setCargando] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [consulta, setConsulta] = useState<Consulta>(CONSULTA_INICIAL);
  const [recarga, setRecarga] = useState(0);

  const [borradorAccion, setBorradorAccion] = useState("");
  const [borradorActor, setBorradorActor] = useState("");

  const [detalle, setDetalle] = useState<AuditEntry | null>(null);

  useEffect(() => {
    let vigente = true;

    const cargar = async () => {
      setCargando(true);
      try {
        // Array desnudo: sin envoltura .data.
        const filas = await api.listAudit({
          action: consulta.accion || undefined,
          actor: consulta.actor || undefined,
          limit: consulta.limite,
          offset: consulta.desplazamiento,
        });
        if (!vigente) return;
        setEntradas(filas);
        setError(null);
      } catch (err) {
        if (!vigente) return;
        // Un fallo de red no puede parecer "no hay registros".
        setEntradas([]);
        setError(messageForError(err));
      } finally {
        if (vigente) setCargando(false);
      }
    };

    void cargar();
    return () => {
      vigente = false;
    };
  }, [api, consulta, recarga]);

  const aplicarFiltros = () => {
    setConsulta((previa) => ({
      ...previa,
      accion: borradorAccion.trim(),
      actor: borradorActor.trim(),
      desplazamiento: 0,
    }));
  };

  const limpiarFiltros = () => {
    setBorradorAccion("");
    setBorradorActor("");
    setConsulta((previa) => ({ ...previa, accion: "", actor: "", desplazamiento: 0 }));
  };

  const hayFiltros = consulta.accion !== "" || consulta.actor !== "";
  const paginaActual = Math.floor(consulta.desplazamiento / consulta.limite) + 1;
  // Sin total en la API: solo se sabe que hay mas si esta pagina vino llena.
  const puedeAvanzar = !error && entradas.length === consulta.limite;
  const puedeRetroceder = consulta.desplazamiento > 0;

  const columnas: Column<AuditEntry>[] = [
    {
      key: "fecha",
      header: "Fecha",
      width: "130px",
      render: (fila) => {
        const { hora, dia } = partesFecha(fila.created_at);
        return (
          <span className="aud-fecha">
            <span className="aud-fecha-hora">{hora}</span>
            <span className="aud-fecha-dia">{dia}</span>
          </span>
        );
      },
    },
    {
      key: "actor",
      header: "Quien",
      width: "150px",
      render: (fila) =>
        fila.actor_username ? (
          <strong>{fila.actor_username}</strong>
        ) : (
          <span className="aud-vacio">sistema / anonimo</span>
        ),
    },
    {
      key: "accion",
      header: "Accion",
      width: "210px",
      render: (fila) => <span className="aud-accion">{fila.action}</span>,
    },
    {
      key: "objetivo",
      header: "Sobre que",
      render: (fila) =>
        fila.target_type || fila.target_id ? (
          <span className="aud-objetivo">
            <span className="aud-objetivo-tipo">{fila.target_type ?? "sin tipo"}</span>
            <span className="aud-objetivo-id">{fila.target_id ?? "sin identificador"}</span>
          </span>
        ) : (
          <span className="aud-vacio">sin objetivo</span>
        ),
    },
    {
      key: "ip",
      header: "IP",
      width: "140px",
      render: (fila) =>
        fila.ip_address ? (
          <span className="aud-mono">{fila.ip_address}</span>
        ) : (
          <span className="aud-vacio">sin IP</span>
        ),
    },
    {
      key: "detalles",
      header: "Detalles",
      width: "110px",
      align: "right",
      render: (fila) =>
        fila.details && Object.keys(fila.details).length > 0 ? (
          <Button size="sm" variant="ghost" onClick={() => setDetalle(fila)}>
            Ver
          </Button>
        ) : (
          <span className="aud-vacio">—</span>
        ),
    },
  ];

  return (
    <div className="aud">
      <form
        className="aud-filtros"
        onSubmit={(evento) => {
          evento.preventDefault();
          aplicarFiltros();
        }}
      >
        <div className="aud-filtro">
          <Field label="Accion" hint="Coincidencia exacta">
            {(props) => (
              <>
                <TextInput
                  {...props}
                  list="aud-acciones"
                  value={borradorAccion}
                  onChange={(evento) => setBorradorAccion(evento.target.value)}
                  placeholder="subscriber.suspend"
                />
                <datalist id="aud-acciones">
                  {ACCIONES_CONOCIDAS.map((accion) => (
                    <option value={accion} key={accion} />
                  ))}
                </datalist>
              </>
            )}
          </Field>
        </div>

        <div className="aud-filtro">
          <Field label="Usuario que actuo" hint="Coincidencia exacta">
            {(props) => (
              <TextInput
                {...props}
                value={borradorActor}
                onChange={(evento) => setBorradorActor(evento.target.value)}
                placeholder="admin"
              />
            )}
          </Field>
        </div>

        <div className="aud-filtro-corto">
          <Field label="Filas por pagina">
            {(props) => (
              <Select
                {...props}
                value={consulta.limite}
                onChange={(evento) =>
                  setConsulta((previa) => ({
                    ...previa,
                    limite: Number(evento.target.value),
                    desplazamiento: 0,
                  }))
                }
              >
                {LIMITES.map((limite) => (
                  <option value={limite} key={limite}>
                    {limite}
                  </option>
                ))}
              </Select>
            )}
          </Field>
        </div>

        <div className="aud-filtros-acciones">
          <Button type="submit" variant="primary" icon={<Search size={16} />}>
            Filtrar
          </Button>
          <Button variant="ghost" onClick={limpiarFiltros} disabled={!hayFiltros}>
            Limpiar
          </Button>
          <Button
            variant="secondary"
            icon={<RefreshCw size={16} />}
            loading={cargando}
            onClick={() => setRecarga((valor) => valor + 1)}
          >
            Actualizar
          </Button>
        </div>
      </form>

      <p className="aud-nota">
        <Info size={16} aria-hidden />
        <span>
          El registro es de solo lectura y va de lo mas reciente a lo mas antiguo. Los
          filtros de accion y usuario son exactos: <code>subscriber.update</code> no
          encuentra nada si escribes <code>subscriber</code>. La API no informa del total
          de registros, asi que solo se puede avanzar mientras la pagina venga llena.
        </span>
      </p>

      <DataTable
        columns={columnas}
        rows={entradas}
        rowKey={(fila) => fila.id}
        loading={cargando}
        error={error}
        onRetry={() => setRecarga((valor) => valor + 1)}
        caption="Registro de auditoria"
        emptyMessage={
          hayFiltros
            ? "Ningun registro coincide con esos filtros."
            : consulta.desplazamiento > 0
              ? "No hay mas registros en esta pagina."
              : "El registro de auditoria esta vacio."
        }
        footer={
          <div className="aud-paginacion">
            <span className="aud-paginacion-info">
              Pagina {paginaActual} · registros {consulta.desplazamiento + 1}–
              {consulta.desplazamiento + entradas.length}
              {hayFiltros ? " (filtrados)" : ""}
            </span>
            <span className="aud-paginacion-botones">
              <StatusBadge tone="neutral">{entradas.length} en pantalla</StatusBadge>
              <Button
                size="sm"
                disabled={!puedeRetroceder || cargando}
                onClick={() =>
                  setConsulta((previa) => ({
                    ...previa,
                    desplazamiento: Math.max(0, previa.desplazamiento - previa.limite),
                  }))
                }
              >
                Anterior
              </Button>
              <Button
                size="sm"
                disabled={!puedeAvanzar || cargando}
                onClick={() =>
                  setConsulta((previa) => ({
                    ...previa,
                    desplazamiento: previa.desplazamiento + previa.limite,
                  }))
                }
              >
                Siguiente
              </Button>
            </span>
          </div>
        }
      />

      <Modal
        open={detalle !== null}
        onClose={() => setDetalle(null)}
        size="lg"
        title="Detalle del registro"
        footer={
          <Button variant="ghost" onClick={() => setDetalle(null)}>
            Cerrar
          </Button>
        }
      >
        {detalle ? (
          <>
            <div className="aud-detalle-cabecera">
              <span className="aud-detalle-fila">
                <span className="aud-detalle-etiqueta">Fecha</span>
                <span className="aud-detalle-valor">
                  {detalle.created_at
                    ? new Date(detalle.created_at).toLocaleString("es-ES")
                    : "sin fecha"}
                </span>
              </span>
              <span className="aud-detalle-fila">
                <span className="aud-detalle-etiqueta">Quien</span>
                <span className="aud-detalle-valor">
                  {detalle.actor_username ?? "sistema / anonimo"}
                </span>
              </span>
              <span className="aud-detalle-fila">
                <span className="aud-detalle-etiqueta">Accion</span>
                <span className="aud-detalle-valor aud-mono">{detalle.action}</span>
              </span>
              <span className="aud-detalle-fila">
                <span className="aud-detalle-etiqueta">Sobre que</span>
                <span className="aud-detalle-valor aud-mono">
                  {detalle.target_type ?? "sin tipo"} · {detalle.target_id ?? "sin id"}
                </span>
              </span>
              <span className="aud-detalle-fila">
                <span className="aud-detalle-etiqueta">IP</span>
                <span className="aud-detalle-valor aud-mono">
                  {detalle.ip_address ?? "sin IP"}
                </span>
              </span>
            </div>
            <pre className="aud-json">{JSON.stringify(detalle.details, null, 2)}</pre>
          </>
        ) : null}
      </Modal>
    </div>
  );
}
