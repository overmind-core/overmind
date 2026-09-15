// Import charts from here, never statically: one static import of a recharts
// module anywhere pulls the whole chunk back into the initial bundle.

import { lazyChart } from "@/components/ui/lazy-chart";

export const LossChart = lazyChart(
  () => import("@/components/finetuning/loss-chart").then((m) => ({ default: m.LossChart })),
  { minHeight: 160 }
);

export const MetricSeriesChart = lazyChart(
  () =>
    import("@/components/finetuning/loss-chart").then((m) => ({ default: m.MetricSeriesChart })),
  { minHeight: 160 }
);

export const MultiSeriesChart = lazyChart(
  () => import("@/components/finetuning/loss-chart").then((m) => ({ default: m.MultiSeriesChart })),
  { minHeight: 160 }
);

export const ClassMetricsTable = lazyChart(
  () =>
    import("@/components/finetuning/class-metrics").then((m) => ({ default: m.ClassMetricsTable })),
  { minHeight: 180 }
);

export const ClassSeriesChart = lazyChart(
  () =>
    import("@/components/finetuning/class-metrics").then((m) => ({ default: m.ClassSeriesChart })),
  { minHeight: 180 }
);

export const ConfusionMatrixGrid = lazyChart(
  () =>
    import("@/components/finetuning/class-metrics").then((m) => ({
      default: m.ConfusionMatrixGrid,
    })),
  { minHeight: 180 }
);

export const LiveMetricsChart = lazyChart(
  () =>
    import("@/components/finetuning/live-metrics-chart").then((m) => ({
      default: m.LiveMetricsChart,
    })),
  { minHeight: 320 }
);
