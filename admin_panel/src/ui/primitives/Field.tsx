import { useId } from "react";
import type {
  InputHTMLAttributes,
  ReactNode,
  SelectHTMLAttributes,
  TextareaHTMLAttributes,
} from "react";

export type FieldProps = {
  label: string;
  /** Texto de ayuda bajo el control. Se oculta si hay `error`. */
  hint?: ReactNode;
  /** Mensaje de error; pinta el control en rojo y lo anuncia con role="alert". */
  error?: string | null;
  required?: boolean;
  /**
   * Render prop: recibe los ids que hay que colgar del control para que la
   * etiqueta y el error queden accesibles.
   */
  children: (props: {
    id: string;
    "aria-describedby": string | undefined;
    "aria-invalid": boolean | undefined;
  }) => ReactNode;
};

/**
 * Envoltorio de un control de formulario. No renderiza el input: lo recibes
 * como render prop para poder usar input, select, textarea o lo que sea.
 *
 *   <Field label="Usuario" error={errors.username} required>
 *     {(p) => <TextInput {...p} value={v} onChange={...} />}
 *   </Field>
 */
export function Field({ label, hint, error, required, children }: FieldProps) {
  const id = useId();
  const hintId = `${id}-hint`;
  const errorId = `${id}-error`;
  const describedBy = error ? errorId : hint ? hintId : undefined;

  return (
    <div className={`field${error ? " field-invalid" : ""}`}>
      <label className="field-label" htmlFor={id}>
        {label}
        {required ? <span className="field-required" aria-hidden> *</span> : null}
      </label>
      {children({
        id,
        "aria-describedby": describedBy,
        "aria-invalid": error ? true : undefined,
      })}
      {error ? (
        <p className="field-error" id={errorId} role="alert">
          {error}
        </p>
      ) : hint ? (
        <p className="field-hint" id={hintId}>
          {hint}
        </p>
      ) : null}
    </div>
  );
}

export type TextInputProps = InputHTMLAttributes<HTMLInputElement>;

export function TextInput({ className, ...rest }: TextInputProps) {
  return <input {...rest} className={["input", className ?? ""].filter(Boolean).join(" ")} />;
}

export type SelectProps = SelectHTMLAttributes<HTMLSelectElement>;

export function Select({ className, children, ...rest }: SelectProps) {
  return (
    <select {...rest} className={["input", "select", className ?? ""].filter(Boolean).join(" ")}>
      {children}
    </select>
  );
}

export type TextAreaProps = TextareaHTMLAttributes<HTMLTextAreaElement>;

export function TextArea({ className, rows = 3, ...rest }: TextAreaProps) {
  return (
    <textarea
      {...rest}
      rows={rows}
      className={["input", "textarea", className ?? ""].filter(Boolean).join(" ")}
    />
  );
}
