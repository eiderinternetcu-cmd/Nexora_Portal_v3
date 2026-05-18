import { AlertTriangle, CheckCircle2, Info } from "lucide-react";

export type ToastTone = "info" | "success" | "error";

export type Toast = {
  id: number;
  message: string;
  tone: ToastTone;
};

const iconForTone = (tone: ToastTone) => {
  if (tone === "success") return <CheckCircle2 size={16} />;
  if (tone === "error") return <AlertTriangle size={16} />;
  return <Info size={16} />;
};

export function ToastHost({ toasts }: { toasts: Toast[] }) {
  return (
    <div className="toast-host" aria-live="polite" aria-atomic="true">
      {toasts.map((toast) => (
        <div key={toast.id} className={`toast toast-${toast.tone}`}>
          {iconForTone(toast.tone)}
          <span>{toast.message}</span>
        </div>
      ))}
    </div>
  );
}
