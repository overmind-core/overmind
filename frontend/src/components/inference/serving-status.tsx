import { DeployedModelStatusBadge } from "@/components/inference/status-badge";
import { Badge } from "@/components/ui/badge";
import { domainStatus, type StatusTone, TONE_BADGE_VARIANT } from "@/lib/colors";
import type { InferenceLiveStats } from "@/openapi";

export type LiveState = "live" | "warming" | "dormant";

/** `recentlyActive` outranks the runner counts: for web_server workers HTTP
    traffic bypasses the input queue, so the counts read zero mid-inference. */
function deriveLiveState(live: InferenceLiveStats | undefined): LiveState {
  if (!live) return "dormant";
  if (live.recentlyActive) return "live";
  if (live.warming) return "warming";
  if ((live.numTotalRunners ?? 0) > 0) return "live";
  return "dormant";
}

const LIVE_TONE: Record<LiveState, StatusTone> = {
  dormant: domainStatus("dormant"),
  live: domainStatus("live"),
  warming: domainStatus("warming"),
};
const LIVE_LABEL: Record<LiveState, string> = {
  dormant: "Dormant",
  live: "Live",
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
      variant={TONE_BADGE_VARIANT[LIVE_TONE[state]]}
    >
      {LIVE_LABEL[state]}
    </Badge>
  );
}
