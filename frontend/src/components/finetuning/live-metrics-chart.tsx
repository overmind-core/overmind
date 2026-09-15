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

import { seriesColor } from "@/lib/colors";
import type { FinetuningProgress } from "@/lib/finetuning-progress";

// Pinned slots: train/eval keep one hue across every chart below.
const TRAIN_COLOR = seriesColor(0);
const EVAL_COLOR = seriesColor(1);
const TRAIN_ACC_COLOR = seriesColor(2);
const EVAL_ACC_COLOR = seriesColor(3);
const LR_COLOR = seriesColor(4);

function MiniTooltip({
  active,
  payload,
  label,
  xLabel = "step",
  formatValue = (v: number) => v.toFixed(4),
}: {
  active?: boolean;
  payload?: Array<{ name: string; value: number | null; color: string }>;
  label?: number | string;
  xLabel?: string;
  formatValue?: (v: number) => string;
}) {
  if (!active || !payload?.length) return null;
  const entries = payload.filter((e) => e.value != null);
  if (!entries.length) return null;
  return (
    <div className="rounded-md border border-border bg-popover px-3 py-2 text-xs">
      <p className="mb-1 font-medium text-muted-foreground">
        {xLabel} {label}
      </p>
      {entries.map((entry) => (
        <div className="flex items-center gap-2" key={entry.name}>
          <span className="inline-block size-2 rounded-xs" style={{ background: entry.color }} />
          <span className="text-muted-foreground">{entry.name}</span>
          <span className="ml-auto font-mono tabular-nums">
            {formatValue(entry.value as number)}
          </span>
        </div>
      ))}
    </div>
  );
}

function MiniChart({
  title,
  subtitle,
  children,
  height = 110,
}: {
  title: string;
  subtitle?: string;
  children: React.ReactNode;
  height?: number;
}) {
  return (
    <div className="space-y-1">
      <div className="flex items-baseline gap-2">
        <p className="text-xs font-semibold text-muted-foreground">{title}</p>
        {subtitle && <p className="text-xs text-muted-foreground/60">{subtitle}</p>}
      </div>
      <ResponsiveContainer height={height} width="100%">
        {children as React.ReactElement}
      </ResponsiveContainer>
    </div>
  );
}

const SHARED_LINE_PROPS = {
  connectNulls: true as const,
  dot: false as const,
  isAnimationActive: false as const,
  strokeWidth: 1.5,
  type: "monotone" as const,
};

function SharedAxes({ xLabel = "step" }: { xLabel?: string }) {
  return (
    <>
      <CartesianGrid className="stroke-border/40" strokeDasharray="3 3" />
      <XAxis
        dataKey="x"
        label={
          xLabel !== "step"
            ? { fontSize: 9, offset: -2, position: "insideBottomRight", value: xLabel }
            : undefined
        }
        tick={{ fontSize: 9 }}
        tickLine={false}
      />
      <YAxis domain={["auto", "auto"]} tick={{ fontSize: 9 }} tickLine={false} width={38} />
    </>
  );
}

interface LiveMetricsChartProps {
  progress: FinetuningProgress;
}

