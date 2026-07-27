import { useCallback, useEffect, useState } from "react";
import {
  KeyRound,
  Pencil,
  Plus,
  Power,
  RefreshCw,
  ShieldCheck,
  ShieldAlert,
  Trash2,
} from "lucide-react";

import { useApi, useAuth } from "../../auth/AuthContext";
import { ApiError, messageForError } from "../../api/errors";
import type { User, UserPasswordChange, UserRole } from "../../api/types";
import {
  Button,
  ConfirmDialog,
  DataTable,
  Select,
  StatusBadge,
  TextInput,
  toneForBoolean,
  useToast,
} from "../../ui/primitives";
import type { Column } from "../../ui/primitives";
import { PasswordModal } from "./PasswordModal";
import { UserFormModal } from "./UserFormModal";
import type { UserFormPayload } from "./UserFormModal";
import { UserPasswordResetModal } from "./UserPasswordResetModal";
import "./usuarios.css";

const PAGE_SIZE = 25;

/** Espera antes de consultar para no lanzar una peticion por tecla pulsada. */
const SEARCH_DELAY_MS = 350;

type ActiveFilter = "" | "true" | "false";

const formatDateTime = (value: string | null) => {
  if (!value) return "Nunca";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString("es-ES", {
    day: "2-digit",
    month: "2-digit",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
};

const otherRole = (role: UserRole): UserRole => (role === "admin" ? "reseller" : "admin");

/**
 * Personal que administra la plataforma. NO son clientes: los clientes viven en
 * Suscriptores. Solo hay dos roles (app/models/user.py): admin y reseller.
 *
 * Endpoints (prefijo /api/admin, todos exigen rol admin):
 *   GET    /users?q=&role=&is_active= -> PaginatedResponse<User>
 *   POST   /users                     -> ApiResponse<User>
 *   PATCH  /users/{id}                -> ApiResponse<User>  (email, nombre, rol, activo, notas)
 *   DELETE /users/{id}                -> MessageResponse
 *   POST   /users/me/change-password  -> MessageResponse    (la propia cuenta, pide la actual)
 *   POST   /users/{id}/set-password   -> MessageResponse    (reponer la de otro usuario)
 *
 * Los tres filtros los aplica la API sobre toda la tabla, no sobre la pagina.
 *
 * El guard de ruta ya limita la seccion a admin, pero aqui no se supone nada:
 * si la API responde 403 se muestra explicitamente.
 */
export function UsuariosView() {
  const api = useApi();
  const { user: sessionUser } = useAuth();
  const toast = useToast();

  const [users, setUsers] = useState<User[]>([]);
  const [total, setTotal] = useState(0);
  const [pages, setPages] = useState(1);
  const [page, setPage] = useState(1);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [forbidden, setForbidden] = useState(false);

  // Filtros del servidor: lo que se escribe (search) y lo ya consultado (query).
  const [search, setSearch] = useState("");
  const [query, setQuery] = useState("");
  const [roleFilter, setRoleFilter] = useState<UserRole | "">("");
  const [activeFilter, setActiveFilter] = useState<ActiveFilter>("");

  const [formOpen, setFormOpen] = useState(false);
  const [editing, setEditing] = useState<User | null>(null);
  const [saving, setSaving] = useState(false);

  // Alta con rol admin: se confirma antes de crearla.
  const [pendingCreate, setPendingCreate] = useState<UserFormPayload | null>(null);

  const [roleTarget, setRoleTarget] = useState<User | null>(null);
  const [activeTarget, setActiveTarget] = useState<User | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<User | null>(null);
  const [acting, setActing] = useState(false);

  const [passwordOpen, setPasswordOpen] = useState(false);
  const [changingPassword, setChangingPassword] = useState(false);
  /** Usuario ajeno al que se le va a reponer la contrasena (set-password). */
  const [resetTarget, setResetTarget] = useState<User | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    setForbidden(false);
    try {
      // PaginatedResponse<User>: filas en .data, ademas total/page/pages.
      const response = await api.listUsers({
        page,
        page_size: PAGE_SIZE,
        q: query || undefined,
        role: roleFilter || undefined,
        // "" = sin filtro; hay que distinguirlo de false, que si filtra.
        is_active: activeFilter === "" ? undefined : activeFilter === "true",
      });
      setUsers(response.data ?? []);
      setTotal(response.total);
      setPages(response.pages);
    } catch (err) {
      setUsers([]);
      setTotal(0);
      setPages(1);
      if (err instanceof ApiError && err.status === 403) {
        setForbidden(true);
        setError("La API denego el acceso (403). Esta seccion exige rol admin.");
      } else {
        setError(messageForError(err));
      }
    } finally {
      setLoading(false);
    }
  }, [api, page, query, roleFilter, activeFilter]);

  useEffect(() => {
    void load();
  }, [load]);

  // Retardo del buscador: se consulta cuando el usuario deja de escribir. Se
  // vuelve a la pagina 1 porque el numero de paginas cambia con el filtro.
  useEffect(() => {
    const timer = window.setTimeout(() => {
      setQuery(search.trim());
      setPage(1);
    }, SEARCH_DELAY_MS);
    return () => window.clearTimeout(timer);
  }, [search]);

  const searching = search.trim() !== query;
  const hasFilters = query !== "" || roleFilter !== "" || activeFilter !== "";

  const reportError = (err: unknown) => {
    if (err instanceof ApiError && err.status === 403) {
      setForbidden(true);
      toast.error("Accion denegada por la API (403): requiere rol admin.");
      return;
    }
    toast.error(messageForError(err));
  };

  const submitCreate = async (payload: UserFormPayload) => {
    if (payload.mode !== "create") return;
    setSaving(true);
    try {
      await api.createUser(payload.body);
      toast.success(`Usuario "${payload.body.username}" creado.`);
      setFormOpen(false);
      setPendingCreate(null);
      setEditing(null);
      await load();
    } catch (err) {
      reportError(err);
    } finally {
      setSaving(false);
    }
  };

  const handleSubmit = async (payload: UserFormPayload) => {
    if (payload.mode === "create") {
      // Crear un admin es un cambio de permisos: se confirma antes.
      if (payload.body.role === "admin") {
        setPendingCreate(payload);
        return;
      }
      await submitCreate(payload);
      return;
    }

    if (!editing) return;
    setSaving(true);
    try {
      await api.updateUser(editing.id, payload.body);
      toast.success(`Usuario "${editing.username}" actualizado.`);
      setFormOpen(false);
      setEditing(null);
      await load();
    } catch (err) {
      reportError(err);
    } finally {
      setSaving(false);
    }
  };

  const handleRoleChange = async () => {
    if (!roleTarget) return;
    const next = otherRole(roleTarget.role);
    setActing(true);
    try {
      await api.updateUser(roleTarget.id, { role: next });
      toast.success(`"${roleTarget.username}" ahora es ${next}.`);
      setRoleTarget(null);
      await load();
    } catch (err) {
      reportError(err);
    } finally {
      setActing(false);
    }
  };

  const handleActiveToggle = async () => {
    if (!activeTarget) return;
    setActing(true);
    try {
      await api.updateUser(activeTarget.id, { is_active: !activeTarget.is_active });
      toast.success(
        activeTarget.is_active
          ? `"${activeTarget.username}" desactivado.`
          : `"${activeTarget.username}" activado.`,
      );
      setActiveTarget(null);
      await load();
    } catch (err) {
      reportError(err);
    } finally {
      setActing(false);
    }
  };

  const handleDelete = async () => {
    if (!deleteTarget) return;
    setActing(true);
    try {
      await api.deleteUser(deleteTarget.id);
      toast.success(`Usuario "${deleteTarget.username}" eliminado.`);
      setDeleteTarget(null);
      await load();
    } catch (err) {
      reportError(err);
    } finally {
      setActing(false);
    }
  };

  const handlePasswordChange = async (body: UserPasswordChange) => {
    setChangingPassword(true);
    try {
      await api.changeOwnPassword(body);
      toast.success("Contrasena actualizada.");
      setPasswordOpen(false);
    } catch (err) {
      reportError(err);
    } finally {
      setChangingPassword(false);
    }
  };

  const columns: Column<User>[] = [
    {
      key: "user",
      header: "Usuario",
      render: (row) => (
        <div className="usuarios-identity">
          <strong>
            {row.username}
            {row.id === sessionUser?.id ? <span className="usuarios-self">tu cuenta</span> : null}
          </strong>
          <span>{row.full_name ?? row.email}</span>
        </div>
      ),
    },
    {
      key: "email",
      header: "Email",
      width: "220px",
      render: (row) => row.email,
    },
    {
      key: "role",
      header: "Rol",
      width: "110px",
      render: (row) => (
        <StatusBadge tone={row.role === "admin" ? "info" : "neutral"} dot>
          {row.role}
        </StatusBadge>
      ),
    },
    {
      key: "active",
      header: "Estado",
      width: "110px",
      render: (row) => (
        <StatusBadge tone={toneForBoolean(row.is_active)} dot>
          {row.is_active ? "Activo" : "Inactivo"}
        </StatusBadge>
      ),
    },
    {
      key: "last_login",
      header: "Ultimo acceso",
      width: "170px",
      render: (row) => <span className="usuarios-dim">{formatDateTime(row.last_login_at)}</span>,
    },
    {
      key: "actions",
      header: "",
      align: "right",
      width: "330px",
      render: (row) => {
        const isSelf = row.id === sessionUser?.id;
        return (
          <div className="usuarios-row-actions">
            <Button
              size="sm"
              variant="ghost"
              icon={<Pencil size={15} />}
              onClick={() => {
                setEditing(row);
                setFormOpen(true);
              }}
            >
              Editar
            </Button>
            <Button
              size="sm"
              variant="ghost"
              icon={row.role === "admin" ? <ShieldAlert size={15} /> : <ShieldCheck size={15} />}
              disabled={isSelf}
              title={isSelf ? "No puedes cambiar tu propio rol." : undefined}
              onClick={() => setRoleTarget(row)}
            >
              Rol
            </Button>
            <Button
              size="sm"
              variant="ghost"
              icon={<Power size={15} />}
              disabled={isSelf}
              title={isSelf ? "No puedes desactivar tu propia cuenta." : undefined}
              onClick={() => setActiveTarget(row)}
            >
              {row.is_active ? "Desactivar" : "Activar"}
            </Button>
            <Button
              size="sm"
              variant="ghost"
              icon={<KeyRound size={15} />}
              title={
                isSelf
                  ? "Cambiar tu contrasena (pide la actual)."
                  : `Reponer la contrasena de ${row.username} sin conocer la actual.`
              }
              onClick={() => {
                // Dos endpoints distintos: la propia exige la contrasena
                // actual; la de otro usuario es una reposicion de admin.
                if (isSelf) setPasswordOpen(true);
                else setResetTarget(row);
              }}
            >
              Contrasena
            </Button>
            <Button
              size="sm"
              variant="ghost"
              icon={<Trash2 size={15} />}
              disabled={isSelf}
              title={isSelf ? "No puedes eliminar tu propia cuenta." : undefined}
              onClick={() => setDeleteTarget(row)}
            >
              Eliminar
            </Button>
          </div>
        );
      },
    },
  ];

  const rangeStart = users.length === 0 ? 0 : (page - 1) * PAGE_SIZE + 1;
  const rangeEnd = (page - 1) * PAGE_SIZE + users.length;

  return (
    <section>
      <header className="usuarios-header">
        <div className="usuarios-heading">
          <h2>Usuarios</h2>
          <p>
            Personal que administra la plataforma. Los clientes finales estan en
            Suscriptores. Solo existen los roles <code>admin</code> y <code>reseller</code>.
          </p>
        </div>
        <div className="usuarios-header-actions">
          <Button icon={<RefreshCw size={16} />} onClick={() => void load()} disabled={loading}>
            Actualizar
          </Button>
          <Button icon={<KeyRound size={16} />} onClick={() => setPasswordOpen(true)}>
            Mi contrasena
          </Button>
          <Button
            variant="primary"
            icon={<Plus size={16} />}
            disabled={forbidden}
            onClick={() => {
              setEditing(null);
              setFormOpen(true);
            }}
          >
            Nuevo usuario
          </Button>
        </div>
      </header>

      {forbidden ? (
        <div className="usuarios-forbidden" role="alert">
          <ShieldAlert size={18} aria-hidden />
          <div>
            <strong>Acceso denegado por la API (403)</strong>
            <span>
              La gestion de usuarios exige rol admin (require_admin). Tu sesion es{" "}
              <code>{sessionUser?.role ?? "desconocida"}</code>. Ocultar el enlace no
              protege el dato: quien manda es la API.
            </span>
          </div>
        </div>
      ) : null}

      <div className="usuarios-toolbar">
        <div className="usuarios-toolbar-field usuarios-grow">
          <label className="usuarios-toolbar-label" htmlFor="usuarios-buscar">
            Buscar
          </label>
          <TextInput
            id="usuarios-buscar"
            type="search"
            value={search}
            placeholder="Usuario, nombre o email"
            maxLength={128}
            disabled={forbidden}
            onChange={(event) => setSearch(event.target.value)}
          />
        </div>
        <div className="usuarios-toolbar-field">
          <label className="usuarios-toolbar-label" htmlFor="usuarios-rol">
            Rol
          </label>
          <Select
            id="usuarios-rol"
            value={roleFilter}
            disabled={forbidden}
            onChange={(event) => {
              setRoleFilter(event.target.value as UserRole | "");
              setPage(1);
            }}
          >
            <option value="">Todos</option>
            <option value="admin">admin</option>
            <option value="reseller">reseller</option>
          </Select>
        </div>
        <div className="usuarios-toolbar-field">
          <label className="usuarios-toolbar-label" htmlFor="usuarios-estado">
            Estado
          </label>
          <Select
            id="usuarios-estado"
            value={activeFilter}
            disabled={forbidden}
            onChange={(event) => {
              setActiveFilter(event.target.value as ActiveFilter);
              setPage(1);
            }}
          >
            <option value="">Todos</option>
            <option value="true">Activos</option>
            <option value="false">Inactivos</option>
          </Select>
        </div>
        <p className="usuarios-toolbar-hint">
          Los tres filtros los aplica la API sobre todos los usuarios, no solo sobre la
          pagina que estas viendo.
        </p>
      </div>

      <DataTable
        columns={columns}
        rows={users}
        rowKey={(row) => row.id}
        loading={loading}
        error={error}
        onRetry={() => void load()}
        emptyMessage={
          hasFilters
            ? "Ningun usuario coincide con los filtros."
            : "No hay usuarios en esta pagina."
        }
        caption="Listado de usuarios del panel"
        footer={
          <div className="usuarios-pager">
            <span>
              {total === 0
                ? "0 usuarios"
                : `${rangeStart}-${rangeEnd} de ${total} usuarios · pagina ${page} de ${pages}`}
              {searching ? " · actualizando busqueda..." : ""}
            </span>
            <div className="usuarios-pager-buttons">
              <Button
                size="sm"
                disabled={loading || page <= 1}
                onClick={() => setPage((current) => Math.max(1, current - 1))}
              >
                Anterior
              </Button>
              <Button
                size="sm"
                disabled={loading || page >= pages}
                onClick={() => setPage((current) => current + 1)}
              >
                Siguiente
              </Button>
            </div>
          </div>
        }
      />

      <UserFormModal
        open={formOpen}
        user={editing}
        saving={saving}
        onSubmit={(payload) => void handleSubmit(payload)}
        onClose={() => {
          if (saving) return;
          setFormOpen(false);
          setEditing(null);
        }}
      />

      <PasswordModal
        open={passwordOpen}
        username={sessionUser?.username ?? ""}
        saving={changingPassword}
        onSubmit={(body) => void handlePasswordChange(body)}
        onClose={() => {
          if (!changingPassword) setPasswordOpen(false);
        }}
      />

      <UserPasswordResetModal
        open={resetTarget !== null}
        user={resetTarget}
        onClose={() => setResetTarget(null)}
      />

      <ConfirmDialog
        open={pendingCreate !== null}
        tone="danger"
        title="Crear usuario con rol admin"
        message={
          pendingCreate?.mode === "create"
            ? `"${pendingCreate.body.username}" se creara como admin: acceso total al panel, incluida la gestion de usuarios y planes.`
            : ""
        }
        confirmLabel="Crear admin"
        busy={saving}
        onCancel={() => {
          if (!saving) setPendingCreate(null);
        }}
        onConfirm={() => {
          if (pendingCreate) void submitCreate(pendingCreate);
        }}
      />

      <ConfirmDialog
        open={roleTarget !== null}
        tone="danger"
        title="Cambiar rol"
        message={
          roleTarget
            ? `"${roleTarget.username}" pasara de ${roleTarget.role} a ${otherRole(roleTarget.role)}. El cambio de permisos es inmediato.`
            : ""
        }
        confirmLabel="Cambiar rol"
        busy={acting}
        onCancel={() => {
          if (!acting) setRoleTarget(null);
        }}
        onConfirm={() => void handleRoleChange()}
      />

      <ConfirmDialog
        open={activeTarget !== null}
        tone={activeTarget?.is_active ? "danger" : "default"}
        title={activeTarget?.is_active ? "Desactivar usuario" : "Activar usuario"}
        message={
          activeTarget?.is_active
            ? `"${activeTarget.username}" no podra iniciar sesion en el panel a partir de ahora.`
            : `"${activeTarget?.username ?? ""}" volvera a poder iniciar sesion en el panel.`
        }
        confirmLabel={activeTarget?.is_active ? "Desactivar" : "Activar"}
        busy={acting}
        onCancel={() => {
          if (!acting) setActiveTarget(null);
        }}
        onConfirm={() => void handleActiveToggle()}
      />

      <ConfirmDialog
        open={deleteTarget !== null}
        tone="danger"
        title="Eliminar usuario"
        message={`Se eliminara "${deleteTarget?.username ?? ""}" (${deleteTarget?.role ?? ""}). Esta accion no se puede deshacer.`}
        confirmLabel="Eliminar"
        busy={acting}
        onCancel={() => {
          if (!acting) setDeleteTarget(null);
        }}
        onConfirm={() => void handleDelete()}
      />
    </section>
  );
}
