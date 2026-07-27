/**
 * Primitivas de UI del panel. Importa SIEMPRE desde aqui:
 *   import { Button, DataTable, useToast } from "../../ui/primitives";
 *
 * Si necesitas una primitiva nueva que van a usar varias pantallas, anadela a
 * esta carpeta y exportala aqui. Si solo la usa tu pantalla, dejala en tu
 * propia carpeta de feature.
 */
export { Button } from "./Button";
export type { ButtonProps, ButtonSize, ButtonVariant } from "./Button";

export { Field, Select, TextArea, TextInput } from "./Field";
export type { FieldProps, SelectProps, TextAreaProps, TextInputProps } from "./Field";

export { DataTable } from "./DataTable";
export type { Column, DataTableProps } from "./DataTable";

export { Modal } from "./Modal";
export type { ModalProps } from "./Modal";

export { ConfirmDialog } from "./ConfirmDialog";
export type { ConfirmDialogProps } from "./ConfirmDialog";

export { StatusBadge, toneForBoolean, toneForSubscriberStatus } from "./StatusBadge";
export type { BadgeTone, StatusBadgeProps } from "./StatusBadge";

export { ToastProvider, useToast } from "./Toast";
export type { Toast, ToastApi, ToastTone } from "./Toast";
