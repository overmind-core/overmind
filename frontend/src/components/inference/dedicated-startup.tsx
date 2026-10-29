import { useEffect, useRef, useState } from "react";

import type { DedicatedDeployment } from "@/openapi";

// The cold and snapshot paths share these milestones; provider events within a
// milestone do not advance the bar or imply elapsed-time progress.
const STARTUP_MILESTONES = [
  ["starting_worker", "starting_engine"],
  [
    "refreshing_volumes",
    "validating_weights",
    "preparing_snapshot_artifact",
    "snapshot_artifact_ready",
    "starting_engine_process",
  ],
  ["allocating_weight_memory", "loading_weights", "restoring_weights", "restored_weights"],
  [
    "compiling_model",
    "allocating_kv_cache",
    "capturing_graphs",
    "warming_kernels",
    "adapter_ready",
  ],
  ["checking_engine_health", "engine_ready"],
  ["online"],
];

export function DedicatedStartupProgress({ row }: { row: DedicatedDeployment }) {
  const track = useRef<HTMLDivElement>(null);
  const [blockCount, setBlockCount] = useState(1);
  useEffect(() => {
    const element = track.current;
    if (!element) return;
    const observer = new ResizeObserver(([entry]) => {
      setBlockCount(Math.max(1, Math.floor((entry.contentRect.width + 3) / 15)));
    });
    observer.observe(element);
    return () => observer.disconnect();
  }, []);
  const startup = row.startup;
  const label = startup?.label ?? "Starting worker";
  const reached = Math.max(
    0,
    ...[row.status, startup?.stage, ...(startup?.logs.map((entry) => entry.stage) ?? [])].map(
      (stage) =>
        STARTUP_MILESTONES.findIndex((milestone) => stage != null && milestone.includes(stage))
    )
  );
  const total = STARTUP_MILESTONES.length - 1;
  return (
    <div className="flex min-h-7 min-w-0 flex-1 items-center gap-2 overflow-hidden rounded-sm border border-border p-0.5">
      <span
        aria-live="polite"
        className="max-w-[60%] shrink-0 truncate rounded-xs bg-primary px-2 py-0.5 text-xs text-primary-foreground"
      >
        {label}
      </span>
      <div className="min-w-0 flex-1 pr-1">
        <div
          aria-label="Startup stages"
          aria-valuemax={total}
          aria-valuemin={0}
          aria-valuenow={reached}
          aria-valuetext={`${label} · startup milestone ${reached + 1} of ${STARTUP_MILESTONES.length}`}
          className="flex h-3 w-full gap-[3px]"
          ref={track}
          role="progressbar"
        >
          {Array.from({ length: blockCount }, (_, index) => (
            <span
              aria-hidden="true"
              className={`min-w-0 flex-1 ${index < Math.floor((reached / total) * blockCount) ? "bg-primary" : "bg-muted"}`}
              key={index}
            />
          ))}
        </div>
      </div>
    </div>
  );
}
