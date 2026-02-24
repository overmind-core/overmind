export function ReportMetricRow({
  label,
  value,
}: {
  label: string;
  value: string;
  progress?: number;
}) {
  return (
    <div className="flex items-center justify-between">
      <span className="text-[0.82rem] font-medium text-muted-foreground">
        {label}
      </span>
      <span className="text-[0.82rem] font-bold text-foreground">
        {value}
      </span>
    </div>
  );
}
