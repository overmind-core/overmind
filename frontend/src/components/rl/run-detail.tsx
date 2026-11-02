import { useMemo, useState } from "react";

import { duration, pct, rewardLabel, score, smooth } from "@/components/rl/format";
import { RewardChart } from "@/components/rl/rl-charts";
import type {
  Attempt,
  CheckOutcome,
  HeldOutSample,
  RewardConfig,
  RLStepRecord,
  StageMetrics,
  Verdict,
} from "@/components/rl/types";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { DeltaChip, fromScore } from "@/components/ui/delta-chip";
import { EmptyState } from "@/components/ui/empty-state";
import { Icon } from "@/components/ui/icons";
import { Progress } from "@/components/ui/progress";
import { SectionCard } from "@/components/ui/section-card";
import { Slider } from "@/components/ui/slider";
import { Spinner } from "@/components/ui/spinner";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { seriesColor } from "@/lib/colors";
import { cn } from "@/lib/utils";
import type { RLRunDetail } from "@/openapi";

const STAGES = [
  { key: "starting", label: "Load model" },
  { key: "baseline", label: "Score base model" },
  { key: "training", label: "Train" },
  { key: "final", label: "Score trained model" },
  { key: "succeeded", label: "Compare" },
] as const;

const STAGE_INDEX: Record<string, number> = {
  baseline: 1,
  cancelled: -1,
  failed: -1,
  final: 3,
  queued: 0,
  starting: 0,
  succeeded: 5,
  training: 2,
};

export function RunDetail({ run }: { run: RLRunDetail }) {
  const steps = (run.steps ?? []) as RLStepRecord[];
  const rewards = (run.rewards ?? []) as RewardConfig[];
  const baseline = (run.baseline ?? null) as StageMetrics | null;
  const final = (run._final ?? null) as StageMetrics | null;
  const samples = (run.samples ?? []) as HeldOutSample[];
  const maxSteps = Number(run.hyperparameters?.max_steps ?? 0);

  return (
    <div className="flex flex-col gap-4">
      <StageTrack maxSteps={maxSteps} run={run} />
      {run.status === "failed" && run.error ? (
        <div className="rounded-md border border-destructive/40 bg-destructive/10 px-4 py-3 text-sm text-destructive">
          {run.error}
        </div>
      ) : null}
      <Headline baseline={baseline} final={final} run={run} steps={steps} />
      {run.verdict && Object.keys(run.verdict).length ? (
        <VerdictBanner samples={samples} verdict={run.verdict as Verdict} />
      ) : null}
      <div className="grid gap-4 xl:grid-cols-2">
        <RewardCurve baseline={baseline} maxSteps={maxSteps} steps={steps} />
        <CheckCurves baseline={baseline} maxSteps={maxSteps} rewards={rewards} steps={steps} />
      </div>
      <AttemptGroups rewards={rewards} steps={steps} />
      <HeldOutComparison rewards={rewards} samples={samples} />
      <Recipe run={run} />
    </div>
  );
}

function StageTrack({ run, maxSteps }: { run: RLRunDetail; maxSteps: number }) {
  const current = STAGE_INDEX[run.status] ?? 0;
  const ended = run.status === "failed" || run.status === "cancelled";
  const reached = ended ? reachedBefore(run) : current;
  return (
    <div className="grid grid-cols-5 gap-2">
      {STAGES.map((stage, index) => {
        const done = index < reached || run.status === "succeeded";
        const active = !ended && index === reached && run.status !== "succeeded";
        const stopped = ended && index === reached;
        return (
          <div
            className={cn(
              "flex flex-col gap-2 rounded-md border px-3 py-2.5",
              done && "border-success/40 bg-success/10",
              active && "border-info/40 bg-info/10",
              stopped && "border-destructive/40 bg-destructive/10",
              !done && !active && !stopped && "border-border/70 bg-wash-subtle"
            )}
            key={stage.key}
          >
            <div className="flex items-center gap-2 text-sm font-medium">
              {done ? (
                <Icon.success className="size-4 text-success" />
              ) : active ? (
                <Spinner size="sm" />
              ) : stopped ? (
                <Icon.failed className="size-4 text-destructive" />
              ) : (
                <span className="size-4 rounded-sm border border-border/70" />
              )}
              <span className={cn(!done && !active && "text-muted-foreground")}>{stage.label}</span>
            </div>
            {stage.key === "training" ? (
              <div className="flex items-center gap-2">
                <Progress
                  className="h-1"
                  label="Training steps"
                  percent={maxSteps ? (100 * run.stepsCompleted) / maxSteps : null}
                />
                <span className="shrink-0 font-mono text-xs tabular-nums text-muted-foreground">
                  {run.stepsCompleted}/{maxSteps}
                </span>
              </div>
            ) : null}
          </div>
        );
      })}
    </div>
  );
}