export function LiveMetricsChart({ progress }: LiveMetricsChartProps) {
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

  const hasStepEvalLoss = stepEvalLoss.length > 0;
  const hasStepEvalAcc = stepEvalAcc.length > 0;
  const hasStepAcc = stepTrainAcc.length > 0;
  const hasLr = stepLr.length > 0;

  return (
    <div className="space-y-5">
      {stepTrainLoss.length > 0 && (
        <MiniChart subtitle="per step" title="Train loss">
          <LineChart data={stepTrainLoss} margin={{ bottom: 0, left: 0, right: 8, top: 4 }}>
            <SharedAxes />
            {evalSteps.map((s) => (
              <ReferenceLine
                key={s}
                stroke="color-mix(in srgb, var(--muted-foreground) 20%, transparent)"
                x={s}
              />
            ))}
            <Tooltip content={<MiniTooltip formatValue={(v) => v.toFixed(4)} xLabel="step" />} />
            <Line {...SHARED_LINE_PROPS} dataKey="value" name="train loss" stroke={TRAIN_COLOR} />
          </LineChart>
        </MiniChart>
      )}

      {hasStepEvalLoss && (
        <MiniChart subtitle="per eval checkpoint" title="Val loss">
          <LineChart data={stepEvalLoss} margin={{ bottom: 0, left: 0, right: 8, top: 4 }}>
            <SharedAxes />
            <Tooltip content={<MiniTooltip formatValue={(v) => v.toFixed(4)} xLabel="step" />} />
            <Line
              {...SHARED_LINE_PROPS}
              dataKey="value"
              dot={{ fill: EVAL_COLOR, r: 4, strokeWidth: 0 }}
              name="val loss"
              stroke={EVAL_COLOR}
            />
          </LineChart>
        </MiniChart>
      )}

      {hasStepAcc && (
        <MiniChart subtitle="per step" title="Train accuracy">
          <LineChart data={stepTrainAcc} margin={{ bottom: 0, left: 0, right: 8, top: 4 }}>
            <SharedAxes />
            {evalSteps.map((s) => (
              <ReferenceLine
                key={s}
                stroke="color-mix(in srgb, var(--muted-foreground) 20%, transparent)"
                x={s}
              />
            ))}
            <Tooltip
              content={<MiniTooltip formatValue={(v) => `${v.toFixed(1)}%`} xLabel="step" />}
            />
            <Line
              {...SHARED_LINE_PROPS}
              dataKey="value"
              name="train acc"
              stroke={TRAIN_ACC_COLOR}
            />
          </LineChart>
        </MiniChart>
      )}

      {hasStepEvalAcc && (
        <MiniChart subtitle="per eval checkpoint" title="Val accuracy">
          <LineChart data={stepEvalAcc} margin={{ bottom: 0, left: 0, right: 8, top: 4 }}>
            <SharedAxes />
            <Tooltip
              content={<MiniTooltip formatValue={(v) => `${v.toFixed(1)}%`} xLabel="step" />}
            />
            <Line
              {...SHARED_LINE_PROPS}
              dataKey="value"
              dot={{ fill: EVAL_ACC_COLOR, r: 4, strokeWidth: 0 }}
              name="val acc"
              stroke={EVAL_ACC_COLOR}
            />
          </LineChart>
        </MiniChart>
      )}

      {hasLr && (
        <MiniChart height={90} subtitle="per step" title="Learning rate">
          <LineChart data={stepLr} margin={{ bottom: 0, left: 0, right: 8, top: 4 }}>
            <SharedAxes />
            <Tooltip
              content={<MiniTooltip formatValue={(v) => v.toExponential(3)} xLabel="step" />}
            />
            <Line {...SHARED_LINE_PROPS} dataKey="value" name="lr" stroke={LR_COLOR} />
          </LineChart>
        </MiniChart>
      )}

      {epochTrainLoss.length > 0 && (
        <MiniChart subtitle="epoch avg" title="Train loss (epoch)">
          <LineChart data={epochTrainLoss} margin={{ bottom: 0, left: 0, right: 8, top: 4 }}>
            <SharedAxes xLabel="epoch" />
            <Tooltip content={<MiniTooltip formatValue={(v) => v.toFixed(4)} xLabel="epoch" />} />
            <Line
              {...SHARED_LINE_PROPS}
              dataKey="value"
              dot={{ fill: TRAIN_COLOR, r: 4, strokeWidth: 0 }}
              name="train loss"
              stroke={TRAIN_COLOR}
            />
          </LineChart>
        </MiniChart>
      )}

      {epochEvalLoss.length > 0 && (
        <MiniChart subtitle="per epoch" title="Val loss (epoch)">
          <LineChart data={epochEvalLoss} margin={{ bottom: 0, left: 0, right: 8, top: 4 }}>
            <SharedAxes xLabel="epoch" />
            <Tooltip content={<MiniTooltip formatValue={(v) => v.toFixed(4)} xLabel="epoch" />} />
            <Line
              {...SHARED_LINE_PROPS}
              dataKey="value"
              dot={{ fill: EVAL_COLOR, r: 4, strokeWidth: 0 }}
              name="val loss"
              stroke={EVAL_COLOR}
            />
          </LineChart>
        </MiniChart>
      )}

      {epochTrainAcc.length > 0 && (
        <MiniChart subtitle="epoch avg" title="Train accuracy (epoch)">
          <LineChart data={epochTrainAcc} margin={{ bottom: 0, left: 0, right: 8, top: 4 }}>
            <SharedAxes xLabel="epoch" />
            <Tooltip
              content={<MiniTooltip formatValue={(v) => `${v.toFixed(1)}%`} xLabel="epoch" />}
            />
            <Line
              {...SHARED_LINE_PROPS}
              dataKey="value"
              dot={{ fill: TRAIN_ACC_COLOR, r: 4, strokeWidth: 0 }}
              name="train acc"
              stroke={TRAIN_ACC_COLOR}
            />
          </LineChart>
        </MiniChart>
      )}

      {epochEvalAcc.length > 0 && (
        <MiniChart subtitle="per epoch" title="Val accuracy (epoch)">
          <LineChart data={epochEvalAcc} margin={{ bottom: 0, left: 0, right: 8, top: 4 }}>
            <SharedAxes xLabel="epoch" />
            <Tooltip
              content={<MiniTooltip formatValue={(v) => `${v.toFixed(1)}%`} xLabel="epoch" />}
            />
            <Line
              {...SHARED_LINE_PROPS}
              dataKey="value"
              dot={{ fill: EVAL_ACC_COLOR, r: 4, strokeWidth: 0 }}
              name="val acc"
              stroke={EVAL_ACC_COLOR}
            />
          </LineChart>
        </MiniChart>
      )}
    </div>
  );
}
