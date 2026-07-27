import type { ReactNode } from "react";
import { AlertTriangle, Inbox, Loader2 } from "lucide-react";

import { Button } from "./Button";

export type Column<T> = {
  /** Identificador estable de la columna (clave de React). */
  key: string;
  header: ReactNode;
  /** Como pintar la celda de esa fila. */
  render: (row: T) => ReactNode;
  /** Ancho CSS, ej. "180px" o "20%". Opcional. */
  width?: string;
  align?: "left" | "center" | "right";
};

export type DataTableProps<T> = {
  columns: Column<T>[];
  rows: T[];
  /** Clave estable por fila; casi siempre `(r) => r.id`. */
  rowKey: (row: T) => string;
  /** Muestra el estado de carga en lugar de las filas. */
  loading?: boolean;
  /** Mensaje de error ya presentable (usa messageForError). */
  error?: string | null;
  /** Si lo pasas, el estado de error muestra un boton "Reintentar". */
  onRetry?: () => void;
  /** Texto del estado vacio. */
  emptyMessage?: string;
  /** Hace las filas clicables. */
  onRowClick?: (row: T) => void;
  /** Contenido bajo la tabla, tipicamente la paginacion. */
  footer?: ReactNode;
  /** Etiqueta accesible de la tabla. */
  caption?: string;
};

/**
 * Tabla con los tres estados que toda pantalla de listado repite.
 * PRECEDENCIA: error > loading > vacio > filas.
 *
 *   <DataTable
 *     columns={[{ key: "user", header: "Usuario", render: (r) => r.username }]}
 *     rows={rows}
 *     rowKey={(r) => r.id}
 *     loading={loading}
 *     error={error}
 *     onRetry={load}
 *   />
 */
export function DataTable<T>({
  columns,
  rows,
  rowKey,
  loading = false,
  error = null,
  onRetry,
  emptyMessage = "No hay resultados.",
  onRowClick,
  footer,
  caption,
}: DataTableProps<T>) {
  const colCount = columns.length;

  const body = () => {
    if (error) {
      return (
        <tr>
          <td colSpan={colCount}>
            <div className="table-state table-state-error" role="alert">
              <AlertTriangle size={20} aria-hidden />
              <span>{error}</span>
              {onRetry ? (
                <Button size="sm" onClick={onRetry}>
                  Reintentar
                </Button>
              ) : null}
            </div>
          </td>
        </tr>
      );
    }

    if (loading) {
      return (
        <tr>
          <td colSpan={colCount}>
            <div className="table-state" aria-live="polite">
              <Loader2 size={20} className="spin" aria-hidden />
              <span>Cargando...</span>
            </div>
          </td>
        </tr>
      );
    }

    if (rows.length === 0) {
      return (
        <tr>
          <td colSpan={colCount}>
            <div className="table-state">
              <Inbox size={20} aria-hidden />
              <span>{emptyMessage}</span>
            </div>
          </td>
        </tr>
      );
    }

    return rows.map((row) => (
      <tr
        key={rowKey(row)}
        className={onRowClick ? "row-clickable" : undefined}
        onClick={onRowClick ? () => onRowClick(row) : undefined}
      >
        {columns.map((col) => (
          <td key={col.key} style={col.align ? { textAlign: col.align } : undefined}>
            {col.render(row)}
          </td>
        ))}
      </tr>
    ));
  };

  return (
    <div className="table-wrap">
      <table className="table">
        {caption ? <caption className="sr-only">{caption}</caption> : null}
        <thead>
          <tr>
            {columns.map((col) => (
              <th
                key={col.key}
                scope="col"
                style={{
                  width: col.width,
                  textAlign: col.align,
                }}
              >
                {col.header}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>{body()}</tbody>
      </table>
      {footer ? <div className="table-footer">{footer}</div> : null}
    </div>
  );
}