function reachedBefore(run: RLRunDetail) {
  if (run._final && Object.keys(run._final).length) return 4;
  if (run.stepsCompleted > 0) return 2;
  if (run.baseline && Object.keys(run.baseline).length) return 2;
  return run.startedAt ? 1 : 0;
}

function Stat({
  label,
  children,
  caption,
}: {
  label: string;
  children: React.ReactNode;
  caption?: React.ReactNode;
}) {
  return (
    <div className="flex min-w-0 flex-col gap-1 rounded-md border border-border/70 bg-wash-subtle px-4 py-3">
      <span className="text-xs text-muted-foreground">{label}</span>
      <div className="flex items-baseline gap-2 font-mono text-2xl font-semibold tabular-nums">
        {children}
      </div>
      {caption ? <span className="text-xs text-muted-foreground">{caption}</span> : null}
    </div>
  );
}

function BeforeAfter({
  before,
  after,
  format,
}: {
  before: number | null | undefined;
  after: number | null | undefined;
  format: (v: number | null | undefined) => string;
}) {
  return (
    <>
      <span className={cn(after != null && "text-muted-foreground")}>{format(before)}</span>
      {after != null ? (
        <>
          <Icon.forward className="size-4 self-center text-muted-foreground" />
          <span>{format(after)}</span>
          {before != null ? (
            <DeltaChip className="self-center" value={fromScore(after - before)} />
          ) : null}
        </>
      ) : null}
    </>
  );
}

function Headline({
  run,
  baseline,
  final,
  steps,
}: {
  run: RLRunDetail;
  baseline: StageMetrics | null;
  final: StageMetrics | null;
  steps: RLStepRecord[];
}) {
  const recent = smooth(
    steps.map((s) => ({ step: s.step, value: s.metrics?.reward ?? s.batch?.mean_reward ?? null }))
  ).at(-1)?.value;
  return (
    <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
      <Stat
        caption={`${run.holdoutRows} held-out prompts, never trained on`}
        label="Held-out pass rate"
      >
        <BeforeAfter after={final?.pass_rate} before={baseline?.pass_rate} format={pct} />
      </Stat>
      <Stat caption="Weighted mean of the reward checks" label="Held-out mean reward">
        <BeforeAfter after={final?.mean_reward} before={baseline?.mean_reward} format={score} />
      </Stat>
      <Stat
        caption={
          steps.length ? `Mean of the last 5 steps · ${steps.length} recorded` : "No steps yet"
        }
        label="Training reward"
      >
        {score(recent)}
      </Stat>
      <Stat caption={`${run.gpu || "GPU"} · measured by the worker`} label="GPU time">
        {run.gpuSeconds != null ? duration(run.gpuSeconds) : "—"}
      </Stat>
    </div>
  );
}

function VerdictBanner({ verdict, samples }: { verdict: Verdict; samples: HeldOutSample[] }) {
  const paired = samples.filter((s) => s.baseline && s.final);
  const fixed = paired.filter((s) => s.final?.passed && !s.baseline?.passed).length;
  const broken = paired.filter((s) => s.baseline?.passed && !s.final?.passed).length;
  const tone =
    verdict.decision === "promote"
      ? "border-success/40 bg-success/10 text-success"
      : verdict.decision === "review"
        ? "border-warning/40 bg-warning/10 text-warning"
        : "border-border bg-wash-raised text-muted-foreground";
  const title =
    verdict.decision === "promote"
      ? "Promote the trained adapter"
      : verdict.decision === "review"
        ? "Review before promoting"
        : "Keep the current model";
  return (
    <div
      className={cn(
        "flex flex-wrap items-center gap-x-6 gap-y-2 rounded-md border px-4 py-3",
        tone
      )}
    >
      <div className="flex items-center gap-2 font-semibold">
        {verdict.decision === "promote" ? (
          <Icon.trophy className="size-4" />
        ) : (
          <Icon.warning className="size-4" />
        )}
        {title}
      </div>
      <span className="text-sm text-foreground">{verdict.reason}</span>
      <span className="ml-auto flex gap-2 text-xs">
        <Badge variant="success">{fixed} fixed</Badge>
        <Badge variant={broken ? "error" : "neutral"}>{broken} broken</Badge>
      </span>
    </div>
  );
}

