import { RawResultAccordion } from "./RawResultAccordion";

interface AgentDiscoveryStats {
  mapped: number;
  new_templates: number;
  unmapped: number;
}

function normalise(result: Record<string, unknown>): AgentDiscoveryStats {
  // Shape B: { reason, stats: {...} }
  if (typeof result.stats === "object" && result.stats !== null) {
    return result.stats as AgentDiscoveryStats;
  }
  // Shape A: stats directly on result — extract with runtime guards to avoid NaN on missing fields
  return {
    mapped: typeof result.mapped === "number" ? result.mapped : 0,
    new_templates: typeof result.new_templates === "number" ? result.new_templates : 0,
    unmapped: typeof result.unmapped === "number" ? result.unmapped : 0,
  };
}

interface StatChipProps {
  label: string;
  value: number;
  highlight?: boolean;
}

function StatChip({ label, value, highlight }: StatChipProps) {
  return (
    <div className="flex flex-col items-center gap-1 rounded-lg border border-border bg-muted/30 px-6 py-4">
      <span
        className={`text-2xl font-bold tabular-nums ${highlight ? "text-amber-600" : "text-foreground"}`}
      >
        {value}
      </span>
      <span className="text-xs text-muted-foreground">{label}</span>
    </div>
  );
}

interface AgentDiscoveryResultProps {
  result: Record<string, unknown>;
}

export function AgentDiscoveryResult({ result }: AgentDiscoveryResultProps) {
  const stats = normalise(result);
  const reason = typeof result.reason === "string" ? result.reason : undefined;

  return (
    <div className="space-y-4">
      {reason && (
        <p className="text-sm text-muted-foreground">{reason}</p>
      )}
      <div className="flex flex-wrap gap-3">
        <StatChip highlight label="New Templates" value={stats.new_templates} />
        <StatChip label="Spans Mapped" value={stats.mapped} />
        <StatChip label="Still Unmapped" value={stats.unmapped} />
      </div>
      <RawResultAccordion result={result} />
    </div>
  );
}
