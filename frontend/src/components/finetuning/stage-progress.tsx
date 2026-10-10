import { useEffect, useState } from "react";

import { ProgressBar } from "@/components/finetuning/finetuning-chrome";
import type { FinetuningProgress } from "@/lib/finetuning-progress";

function age(seconds: number): string {
  const value = Math.max(0, Math.floor(seconds));
  return value < 60 ? `${value}s` : `${Math.floor(value / 60)}m ${value % 60}s`;
}

export function StageProgress({ progress }: { progress: FinetuningProgress }) {
  const [now, setNow] = useState(() => Date.now() / 1000);
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now() / 1000), 1000);
    return () => clearInterval(timer);
  }, []);
  const detail = progress.diagnostics;
  const completed = detail?.completed;
  const total = detail?.total;
  const measured =
    typeof completed === "number" &&
    Number.isFinite(completed) &&
    completed >= 0 &&
    typeof total === "number" &&
    Number.isFinite(total) &&
    total > 0;
  const percent = measured ? Math.min(100, Math.floor((completed / total) * 100)) : null;
  const stageStart = detail?.stage_started_at;
  const lastProgress = detail?.last_progress_at;
  const heartbeat = detail?.heartbeat_at;
  const loading = progress.stage !== "transferring";
  const acknowledged = detail?.measurement === "provider_acknowledged_files";
  const freshness = [
    stageStart != null ? `${age(now - stageStart)} in this stage` : "Stage start not reported",
    lastProgress != null
      ? `${age(now - lastProgress)} since last progress`
      : "Progress not reported",
    acknowledged ? "Live upload-byte progress unavailable" : null,
    loading
      ? heartbeat != null
        ? `${age(now - heartbeat)} since worker heartbeat`
        : "Worker heartbeat not reported"
      : null,
    loading && percent == null ? "Loading percentage not reported" : null,
  ]
    .filter(Boolean)
    .join(" · ");
  return (
    <div className="flex flex-col gap-1.5">
      {percent != null && (
        <ProgressBar
          label={loading ? "Current loading stage" : "Current transfer stage"}
          percent={percent}
          terminal={false}
        />
      )}
      <p className="font-mono text-xs tabular-nums text-muted-foreground">{freshness}</p>
    </div>
  );
}