function RewardCurve({
  steps,
  baseline,
  maxSteps,
}: {
  steps: RLStepRecord[];
  baseline: StageMetrics | null;
  maxSteps: number;
}) {
  const reward = steps.map((s) => ({
    step: s.step,
    value: s.metrics?.reward ?? s.batch?.mean_reward ?? null,
  }));
  const passRate = steps.map((s) => ({ step: s.step, value: s.batch?.pass_rate ?? null }));
  return (
    <SectionCard
      description="Each step samples a group of attempts per prompt; the reward is the checks' weighted mean."
      icon={<Icon.chart className="size-4" />}
      title="Training reward"
    >
      {steps.length ? (
        <RewardChart
          maxSteps={maxSteps}
          references={
            baseline
              ? [
                  {
                    color: "var(--muted-foreground)",
                    label: "base model",
                    value: baseline.mean_reward,
                  },
                ]
              : []
          }
          series={[
            {
              color: seriesColor(0),
              faint: true,
              id: "reward:raw",
              name: "Reward",
              points: reward,
            },
            {
              color: seriesColor(0),
              id: "reward",
              name: "Reward (5-step mean)",
              points: smooth(reward),
            },
            {
              color: seriesColor(1),
              id: "pass",
              name: "Pass rate (5-step mean)",
              points: smooth(passRate),
            },
          ]}
        />
      ) : (
        <ChartWaiting />
      )}
      <Legend
        items={[
          { color: seriesColor(0), label: "Reward" },
          { color: seriesColor(1), label: "Attempts passing every check" },
        ]}
      />
    </SectionCard>
  );
}

function CheckCurves({
  steps,
  rewards,
  baseline,
  maxSteps,
}: {
  steps: RLStepRecord[];
  rewards: RewardConfig[];
  baseline: StageMetrics | null;
  maxSteps: number;
}) {
  const keys = rewards.map((r) => r.key);
  return (
    <SectionCard
      description="Mean score of each check over the attempts it scored; abstentions are left out."
      icon={<Icon.eval className="size-4" />}
      title="Reward checks"
    >
      {steps.length ? (
        <RewardChart
          maxSteps={maxSteps}
          references={keys.flatMap((key, i) => {
            const value = baseline?.per_check?.[key]?.mean;
            return value == null
              ? []
              : [{ color: seriesColor(i + 3), label: `base ${rewardLabel(key, rewards)}`, value }];
          })}
          series={keys.map((key, i) => ({
            color: seriesColor(i + 3),
            id: key,
            name: rewardLabel(key, rewards),
            points: smooth(
              steps.map((s) => ({ step: s.step, value: s.batch?.per_check?.[key]?.mean ?? null }))
            ),
          }))}
        />
      ) : (
        <ChartWaiting />
      )}
      <Legend
        items={keys.map((key, i) => ({
          color: seriesColor(i + 3),
          label: rewardLabel(key, rewards),
        }))}
      />
    </SectionCard>
  );
}

function ChartWaiting() {
  return (
    <div className="flex h-60 items-center justify-center gap-2 text-sm text-muted-foreground">
      <Spinner size="sm" /> Waiting for the first training step
    </div>
  );
}

function Legend({ items }: { items: { color: string; label: string }[] }) {
  return (
    <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted-foreground">
      {items.map((item) => (
        <span className="flex items-center gap-1.5" key={item.label}>
          <span className="h-0.5 w-4 rounded-sm" style={{ backgroundColor: item.color }} />
          {item.label}
        </span>
      ))}
    </div>
  );
}

