import { useCallback, useEffect, useMemo, useState } from "react";
import { AlertTriangle, ListChecks, RefreshCw } from "lucide-react";

import { messageForError } from "../../api/errors";
import type { Plan, PlanChannel } from "../../api/types";
import { useApi } from "../../auth/AuthContext";
import {
  Button,
  ConfirmDialog,
  Modal,
  StatusBadge,
  TextInput,
  useToast,
} from "../../ui/primitives";

export type PlanChannelsModalProps = {
  open: boolean;
  plan: Plan | null;
  /**
   * Las escrituras exigen rol admin (require_admin en app/api/admin/plan_channels.py).
   * Un reseller puede leer la lista, asi que la ventana se abre igual pero en
   * modo consulta: no se finge un guardado que la API va a rechazar con 403.
   */
  canEdit: boolean;
  onClose: () => void;
};

/** "1 canal anadido" / "3 canales anadidos" sin frases robotizadas. */
const contar = (n: number, singular: string, plural: string) =>
  `${n} ${n === 1 ? singular : plural}`;

/**
 * Lista blanca de canales de un plan.
 *
 * Endpoints (prefijo /api/admin):
 *   GET    /plans/{id}/channels?include_excluded=true -> ApiResponse<PlanChannel[]>
 *   PUT    /plans/{id}/channels {channel_ids}         -> ApiResponse<PlanChannelsSummary>
 *
 * UNA SOLA LECTURA: con `include_excluded=true` la API devuelve el catalogo
 * completo marcando `included` (pertenece al plan) e `is_active` (el canal
 * emite). No hace falta cruzar este listado con GET /channels.
 *
 * SE GUARDA CON EL PUT: reemplazo atomico de toda la lista. El PUT devuelve
 * {added, removed, total}, que es lo que se le cuenta al usuario en vez de un
 * "guardado" generico.
 *
 * REGLA CONTRAINTUITIVA que se avisa en pantalla: plan_channels es una lista
 * blanca ESTRICTA (app/services/entitlement_service.py). Un plan con cero
 * canales no da acceso a todo: no da acceso a nada.
 */
