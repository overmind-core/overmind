import {
  CartesianGrid,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { GraphTooltip } from "@/components/ui/graph-tooltip";
import { seriesColor } from "@/lib/colors";
import type { FinetuningProgress } from "@/lib/finetuning-progress";

// Pinned slots: train/eval keep one hue across every chart below.
const TRAIN_COLOR = seriesColor(0);
const EVAL_COLOR = seriesColor(1);
const TRAIN_ACC_COLOR = seriesColor(2);
const EVAL_ACC_COLOR = seriesColor(3);
const LR_COLOR = seriesColor(4);

interface LiveMetricsChartProps {
  modelName: string;
  progress: FinetuningProgress;
}

export function LiveMetricsChart({ progress, modelName }: LiveMetricsChartProps) {
  const trainHistory = progress.metrics_history ?? [];
  const evalHistory = progress.eval_history ?? [];

  if (trainHistory.length === 0 && evalHistory.length === 0) return null;

  const stepTrainLoss = trainHistory
    .filter((p) => p.train_loss != null)
    .map((p) => ({ value: p.train_loss!, x: p.step }));

  const stepEvalLoss = evalHistory
    .filter((p) => p.eval_loss != null)
    .map((p) => ({ value: p.eval_loss!, x: p.step }));

  const stepTrainAcc = trainHistory
    .filter((p) => p.token_accuracy != null)
    .map((p) => ({ value: +(p.token_accuracy! * 100).toFixed(2), x: p.step }));

  const stepEvalAcc = evalHistory
    .filter((p) => p.eval_token_accuracy != null)
    .map((p) => ({ value: +(p.eval_token_accuracy! * 100).toFixed(2), x: p.step }));

  const stepLr = trainHistory.filter((p) => p.lr != null).map((p) => ({ value: p.lr!, x: p.step }));

  const epochEvalLoss = evalHistory
    .filter((p) => p.epoch != null && p.eval_loss != null)
    .map((p) => ({ value: p.eval_loss!, x: +p.epoch!.toFixed(2) }));

  const epochEvalAcc = evalHistory
    .filter((p) => p.epoch != null && p.eval_token_accuracy != null)
    .map((p) => ({ value: +(p.eval_token_accuracy! * 100).toFixed(2), x: +p.epoch!.toFixed(2) }));

  const { epochTrainLoss, epochTrainAcc } = (() => {
    if (!evalHistory.length || !trainHistory.length)
      return { epochTrainAcc: [], epochTrainLoss: [] };
    const lossResult: { x: number; value: number }[] = [];
    const accResult: { x: number; value: number }[] = [];
    const epochPoints = evalHistory
      .filter((e) => e.epoch != null)
      .sort((a, b) => a.epoch! - b.epoch!);
    let prevStep = 0;
    for (const ep of epochPoints) {
      const epochStep = ep.step ?? 0;
      const windowPts = trainHistory.filter(
        (p) => p.step != null && p.step > prevStep && p.step <= epochStep
      );
      if (windowPts.length > 0) {
        const lossPts = windowPts.filter((p) => p.train_loss != null);
        if (lossPts.length > 0) {
          const avg = lossPts.reduce((s, p) => s + p.train_loss!, 0) / lossPts.length;
          lossResult.push({ value: +avg.toFixed(6), x: +ep.epoch!.toFixed(2) });
        }
        const accPts = windowPts.filter((p) => p.token_accuracy != null);
        if (accPts.length > 0) {
          const avg = accPts.reduce((s, p) => s + p.token_accuracy!, 0) / accPts.length;
          accResult.push({ value: +(avg * 100).toFixed(2), x: +ep.epoch!.toFixed(2) });
        }
      }
      prevStep = epochStep;
    }
    return { epochTrainAcc: accResult, epochTrainLoss: lossResult };
  })();

  const evalSteps = evalHistory.map((e) => e.step).filter(Boolean);

  const loss = (value: number) => value.toFixed(4);
  const accuracy = (value: number) => `${value.toFixed(1)}%`;
  const charts = [
    {
      color: TRAIN_COLOR,
      data: stepTrainLoss,
      format: loss,
      name: "train loss",
      referenceSteps: evalSteps,
      subtitle: "per step",
      title: "Train loss",
    },
    {
      color: EVAL_COLOR,
      data: stepEvalLoss,
      dots: true,
      format: loss,
      name: "val loss",
      subtitle: "per eval checkpoint",
      title: "Val loss",
    },
    {
      color: TRAIN_ACC_COLOR,
      data: stepTrainAcc,
      format: accuracy,
      name: "train acc",
      referenceSteps: evalSteps,
      subtitle: "per step",
      title: "Train accuracy",
    },
    {
      color: EVAL_ACC_COLOR,
      data: stepEvalAcc,
      dots: true,
      format: accuracy,
      name: "val acc",
      subtitle: "per eval checkpoint",
      title: "Val accuracy",
    },
    {
      color: LR_COLOR,
      data: stepLr,
      format: (value: number) => value.toExponential(3),
      height: 90,
      name: "lr",
      subtitle: "per step",
      title: "Learning rate",
    },
    {
      color: TRAIN_COLOR,
      data: epochTrainLoss,
      dots: true,
      epoch: true,
      format: loss,
      name: "train loss",
      subtitle: "epoch avg",
      title: "Train loss (epoch)",
    },
    {
      color: EVAL_COLOR,
      data: epochEvalLoss,
      dots: true,
      epoch: true,
      format: loss,
      name: "val loss",
      subtitle: "per epoch",
      title: "Val loss (epoch)",
    },
    {
      color: TRAIN_ACC_COLOR,
      data: epochTrainAcc,
      dots: true,
      epoch: true,
      format: accuracy,
      name: "train acc",
      subtitle: "epoch avg",
      title: "Train accuracy (epoch)",
    },
    {
      color: EVAL_ACC_COLOR,
      data: epochEvalAcc,
      dots: true,
      epoch: true,
      format: accuracy,
      name: "val acc",
      subtitle: "per epoch",
      title: "Val accuracy (epoch)",
    },
  ];

  return (
    <div className="space-y-5">
      {charts
        .filter((chart) => chart.data.length > 0)
        .map((chart) => (
          <div className="space-y-1" key={chart.title}>
            <div className="flex items-baseline gap-2">
              <p className="text-xs font-semibold text-muted-foreground">{chart.title}</p>
              <p className="text-xs text-muted-foreground/60">{chart.subtitle}</p>
            </div>
            <ResponsiveContainer height={chart.height ?? 110} width="100%">
              <LineChart data={chart.data} margin={{ bottom: 0, left: 0, right: 8, top: 4 }}>
                <CartesianGrid className="stroke-border/40" strokeDasharray="3 3" />
                <XAxis
                  dataKey="x"
                  label={
                    chart.epoch
                      ? { fontSize: 9, offset: -2, position: "insideBottomRight", value: "epoch" }
                      : undefined
                  }
                  tick={{ fontSize: 9 }}
                  tickLine={false}
                />
                <YAxis
                  domain={["auto", "auto"]}
                  tick={{ fontSize: 9 }}
                  tickLine={false}
                  width={38}
                />
                {chart.referenceSteps?.map((step) => (
                  <ReferenceLine
                    key={step}
                    stroke="color-mix(in srgb, var(--muted-foreground) 20%, transparent)"
                    x={step}
                  />
                ))}
                <Tooltip
                  content={({ active, payload, label }) => (
                    <GraphTooltip
                      active={active}
                      label={`${chart.epoch ? "epoch" : "step"} ${label}`}
                      nameFormatter={() => modelName}
                      payload={payload}
                      valueFormatter={chart.format}
                    />
                  )}
                />
                <Line
                  connectNulls
                  dataKey="value"
                  dot={chart.dots ? { fill: chart.color, r: 4, strokeWidth: 0 } : false}
                  isAnimationActive={false}
                  name={chart.name}
                  stroke={chart.color}
                  strokeWidth={1.5}
                  type="monotone"
                />
              </LineChart>
            </ResponsiveContainer>
          </div>
        ))}
    </div>
  );
}
