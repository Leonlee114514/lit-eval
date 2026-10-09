interface Props {
  label: string;
  value: string | number | null | undefined;
  basis?: string;
  badge?: string;
}

export default function MetricCard({ label, value, basis, badge }: Props) {
  return (
    <div className="card" style={{ marginBottom: 0 }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <div className="muted">{label}</div>
        {badge && <span className="badge badge-pending">{badge}</span>}
      </div>
      <div style={{ fontSize: 24, fontWeight: 600, marginTop: 6 }}>{value ?? "—"}</div>
      {basis && <div className="small muted" style={{ marginTop: 6 }} title={basis}>{basis}</div>}
    </div>
  );
}
