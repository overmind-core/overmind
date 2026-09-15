import { Link } from "@tanstack/react-router";

import { Badge } from "@/components/ui/badge";
import { Icon } from "@/components/ui/icons";
import { Spinner } from "@/components/ui/spinner";
import { INTENT_LABEL, type UsedVersionInfo } from "@/hooks/use-datasets";
import { cn } from "@/lib/utils";
import type { Dataset } from "@/openapi";

export const SOURCE_KIND_LABEL: Record<string, string> = {
  file: "File",
  traces: "Traces",
};

/** `train` / `eval` are what a consumer needs; `pending` is not decided yet. */
export function IntentBadge({
  intent,
  className,
}: {
  intent: string | null | undefined;
  className?: string;
}) {
  if (!intent) return null;
  return (
    <Badge className={className} size="chip" variant={intent === "pending" ? "neutral" : "success"}>
      {INTENT_LABEL[intent] ?? intent}
    </Badge>
  );
}

/** Where the dataset stands. Renders nothing when idle. */
export function StateBadge({
  dataset,
  className,
}: {
  dataset: Pick<Dataset, "state" | "error"> | null | undefined;
  className?: string;
}) {
  if (!dataset) return null;
  if (
    dataset.state === "landing" ||
    dataset.state === "running" ||
    dataset.state === "diagnosing"
  ) {
    return (
      <Badge className={cn("gap-1", className)} size="chip" variant="info">
        <Spinner className="size-3" />
        {dataset.state === "landing"
          ? "Landing"
          : dataset.state === "running"
            ? "Running"
            : "Agent"}
      </Badge>
    );
  }
  if (dataset.state === "error") {
    return (
      <Badge className={className} size="chip" title={dataset.error} variant="destructive">
        Failed
      </Badge>
    );
  }
  return null;
}

/** Back-link from a run, job or experiment to the exact version it read. */
export function UsedVersionChip({
  info,
  datasetName,
  className,
}: {
  info: UsedVersionInfo | null;
  datasetName?: string | null;
  className?: string;
}) {
  if (!info) return null;
  const label = datasetName?.trim() ? `${datasetName.trim()} ${info.version}` : info.version;
  return (
    <Link
      className={cn(
        "inline-flex h-6 max-w-full items-center gap-1 rounded-sm border border-border/70 bg-card px-1.5 font-mono text-xs text-foreground hover:bg-accent/60",
        className
      )}
      params={{ datasetId: info.datasetId }}
      search={{ cell: info.id }}
      title={`${info.rows.toLocaleString()} rows · ${info.title}`}
      to="/datasets/$datasetId"
    >
      <Icon.dataset className="size-3 shrink-0 text-muted-foreground" />
      <span className="truncate">{label}</span>
      <Icon.lock className="size-3 shrink-0 text-muted-foreground" />
    </Link>
  );
}