function CheckChip({ label, outcome }: { label: string; outcome: CheckOutcome | undefined }) {
  if (!outcome) return null;
  const passed = outcome.outcome === "scored" && (outcome.score ?? 0) >= 0.5;
  const variant =
    outcome.outcome === "scored"
      ? passed
        ? "success"
        : "error"
      : outcome.outcome === "error"
        ? "warning"
        : "neutral";
  const value =
    outcome.outcome === "scored"
      ? score(outcome.score)
      : outcome.outcome === "error"
        ? "error"
        : "abstained";
  return (
    <Badge size="chip" title={outcome.explanation} variant={variant}>
      {label} · {value}
    </Badge>
  );
}

function AttemptGroups({ steps, rewards }: { steps: RLStepRecord[]; rewards: RewardConfig[] }) {
  const groups = steps.filter((s) => s.group?.attempts?.length);
  const [picked, setPicked] = useState<number | null>(null);
  const index = picked == null ? groups.length - 1 : Math.min(picked, groups.length - 1);
  const current = groups[index];
  const attempts = current?.group.attempts ?? [];
  const best = Math.max(...attempts.map((a) => a.reward), 0);

  return (
    <SectionCard
      description="One prompt per step, with every attempt the model sampled for it. GRPO pushes up the attempts that beat their group's mean."
      headerEnd={
        groups.length ? (
          <div className="flex items-center gap-2">
            <Button
              aria-label="Previous step"
              disabled={index <= 0}
              onClick={() => setPicked(Math.max(0, index - 1))}
              size="icon-sm"
              variant="ghost"
            >
              <Icon.chevronLeft />
            </Button>
            <span className="w-24 text-center font-mono text-xs tabular-nums text-muted-foreground">
              step {current?.step} of {groups.at(-1)?.step}
            </span>
            <Button
              aria-label="Next step"
              disabled={index >= groups.length - 1}
              onClick={() => setPicked(index + 1 >= groups.length - 1 ? null : index + 1)}
              size="icon-sm"
              variant="ghost"
            >
              <Icon.chevronRight />
            </Button>
          </div>
        ) : null
      }
      icon={<Icon.target className="size-4" />}
      title="Sampled attempts"
    >
      {current ? (
        <div className="flex flex-col gap-3">
          {groups.length > 1 ? (
            <Slider
              aria-label="Training step"
              max={groups.length - 1}
              min={0}
              onValueChange={([value]) => setPicked(value >= groups.length - 1 ? null : value)}
              step={1}
              value={[index]}
            />
          ) : null}
          <div className="flex items-start justify-between gap-3">
            <p className="line-clamp-2 text-sm">
              <span className="text-muted-foreground">Prompt · </span>
              {current.group.prompt}
            </p>
            {attempts.length > 1 && attempts.every((a) => a.reward === attempts[0].reward) ? (
              <Badge
                className="shrink-0"
                title="Every advantage is zero, so this group moves no weights"
                variant="neutral"
              >
                Same score for every attempt · no learning signal
              </Badge>
            ) : (
              <Badge className="shrink-0" variant="info">
                Spread{" "}
                {score(
                  Math.max(...attempts.map((a) => a.reward)) -
                    Math.min(...attempts.map((a) => a.reward))
                )}
              </Badge>
            )}
          </div>
          <div className="grid gap-2 md:grid-cols-2 xl:grid-cols-4">
            {attempts.map((attempt, i) => (
              <AttemptCard
                attempt={attempt}
                best={attempt.reward === best && best > 0}
                key={`${current.step}-${i}`}
                mean={attempts.reduce((sum, a) => sum + a.reward, 0) / attempts.length}
                rewards={rewards}
              />
            ))}
          </div>
        </div>
      ) : (
        <EmptyState
          description="Groups appear after the first training step."
          icon={Icon.target}
          size="compact"
          title="No attempts yet"
        />
      )}
    </SectionCard>
  );
}

