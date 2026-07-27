import {
  createContext,
  useCallback,
  useContext,
  useMemo,
  useRef,
  useState,
} from "react";
import type { ReactNode } from "react";
import { AlertTriangle, CheckCircle2, Info, X } from "lucide-react";

export type ToastTone = "info" | "success" | "error";

export type Toast = {
  id: number;
  message: string;
  tone: ToastTone;
};

export type ToastApi = {
  /** Forma generica. Devuelve el id por si quieres descartarlo antes de tiempo. */
  push: (message: string, tone?: ToastTone, durationMs?: number) => number;
  success: (message: string) => number;
  error: (message: string) => number;
  info: (message: string) => number;
  dismiss: (id: number) => void;
};

const ToastContext = createContext<ToastApi | null>(null);

const DEFAULT_DURATION_MS = 4500;
const ERROR_DURATION_MS = 7000;

/** Va montado una sola vez en App. No lo montes dentro de una pantalla. */
export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const nextId = useRef(1);
  const timers = useRef(new Map<number, ReturnType<typeof setTimeout>>());

  const dismiss = useCallback((id: number) => {
    const timer = timers.current.get(id);
    if (timer) {
      clearTimeout(timer);
      timers.current.delete(id);
    }
    setToasts((current) => current.filter((toast) => toast.id !== id));
  }, []);

  const push = useCallback(
    (message: string, tone: ToastTone = "info", durationMs?: number) => {
      const id = nextId.current++;
      setToasts((current) => [...current, { id, message, tone }]);
      const ttl = durationMs ?? (tone === "error" ? ERROR_DURATION_MS : DEFAULT_DURATION_MS);
      timers.current.set(
        id,
        setTimeout(() => dismiss(id), ttl),
      );
      return id;
    },
    [dismiss],
  );

  const api = useMemo<ToastApi>(
    () => ({
      push,
      success: (message: string) => push(message, "success"),
      error: (message: string) => push(message, "error"),
      info: (message: string) => push(message, "info"),
      dismiss,
    }),
    [push, dismiss],
  );

  return (
    <ToastContext.Provider value={api}>
      {children}
      <ToastHost toasts={toasts} onDismiss={dismiss} />
    </ToastContext.Provider>
  );
}

/**
 * Notificaciones efimeras.
 *
 *   const toast = useToast();
 *   try { await api.deletePlan(id); toast.success("Plan eliminado"); }
 *   catch (err) { toast.error(messageForError(err)); }
 */
export function useToast(): ToastApi {
  const ctx = useContext(ToastContext);
  if (!ctx) throw new Error("useToast debe usarse dentro de <ToastProvider>");
  return ctx;
}

const iconForTone = (tone: ToastTone) => {
  if (tone === "success") return <CheckCircle2 size={16} aria-hidden />;
  if (tone === "error") return <AlertTriangle size={16} aria-hidden />;
  return <Info size={16} aria-hidden />;
};

function ToastHost({
  toasts,
  onDismiss,
}: {
  toasts: Toast[];
  onDismiss: (id: number) => void;
}) {
  return (
    <div className="toast-host" aria-live="polite" aria-atomic="false">
      {toasts.map((toast) => (
        <div key={toast.id} className={`toast toast-${toast.tone}`} role="status">
          {iconForTone(toast.tone)}
          <span className="toast-message">{toast.message}</span>
          <button
            type="button"
            className="toast-close"
            onClick={() => onDismiss(toast.id)}
            aria-label="Descartar"
          >
            <X size={14} aria-hidden />
          </button>
        </div>
      ))}
    </div>
  );
}
