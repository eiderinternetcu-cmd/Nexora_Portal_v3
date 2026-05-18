type NexoraBrandProps = {
  compact?: boolean;
};

export function NexoraBrand({ compact = false }: NexoraBrandProps) {
  return (
    <div className={compact ? "brand brand-compact" : "brand"}>
      <img src="/assets/icon.png" alt="" className="brand-icon" />
      <div>
        <div className="brand-word">NEXORA</div>
        {!compact && <div className="brand-subtitle">TU UNIVERSO. TU ENTRETENIMIENTO.</div>}
      </div>
    </div>
  );
}