export function PlanChannelsModal({ open, plan, canEdit, onClose }: PlanChannelsModalProps) {
  const api = useApi();
  const toast = useToast();

  const [catalogo, setCatalogo] = useState<PlanChannel[]>([]);
  const [cargando, setCargando] = useState(true);
  const [error, setError] = useState<string | null>(null);

  /** Plan al que pertenece lo que hay cargado, para no pintar datos de otro. */
  const [planCargado, setPlanCargado] = useState<string | null>(null);
  /** Lo que la API dice que incluye el plan ahora mismo: base para el diff. */
  const [iniciales, setIniciales] = useState<Set<string>>(new Set());
  /** Seleccion en edicion, todavia sin guardar. */
  const [incluidos, setIncluidos] = useState<Set<string>>(new Set());

  const [busqueda, setBusqueda] = useState("");
  const [guardando, setGuardando] = useState(false);
  const [confirmando, setConfirmando] = useState(false);

  const planId = plan?.id ?? null;

  const cargar = useCallback(async () => {
    if (!planId) return;
    setCargando(true);
    setError(null);
    try {
      // include_excluded=true -> catalogo completo con included/is_active.
      const respuesta = await api.listPlanChannels(planId, true);
      const filas = respuesta.data ?? [];
      const marcados = new Set(filas.filter((canal) => canal.included).map((canal) => canal.id));
      setCatalogo(filas);
      setIniciales(marcados);
      setIncluidos(new Set(marcados));
    } catch (err) {
      setCatalogo([]);
      setIniciales(new Set());
      setIncluidos(new Set());
      setError(messageForError(err));
    } finally {
      setPlanCargado(planId);
      setCargando(false);
    }
  }, [api, planId]);

  useEffect(() => {
    if (!open) return;
    setBusqueda("");
    setConfirmando(false);
    void cargar();
  }, [open, cargar]);

  const visibles = useMemo(() => {
    const texto = busqueda.trim().toLowerCase();
    if (!texto) return catalogo;
    return catalogo.filter((canal) =>
      [canal.name, canal.channel_key, canal.category ?? "", String(canal.number)].some((valor) =>
        valor.toLowerCase().includes(texto),
      ),
    );
  }, [catalogo, busqueda]);

  const activos = useMemo(() => catalogo.filter((canal) => canal.is_active), [catalogo]);
  const activosPendientes = activos.filter((canal) => !incluidos.has(canal.id)).length;

  const anadidos = [...incluidos].filter((id) => !iniciales.has(id)).length;
  const quitados = [...iniciales].filter((id) => !incluidos.has(id)).length;
  const hayCambios = anadidos > 0 || quitados > 0;

  const alternar = (canalId: string) => {
    setIncluidos((previo) => {
      const siguiente = new Set(previo);
      if (siguiente.has(canalId)) siguiente.delete(canalId);
      else siguiente.add(canalId);
      return siguiente;
    });
  };

  /**
   * El caso de uso que hasta ahora se resolvia por SSH con
   * scripts/seed_plan_channels.py. Suma los activos a la seleccion sin tocar
   * los inactivos que ya estuvieran incluidos: es "incluir", no "reemplazar".
   */
  const incluirTodosLosActivos = () => {
    setIncluidos((previo) => {
      const siguiente = new Set(previo);
      for (const canal of activos) siguiente.add(canal.id);
      return siguiente;
    });
  };

  const guardar = async () => {
    if (!plan) return;
    setGuardando(true);
    try {
      const respuesta = await api.replacePlanChannels(plan.id, [...incluidos]);
      const resumen = respuesta.data;
      if (resumen && (resumen.added > 0 || resumen.removed > 0)) {
        toast.success(
          `Plan "${plan.name}": ${contar(resumen.added, "canal anadido", "canales anadidos")}, ` +
            `${contar(resumen.removed, "canal quitado", "canales quitados")}. ` +
            `Quedan ${contar(resumen.total, "canal incluido", "canales incluidos")}.`,
        );
      } else {
        toast.success(
          `Plan "${plan.name}": la lista ya era esa, no cambio nada. ` +
            `${contar(resumen?.total ?? incluidos.size, "canal incluido", "canales incluidos")}.`,
        );
      }
      setConfirmando(false);
      // Se recarga en vez de dar por bueno el estado local: la fuente de verdad
      // es la API y el catalogo puede haber cambiado mientras se editaba.
      await cargar();
    } catch (err) {
      toast.error(messageForError(err));
    } finally {
      setGuardando(false);
    }
  };

  const vaciandoElPlan = incluidos.size === 0;
  /** Hay datos en pantalla pero son de otro plan (la carga aun no ha llegado). */
  const desfasado = planId !== null && planCargado !== planId;

  const mensajeConfirmacion = vaciandoElPlan ? (
    <>
      El plan <strong>{plan?.name ?? ""}</strong> quedara con <strong>CERO canales</strong>. Un
      plan con cero canales <strong>no da acceso a todo: da acceso a NADA</strong>. Todos sus
      suscriptores dejaran de reproducir cualquier canal (<code>CHANNEL_NOT_INCLUDED</code>).
      {quitados > 0 ? ` Se quitaran ${contar(quitados, "canal", "canales")}.` : ""}
    </>
  ) : (
    <>
      Se reemplazara la lista blanca de <strong>{plan?.name ?? ""}</strong>:{" "}
      {contar(anadidos, "canal anadido", "canales anadidos")} y{" "}
      {contar(quitados, "canal quitado", "canales quitados")}. El plan quedara con{" "}
      {contar(incluidos.size, "canal", "canales")}. El cambio afecta de golpe a todos los
      suscriptores del plan.
    </>
  );

  const cerrar = () => {
    if (guardando) return;
    onClose();
  };

  return (
    <>
      <Modal
        open={open}
        title={plan ? `Canales incluidos: ${plan.name}` : "Canales incluidos"}
        onClose={cerrar}
        size="lg"
        dismissable={!guardando}
        footer={
          canEdit ? (
            <>
              <Button variant="ghost" onClick={cerrar} disabled={guardando}>
                Cancelar
              </Button>
              <Button
                variant="primary"
                disabled={!hayCambios || cargando || desfasado || error !== null}
                loading={guardando}
                onClick={() => setConfirmando(true)}
              >
                Guardar lista
              </Button>
            </>
          ) : (
            <Button variant="primary" onClick={cerrar}>
              Cerrar
            </Button>
          )
        }
      >
        <div className="planes-canales">
          <div className="planes-canales-aviso" role="note">
            <AlertTriangle size={18} aria-hidden />
            <span>
              <code>plan_channels</code> es una <strong>lista blanca estricta</strong>: si un canal
              no esta incluido, sus suscriptores no lo pueden reproducir. Ojo, es
              contraintuitivo: un plan con <strong>CERO canales no da acceso a todo, da acceso a
              NADA</strong>.
            </span>
          </div>

          {!canEdit ? (
            <p className="planes-canales-nota">
              Solo consulta: modificar la lista exige rol <code>admin</code>.
            </p>
          ) : null}

          {/* `desfasado` evita pintar por un instante los canales del plan
              anterior al reabrir la ventana sobre otro plan. */}
          {cargando || desfasado ? (
            <p className="planes-canales-estado">Cargando el catalogo de canales...</p>
          ) : error ? (
            <div className="planes-canales-estado planes-canales-error" role="alert">
              <AlertTriangle size={18} aria-hidden />
              <span>{error}</span>
              <Button size="sm" icon={<RefreshCw size={15} />} onClick={() => void cargar()}>
                Reintentar
              </Button>
            </div>
          ) : catalogo.length === 0 ? (
            <p className="planes-canales-estado">
              El catalogo no tiene canales, asi que no hay nada que incluir en el plan.
            </p>
          ) : (
            <>
              <div className="planes-canales-barra">
                <TextInput
                  type="search"
                  value={busqueda}
                  placeholder="Buscar por nombre, clave, categoria o numero"
                  aria-label="Buscar canales del catalogo"
                  onChange={(evento) => setBusqueda(evento.target.value)}
                />
                <Button
                  icon={<ListChecks size={16} />}
                  disabled={!canEdit || activosPendientes === 0}
                  title={
                    activosPendientes === 0
                      ? "Todos los canales activos ya estan incluidos."
                      : undefined
                  }
                  onClick={incluirTodosLosActivos}
                >
                  Incluir todos los activos
                </Button>
              </div>

              <div className="planes-canales-resumen">
                <StatusBadge tone={incluidos.size === 0 ? "danger" : "success"} dot>
                  {incluidos.size} de {catalogo.length} incluidos
                </StatusBadge>
                <span>
                  {activos.length} activos en el catalogo
                  {catalogo.length - activos.length > 0
                    ? ` · ${catalogo.length - activos.length} inactivos`
                    : ""}
                </span>
                {hayCambios ? (
                  <span className="planes-canales-cambios">
                    Sin guardar: +{anadidos} / -{quitados}
                  </span>
                ) : null}
                {busqueda.trim() ? <span>{visibles.length} coinciden con la busqueda</span> : null}
              </div>

              {visibles.length === 0 ? (
                <p className="planes-canales-estado">Ningun canal coincide con la busqueda.</p>
              ) : (
                <ul className="planes-canales-lista">
                  {visibles.map((canal) => {
                    const marcado = incluidos.has(canal.id);
                    return (
                      <li key={canal.id}>
                        <label
                          className={`planes-canal${canal.is_active ? "" : " planes-canal-inactivo"}`}
                        >
                          <input
                            type="checkbox"
                            checked={marcado}
                            disabled={!canEdit || guardando}
                            onChange={() => alternar(canal.id)}
                          />
                          <span className="planes-canal-num">{canal.number}</span>
                          <span className="planes-canal-datos">
                            <span className="planes-canal-nombre">{canal.name}</span>
                            <span className="planes-canal-meta">
                              {canal.channel_key}
                              {canal.category ? ` · ${canal.category}` : ""}
                            </span>
                          </span>
                          {canal.is_active ? null : (
                            <span
                              className="planes-canal-marca"
                              title="El canal esta inactivo: no emite. Incluirlo en el plan no lo hace reproducible."
                            >
                              <StatusBadge tone="warning" dot>
                                inactivo
                              </StatusBadge>
                            </span>
                          )}
                        </label>
                      </li>
                    );
                  })}
                </ul>
              )}

              <p className="planes-canales-nota">
                Los canales <strong>inactivos</strong> aparecen atenuados: no emiten, asi que
                incluirlos no habilita nada. Se listan porque un canal puede reactivarse y porque
                ocultar lo que ya esta en el plan seria mentir sobre su contenido.
              </p>
            </>
          )}
        </div>
      </Modal>

      <ConfirmDialog
        open={confirmando}
        tone={vaciandoElPlan || quitados > 0 ? "danger" : "default"}
        title={vaciandoElPlan ? "Dejar el plan sin ningun canal" : "Guardar lista de canales"}
        message={mensajeConfirmacion}
        confirmLabel={vaciandoElPlan ? "Vaciar el plan" : "Guardar"}
        busy={guardando}
        onCancel={() => {
          if (!guardando) setConfirmando(false);
        }}
        onConfirm={() => void guardar()}
      />
    </>
  );
}