function AttemptCard({
  attempt,
  rewards,
  mean,
  best,
}: {
  attempt: Attempt;
  rewards: RewardConfig[];
  mean: number;
  best: boolean;
}) {
  const advantage = attempt.reward - mean;
  return (
    <div
      className={cn(
        "flex min-w-0 flex-col gap-2 rounded-md border p-3",
        attempt.passed ? "border-success/40 bg-success/10" : "border-border/70 bg-wash-subtle"
      )}
    >
      <div className="flex items-center gap-2">
        <div className="h-1.5 flex-1 overflow-hidden rounded-sm bg-muted">
          <div
            className={cn("h-full rounded-sm", attempt.passed ? "bg-success" : "bg-info")}
            style={{ width: `${Math.round(attempt.reward * 100)}%` }}
          />
        </div>
        <span className="font-mono text-xs tabular-nums">{score(attempt.reward)}</span>
        <span
          className={cn(
            "font-mono text-xs tabular-nums",
            advantage > 0.005
              ? "text-success"
              : advantage < -0.005
                ? "text-destructive"
                : "text-muted-foreground"
          )}
          title="Reward minus the group's mean: the direction GRPO moves this attempt"
        >
          {advantage > 0.005 ? "▲" : advantage < -0.005 ? "▼" : "•"}
          {Math.abs(advantage).toFixed(2)}
        </span>
        {best ? <Icon.trophy className="size-3.5 text-success" /> : null}
      </div>
      <pre className="max-h-32 overflow-auto whitespace-pre-wrap break-words rounded-sm bg-card/80 p-2 font-mono text-xs leading-snug">
        {attempt.text.trim() || "(empty)"}
      </pre>
      <div className="flex flex-wrap gap-1">
        {rewards.map((r) => (
          <CheckChip
            key={r.key}
            label={rewardLabel(r.key, rewards)}
            outcome={attempt.checks[r.key]}
          />
        ))}
      </div>
    </div>
  );
}

type SampleFilter = "fixed" | "broken" | "failing" | "passing" | "all";

function HeldOutComparison({
  samples,
  rewards,
}: {
  samples: HeldOutSample[];
  rewards: RewardConfig[];
}) {
  const paired = samples.filter((s) => s.baseline && s.final);
  const groups = useMemo(
    () => ({
      all: samples,
      broken: paired.filter((s) => s.baseline?.passed && !s.final?.passed),
      failing: paired.filter((s) => !s.baseline?.passed && !s.final?.passed),
      fixed: paired.filter((s) => !s.baseline?.passed && s.final?.passed),
      passing: paired.filter((s) => s.baseline?.passed && s.final?.passed),
    }),
    [samples, paired]
  );
  const [filter, setFilter] = useState<SampleFilter>("fixed");
  const effective: SampleFilter = paired.length ? filter : "all";
  const shown = groups[effective].slice(0, 30);

  return (
    <SectionCard
      description="The same prompts, scored by the same checks, before and after training."
      headerEnd={
        paired.length ? (
          <Tabs onValueChange={(v) => setFilter(v as SampleFilter)} value={effective}>
            <TabsList>
              <TabsTrigger value="fixed">Fixed {groups.fixed.length}</TabsTrigger>
              <TabsTrigger value="broken">Broken {groups.broken.length}</TabsTrigger>
              <TabsTrigger value="failing">Still failing {groups.failing.length}</TabsTrigger>
              <TabsTrigger value="passing">Still passing {groups.passing.length}</TabsTrigger>
            </TabsList>
          </Tabs>
        ) : null
      }
      icon={<Icon.history className="size-4" />}
      title="Held-out prompts"
    >
      {samples.length === 0 ? (
        <EmptyState
          description="The base model is scored on the held-out prompts before training starts."
          icon={Icon.history}
          size="compact"
          title="No held-out scores yet"
        />
      ) : shown.length === 0 ? (
        <p className="text-sm text-muted-foreground">No prompts in this group.</p>
      ) : (
        <div className="flex flex-col divide-y divide-border/70">
          {shown.map((sample) => (
            <div className="flex flex-col gap-2 py-3 first:pt-0" key={sample.index}>
              <p className="line-clamp-2 text-sm">
                <span className="font-mono text-xs text-muted-foreground">#{sample.index} · </span>
                {sample.prompt}
              </p>
              <div className="grid gap-2 md:grid-cols-2">
                <SampleSideView label="Base model" rewards={rewards} side={sample.baseline} />
                <SampleSideView label="Trained adapter" rewards={rewards} side={sample.final} />
              </div>
            </div>
          ))}
        </div>
      )}
    </SectionCard>
  );
}

