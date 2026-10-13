import { Badge } from "@/components/ui/badge";
import { DeltaChip, fromScore } from "@/components/ui/delta-chip";
import { EmptyState } from "@/components/ui/empty-state";
import { Icon } from "@/components/ui/icons";
import { LoadingState } from "@/components/ui/spinner";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { useEvalRunComparisonQuery } from "@/hooks/use-evaluations";
import { type StatusTone, scoreChipClass, TONE_BADGE_VARIANT, TONE_CHIP } from "@/lib/colors";
import { cn, scorePct } from "@/lib/utils";
import type { EvalRunComparison } from "@/openapi";

export interface SummaryMetric {
  type?: string;
  mean: number | null;
  n: number;
  failed?: number;
  errors?: number;
}

export interface RunSummary {
  score?: number | null;
  metrics?: Record<string, SummaryMetric>;
  rows?: number;
  scored?: number;
  errored?: number;
  degraded?: number;
  trust?: { trusted?: boolean; errored?: number; degraded?: number };
  class_metrics?: Record<string, unknown>;
}

export const asRunSummary = (summary: unknown): RunSummary =>
  summary != null && typeof summary === "object" ? (summary as RunSummary) : {};

export const metricEntries = (summary: RunSummary): [string, SummaryMetric][] =>
  Object.entries(summary.metrics ?? {}).sort(([a], [b]) => a.localeCompare(b));

export function ScoreChip({ value }: { value: number | null | undefined }) {
  if (value == null) return <span className="text-muted-foreground/40">—</span>;
  const pct = scorePct(value);
  return (
    <span
      className={cn(
        "inline-flex items-center justify-center rounded-sm border px-1.5 py-0.5 font-mono text-xs font-semibold tabular-nums",
        scoreChipClass(pct)
      )}
    >
      {pct}%
    </span>
  );
}

const STATUS_TONE: Record<string, StatusTone> = {
  added: "info",
  improved: "success",
  regressed: "error",
  removed: "warning",
  unchanged: "neutral",
};

const STATUS_LABEL: Record<string, string> = {
  added: "Added",
  improved: "Improved",
  regressed: "Regressed",
  removed: "Lost coverage",
  unchanged: "Unchanged",
};

function StatusPill({ status }: { status: string }) {
  return (
    <Badge
      className="pixel-label text-xs"
      variant={TONE_BADGE_VARIANT[STATUS_TONE[status] ?? "neutral"]}
    >
      {STATUS_LABEL[status] ?? status}
    </Badge>
  );
}

function DeltaCell({ delta, status }: { delta: number | null; status: string }) {
  if (delta == null || status === "unchanged")
    return <span className="font-mono text-xs tabular-nums text-muted-foreground">±0</span>;
  return <DeltaChip value={fromScore(delta)} />;
}

function untrusted(trust: unknown): string | null {
  const t = asRunSummary({ trust }).trust;
  if (t?.trusted !== false) return null;
  const parts: string[] = [];
  if (t.errored) parts.push(`${t.errored} errored`);
  if (t.degraded) parts.push(`${t.degraded} degraded`);
  return parts.length ? parts.join(", ") : "no scored rows";
}

function ComparisonTable({ comparison }: { comparison: EvalRunComparison }) {
  const baselineNote = untrusted(comparison.trust?.baseline);
  const candidateNote = untrusted(comparison.trust?.candidate);
  return (
    <section aria-label="Baseline comparison" className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-muted-foreground">
        <span>
          <span className="font-medium text-foreground">{comparison.candidateLabel}</span> vs
          baseline <span className="font-medium text-foreground">{comparison.baselineLabel}</span>
        </span>
        <span className="font-mono tabular-nums">{comparison.paired} paired rows</span>
        <span className="font-mono tabular-nums text-success">{comparison.wins} wins</span>
        <span className="font-mono tabular-nums text-destructive">{comparison.losses} losses</span>
        <span className="font-mono tabular-nums">{comparison.ties} ties</span>
      </div>
      {(baselineNote || candidateNote) && (
        <div
          className={cn(
            "flex flex-col gap-1 rounded-md border px-4 py-3 text-xs",
            TONE_CHIP.warning
          )}
        >
          {candidateNote && <span>This run: {candidateNote}</span>}
          {baselineNote && <span>Baseline: {baselineNote}</span>}
        </div>
      )}
      <div className="overflow-auto rounded-md border border-border">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Metric</TableHead>
              <TableHead className="text-center">This run</TableHead>
              <TableHead className="text-center">Baseline</TableHead>
              <TableHead className="text-center">Δ</TableHead>
              <TableHead className="text-center">Change</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            <TableRow className="border-b-2 bg-wash-subtle">
              <TableCell className="text-sm font-semibold">Overall</TableCell>
              <TableCell className="text-center">
                <ScoreChip value={comparison.overall.candidate} />
              </TableCell>
              <TableCell className="text-center">
                <ScoreChip value={comparison.overall.baseline} />
              </TableCell>
              <TableCell className="text-center">
                <DeltaCell delta={comparison.overall.delta} status={comparison.overall.status} />
              </TableCell>
              <TableCell className="text-center">
                <StatusPill status={comparison.overall.status} />
              </TableCell>
            </TableRow>
            {comparison.metrics.map((metric) => (
              <TableRow key={metric.name}>
                <TableCell className="text-sm font-medium">{metric.name}</TableCell>
                <TableCell className="text-center">
                  <ScoreChip value={metric.candidate} />
                </TableCell>
                <TableCell className="text-center">
                  <ScoreChip value={metric.baseline} />
                </TableCell>
                <TableCell className="text-center">
                  <DeltaCell delta={metric.delta} status={metric.status} />
                </TableCell>
                <TableCell className="text-center">
                  <StatusPill status={metric.status} />
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>
    </section>
  );
}

/** Compares a run against the baseline launched in the same group. */
export function RunComparison({
  runId,
  hasBaseline,
  active,
}: {
  runId: string;
  hasBaseline: boolean;
  active: boolean;
}) {
  const { data, isLoading } = useEvalRunComparisonQuery(runId, hasBaseline, active);
  if (!hasBaseline) {
    return (
      <EmptyState
        className="rounded-md border border-dashed bg-wash-subtle"
        description="Launch targets together and mark one as the baseline."
        icon={Icon.diff}
        size="section"
        title="No baseline in this group"
      />
    );
  }
  if (isLoading) return <LoadingState label="Loading comparison…" />;
  if (!data) {
    return (
      <EmptyState
        className="rounded-md border border-dashed bg-wash-subtle"
        icon={Icon.diff}
        size="section"
        title="No comparison yet"
      />
    );
  }
  return <ComparisonTable comparison={data} />;
}
