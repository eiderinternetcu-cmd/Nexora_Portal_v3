import type { ReactNode } from "react";

import type { SubscriberStatus } from "../../api/types";

export type BadgeTone = "neutral" | "success" | "warning" | "danger" | "info";

export type StatusBadgeProps = {
  tone?: BadgeTone;
  /** Punto de color a la izquierda. */
  dot?: boolean;
  children: ReactNode;
};

/** <StatusBadge tone={toneForSubscriberStatus(s.status)} dot>{s.status}</StatusBadge> */
export function StatusBadge({ tone = "neutral", dot = false, children }: StatusBadgeProps) {
  return (
    <span className={`badge badge-${tone}`}>
      {dot ? <span className="badge-dot" aria-hidden /> : null}
      {children}
    </span>
  );
}

/** Mapeo canonico de SubscriberStatus a color. Usalo para no divergir entre pantallas. */
export const toneForSubscriberStatus = (status: SubscriberStatus): BadgeTone => {
  switch (status) {
    case "active":
      return "success";
    case "expired":
      return "warning";
    case "suspended":
      return "info";
    case "banned":
      return "danger";
    default:
      return "neutral";
  }
};

/** Para banderas booleanas (is_active, is_blocked, alive...). */
export const toneForBoolean = (value: boolean, invert = false): BadgeTone =>
  value !== invert ? "success" : "danger";
