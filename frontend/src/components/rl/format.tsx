import { stockCheckName } from "@/components/evaluations/evaluation-presentation";
import { Badge } from "@/components/ui/badge";
import { Spinner } from "@/components/ui/spinner";
import type { RewardConfig } from "@/components/rl/types";

export const pct = (value: number | null | undefined) =>
  value == null || !Number.isFinite(value) ? "—" : `${Math.round(value * 100)}%`;

export const score = (value: number | null | undefined) =>
  value == null || !Number.isFinite(value) ? "—" : value.toFixed(2);

export function duration(seconds: number | null | undefined) {
  if (seconds == null) return "—";
  const total = Math.round(seconds);
  const minutes = Math.floor(total / 60);
  return minutes ? `${minutes}m ${total % 60}s` : `${total}s`;
}

/** A reward key is its check name, suffixed when the same check appears twice. */
export function rewardLabel(key: string, rewards?: RewardConfig[]) {
  const check = rewards?.find((r) => r.key === key)?.check ?? key.replace(/_\d+$/, "");
  const label = stockCheckName(check);
  return key === check ? label : `${label} (${key.split("_").pop()})`;
}

/** Trailing mean over `window` points, so a noisy per-step reward reads as a trend. */
export function smooth<T extends { step: number; value: number | null }>(points: T[], window = 5) {
  const out: { step: number; value: number | null }[] = [];
  const recent: number[] = [];
  for (const point of points) {
    if (point.value != null && Number.isFinite(point.value)) {
      recent.push(point.value);
      if (recent.length > window) recent.shift();
    }
    out.push({
      step: point.step,
      value: recent.length ? recent.reduce((a, b) => a + b, 0) / recent.length : null,
    });
  }
  return out;
}

const STATUS: Record<
  string,
  { label: string; variant: "info" | "success" | "error" | "neutral"; live?: boolean }
> = {
  baseline: { label: "Scoring base model", live: true, variant: "info" },
  cancelled: { label: "Stopped", variant: "neutral" },
  failed: { label: "Failed", variant: "error" },
  final: { label: "Scoring trained model", live: true, variant: "info" },
  queued: { label: "Queued", live: true, variant: "neutral" },
  starting: { label: "Starting GPU", live: true, variant: "info" },
  succeeded: { label: "Succeeded", variant: "success" },
  training: { label: "Training", live: true, variant: "info" },
};

export function RLStatusBadge({ status }: { status: string }) {
  const cfg = STATUS[status] ?? { label: status, variant: "neutral" as const };
  return (
    <Badge className="gap-1.5" variant={cfg.variant}>
      {cfg.live ? <Spinner size="sm" /> : null}
      {cfg.label}
    </Badge>
  );
}