function SampleSideView({
  label,
  side,
  rewards,
}: {
  label: string;
  side: HeldOutSample["baseline"];
  rewards: RewardConfig[];
}) {
  return (
    <div
      className={cn(
        "flex min-w-0 flex-col gap-1.5 rounded-md border p-2.5",
        !side
          ? "border-border/70 bg-wash-subtle"
          : side.passed
            ? "border-success/40 bg-success/10"
            : "border-destructive/40 bg-destructive/10"
      )}
    >
      <div className="flex items-center justify-between text-xs">
        <span className="text-muted-foreground">{label}</span>
        {side ? (
          <span className="font-mono tabular-nums">
            {side.passed ? "pass" : "fail"} · {score(side.reward)}
          </span>
        ) : (
          <span className="text-muted-foreground">pending</span>
        )}
      </div>
      {side ? (
        <>
          <pre className="max-h-28 overflow-auto whitespace-pre-wrap break-words font-mono text-xs leading-snug">
            {side.output.trim() || "(empty)"}
          </pre>
          <div className="flex flex-wrap gap-1">
            {rewards.map((r) => (
              <CheckChip
                key={r.key}
                label={rewardLabel(r.key, rewards)}
                outcome={side.checks[r.key]}
              />
            ))}
          </div>
        </>
      ) : null}
    </div>
  );
}

function Recipe({ run }: { run: RLRunDetail }) {
  const rewards = (run.rewards ?? []) as RewardConfig[];
  const hp = (run.hyperparameters ?? {}) as Record<string, number>;
  const rows: [string, string][] = [
    ["Base model", run.baseModel],
    [
      "Dataset",
      `${run.datasetName} · ${run.trainRows} training prompts · ${run.holdoutRows} held out`,
    ],
    [
      "Prompt column",
      run.toolsColumn ? `${run.promptColumn} · tools from ${run.toolsColumn}` : run.promptColumn,
    ],
    [
      "Group size",
      `${hp.group_size} attempts per prompt · ${hp.prompts_per_step} prompts per step`,
    ],
    ["Steps", `${hp.max_steps} · learning rate ${hp.learning_rate} · LoRA rank ${hp.lora_rank}`],
    [
      "Sampling",
      `temperature ${hp.temperature} · up to ${hp.max_completion_tokens} tokens · KL β ${hp.beta}`,
    ],
  ];
  if (run.adapterPath) rows.push(["Adapter", run.adapterPath]);
  return (
    <SectionCard icon={<Icon.settings className="size-4" />} title="Recipe">
      <div className="grid gap-4 lg:grid-cols-2">
        <dl className="grid grid-cols-[auto_minmax(0,1fr)] gap-x-4 gap-y-1.5 text-sm">
          {rows.map(([key, value]) => (
            <div className="contents" key={key}>
              <dt className="text-muted-foreground">{key}</dt>
              <dd className="min-w-0 break-words font-mono text-xs leading-5">{value}</dd>
            </div>
          ))}
        </dl>
        <div className="flex flex-col gap-2">
          <span className="text-xs text-muted-foreground">Reward checks</span>
          {rewards.map((r) => (
            <div
              className="flex flex-col gap-1 rounded-md border border-border/70 bg-wash-subtle px-3 py-2"
              key={r.key}
            >
              <div className="flex items-center justify-between text-sm font-medium">
                {rewardLabel(r.key, rewards)}
                <span className="font-mono text-xs text-muted-foreground">weight {r.weight}</span>
              </div>
              <div className="flex flex-wrap gap-1">
                {Object.entries(r.bind).map(([param, column]) => (
                  <Badge key={param} size="chip" variant="info">
                    {param} ← {column}
                  </Badge>
                ))}
                {Object.entries(r.attributes).map(([attr, column]) => (
                  <Badge key={attr} size="chip" variant="info">
                    {attr} ← {column}
                  </Badge>
                ))}
                {Object.entries(r.params).map(([param, value]) => (
                  <Badge key={param} size="chip" variant="neutral">
                    {param} = {JSON.stringify(value)}
                  </Badge>
                ))}
              </div>
            </div>
          ))}
        </div>
      </div>
    </SectionCard>
  );
}
