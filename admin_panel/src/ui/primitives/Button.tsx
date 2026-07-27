import type { ButtonHTMLAttributes, ReactNode } from "react";
import { Loader2 } from "lucide-react";

export type ButtonVariant = "primary" | "secondary" | "ghost" | "danger";
export type ButtonSize = "sm" | "md";

export type ButtonProps = ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: ButtonVariant;
  size?: ButtonSize;
  /** Muestra spinner y deshabilita el boton. */
  loading?: boolean;
  /** Icono a la izquierda del texto (normalmente de lucide-react). */
  icon?: ReactNode;
  /** Ocupa todo el ancho disponible. */
  block?: boolean;
};

/**
 * <Button variant="primary" icon={<Plus size={16} />} loading={saving}>Crear</Button>
 *
 * `type` es "button" por defecto a proposito: dentro de un <form> un boton sin
 * type envia el formulario sin querer. Pon type="submit" explicitamente.
 */
export function Button({
  variant = "secondary",
  size = "md",
  loading = false,
  icon,
  block = false,
  className,
  disabled,
  children,
  type = "button",
  ...rest
}: ButtonProps) {
  const classes = [
    "btn",
    `btn-${variant}`,
    `btn-${size}`,
    block ? "btn-block" : "",
    className ?? "",
  ]
    .filter(Boolean)
    .join(" ");

  return (
    <button
      {...rest}
      type={type}
      className={classes}
      disabled={disabled || loading}
      aria-busy={loading || undefined}
    >
      {loading ? <Loader2 size={16} className="spin" aria-hidden /> : icon}
      {children ? <span>{children}</span> : null}
    </button>
  );
}
