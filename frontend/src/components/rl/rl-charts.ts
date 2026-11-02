// Import charts from here, never statically: a static recharts import pulls the chunk
// into the initial bundle.

import { lazyChart } from "@/components/ui/lazy-chart";

export const RewardChart = lazyChart(
  () => import("@/components/rl/reward-chart").then((m) => ({ default: m.RewardChart })),
  { minHeight: 240 }
);
