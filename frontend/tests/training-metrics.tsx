import { useState } from "react";
import { createRoot } from "react-dom/client";

import { MultiSeriesChart } from "@/components/finetuning/finetuning-charts";
import type { MetricSeries } from "@/components/finetuning/loss-chart";
import { MonitorChartCard } from "@/components/finetuning/monitor-chart-card";
import { Button } from "@/components/ui/button";
import { PageHeader } from "@/components/ui/page-header";
import { TooltipProvider } from "@/components/ui/tooltip";
import { seriesColor } from "@/lib/colors";

import "../src/styles.css";

const cases: { title: string; series: MetricSeries[] }[] = [
  {
    series: [
      {
        color: seriesColor(0),
        id: "a",
        name: "Qwen3 0.6B",
        points: [
          { step: 0, value: 0.8 },
          { step: 20, value: 0.3 },
          { step: 40, value: 0.5 },
        ],
      },
      {
        color: seriesColor(1),
        id: "b",
        name: "Qwen3.5 0.8B",
        points: [
          { step: 0, value: 0.9 },
          { step: 10, value: 0.6 },
          { step: 40, value: 0.2 },
        ],
      },
    ],
    title: "Comparison",
  },
  {
    series: [
      { color: seriesColor(0), id: "zero", name: "Qwen3 0.6B", points: [{ step: 0, value: 0 }] },
    ],
    title: "One zero measurement",
  },
  {
    series: [
      {
        color: seriesColor(0),
        id: "flat",
        name: "Qwen3 0.6B",
        points: [
          { step: 0, value: 0.5 },
          { step: 100, value: 0.5 },
        ],
      },
    ],
    title: "Constant",
  },
  {
    series: [{ color: seriesColor(0), id: "empty", name: "Qwen3 0.6B", points: [] }],
    title: "Unmeasured",
  },
];

function Preview() {
  const [terminal, setTerminal] = useState(false);
  return (
    <TooltipProvider>
      <main className="mx-auto max-w-6xl space-y-5 p-4">
        <PageHeader
          description="Synthetic measurements. No job or provider call."
          title="Training metric fixtures"
        />
        <Button onClick={() => setTerminal(!terminal)} variant="secondary">
          {terminal ? "Show running" : "Show finished"}
        </Button>
        <div className="grid gap-5 md:grid-cols-2">
          {cases.map(({ title, series }) => (
            <MonitorChartCard
              emptyHint="No loss recorded"
              help="Fixture for endpoint labels, per-model summaries and missing measurements."
              key={title}
              summary={{ kind: "loss", series, terminal }}
              title={title}
            >
              {series.some((item) => item.points.length) ? (
                <MultiSeriesChart series={series} />
              ) : null}
            </MonitorChartCard>
          ))}
        </div>
      </main>
    </TooltipProvider>
  );
}

const root = createRoot(document.getElementById("app")!);
root.render(<Preview />);
import.meta.hot?.dispose(() => root.unmount());
