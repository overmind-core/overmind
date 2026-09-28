import { DeployedModelStatusBadge } from "@/components/inference/status-badge";
import { Badge } from "@/components/ui/badge";
import { domainStatus, type StatusTone, TONE_BADGE_VARIANT } from "@/lib/colors";
import type { InferenceLiveStats } from "@/openapi";

export type LiveState = "live" | "warming" | "dormant" | "unknown";

/** `recentlyActive` outranks the runner counts: for web_server workers HTTP
    traffic bypasses the input queue, so the counts read zero mid-inference. */
function deriveLiveState(live: InferenceLiveStats | undefined): LiveState {
  if (!live) return "unknown";
  if (live.recentlyActive) return "live";
  if (live.warming) return "warming";
  if (!live.available || live.numTotalRunners == null) return "unknown";
  if ((live.numTotalRunners ?? 0) > 0) return "live";
  return "dormant";
}

const LIVE_TONE: Record<LiveState, StatusTone> = {
  dormant: domainStatus("dormant"),
  live: domainStatus("live"),
  unknown: "neutral",
  warming: domainStatus("warming"),
};
const LIVE_LABEL: Record<LiveState, string> = {
  dormant: "Asleep",
  live: "Warm",
  unknown: "Unknown",
  warming: "Warming",
};

export function ServingStatusBadge({
  status,
  live,
}: {
  status: string;
  live: InferenceLiveStats | undefined;
}) {
  if (status !== "ready") return <DeployedModelStatusBadge status={status} />;
  const state = deriveLiveState(live);
  return (
    <Badge
      className="w-24 justify-center whitespace-nowrap text-xs font-medium tracking-normal"
      title={`Deployment ready. Worker ${LIVE_LABEL[state].toLowerCase()}.`}
      variant={TONE_BADGE_VARIANT[LIVE_TONE[state]]}
    >
      {LIVE_LABEL[state]}
    </Badge>
  );
}
