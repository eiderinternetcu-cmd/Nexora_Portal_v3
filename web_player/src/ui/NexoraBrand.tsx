import { brand } from "../brand";

type NexoraBrandProps = {
  compact?: boolean;
};

// Nombre conservado por compatibilidad con App.tsx; el componente ya es agnostico de marca.
export function NexoraBrand({ compact = false }: NexoraBrandProps) {
  return (
    <div className={compact ? "brand brand-compact" : "brand"}>
      <img src={brand.assets.icon} alt="" className="brand-icon" />
      <div>
        <div className="brand-word">{brand.name}</div>
        {!compact && <div className="brand-subtitle">{brand.tagline}</div>}
      </div>
    </div>
  );
}
