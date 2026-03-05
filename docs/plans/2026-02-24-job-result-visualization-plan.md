# Job Result Visualization Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Replace the raw JSON dump on the job detail page with structured visual result panels for
each job type (`prompt_tuning`, `model_backtesting`, `agent_discovery`).

**Architecture:** Co-located renderer components in `frontend/src/components/jobs/`. The route file
`jobs.$jobId.tsx` stays thin — it replaces the `<pre>` block with `<JobResult job={job} />`.
Each renderer receives `job.result` (typed as `Record<string, unknown>`) and casts it locally.

**Tech Stack:** React 18, TypeScript (strict), Tailwind CSS, shadcn/ui (`Accordion`, `Alert`,
`Badge`, `Button`, `Card`), TanStack Router `Link`.

______________________________________________________________________

## Context You Must Read First

- Design doc: `docs/plans/2026-02-24-job-result-visualization-design.md`
- Route file to modify: `frontend/src/routes/_auth/jobs.$jobId.tsx`
- shadcn Accordion: `frontend/src/components/ui/accordion.tsx`
- shadcn Alert: `frontend/src/components/ui/alert.tsx`
- shadcn Badge: `frontend/src/components/ui/badge.tsx`

### Key routing facts

- Agent detail page route: `/_auth/agents/$slug` with search param `tab` (values:
  `"suggestions" | "jobs" | "versions"`).
- To link to an agent's suggestions tab: `<Link to="/agents/$slug" params={{ slug }} search={{ tab: "suggestions" }}>`.
- `job.promptSlug` is the slug needed for that link.

### Key data shapes

**`prompt_tuning` result** (happy path — `status: "improved"`):

```ts
{
  status: "improved",
  scored_count: 100,
  spans_analyzed: 150,
  suggestions_count: 3,
  suggestion_id: "uuid",
  new_version: 3,
  comparison_test: {
    spans_tested: 50,
    spans_created: 45,
    metrics: {
      old_prompt: { avg_score: 0.70, span_count: 50, total_cost: 0.01, avg_latency_ms: 200 },
      new_prompt: { avg_score: 0.85, span_count: 45, total_cost: 0.012, avg_latency_ms: 210 },
      improvement: {
        score_delta: 0.15, score_delta_pct: 21.4,
        cost_delta: 0.002, cost_delta_pct: 20.0,
        latency_delta_ms: 10, latency_delta_pct: 5.0,
      },
    },
  },
}
```

For `status: "no_improvement"` the same shape minus `suggestion_id`/`new_version`.
For cancelled: `{ reason: "string", scored_count?: number }`.
For error: `{ error: "string" }` or `{ reason: "string" }`.

**`model_backtesting` result**:

```ts
{
  current_model: "gpt-4o-mini",
  models_tested: 3,
  spans_tested: 20,
  suggestion_id?: "uuid",
  recommendations: {
    summary: "Consider switching...",
    verdict?: "switch" | "stay",
    baseline: { model: "gpt-4o-mini", avg_eval_score: 0.80, avg_latency_ms: 220, avg_cost_per_request: 0.000012, scored_span_count: 20 },
    top_performer?: { model: "claude-3-5-sonnet", avg_eval_score: 0.91, performance_delta_pct: 13.75, avg_latency_ms: 310, avg_cost_per_request: 0.000050, reason: "..." },
    fastest?: { model: "gpt-4o-mini", avg_latency_ms: 180, performance_delta_pp: -0.02, avg_eval_score: 0.78, reason: "..." },
    cheapest?: { model: "gemini-flash", avg_cost_per_request: 0.000004, performance_delta_pp: -0.05, avg_eval_score: 0.75, reason: "..." },
    best_overall?: { model: "...", avg_eval_score: 0.88, avg_latency_ms: 250, avg_cost_per_request: 0.000020, reason: "..." },
  },
}
```

**`agent_discovery` result** — two possible shapes:

```ts
// Shape A (new templates found): stats directly on result
{ mapped: 15, new_templates: 2, unmapped: 3 }

// Shape B (no new templates): wrapped
{ reason: "No new templates created", stats: { mapped: 15, new_templates: 0, unmapped: 3 } }
```

______________________________________________________________________

## Task 1: RawResultAccordion component

**Files:**

- Create: `frontend/src/components/jobs/RawResultAccordion.tsx`

**Step 1: Create the file**

```tsx
import {
  Accordion,
  AccordionContent,
  AccordionItem,
  AccordionTrigger,
} from "@/components/ui/accordion";

interface RawResultAccordionProps {
  result: Record<string, unknown>;
}

export function RawResultAccordion({ result }: RawResultAccordionProps) {
  return (
    <Accordion collapsible type="single">
      <AccordionItem className="border-0" value="raw">
        <AccordionTrigger className="py-2 text-xs text-muted-foreground hover:no-underline">
          Raw Result
        </AccordionTrigger>
        <AccordionContent>
          <pre className="max-h-60 overflow-auto rounded-md border border-border bg-muted/30 p-3 text-xs font-mono whitespace-pre-wrap break-all">
            {JSON.stringify(result, null, 2)}
          </pre>
        </AccordionContent>
      </AccordionItem>
    </Accordion>
  );
}
```

**Step 2: Verify no linter errors**

Run: `cd frontend && npx tsc --noEmit 2>&1 | grep RawResultAccordion`
Expected: no output (no errors).

**Step 3: Commit**

```bash
git add frontend/src/components/jobs/RawResultAccordion.tsx
git commit -m "feat: add RawResultAccordion component for job detail page"
```

______________________________________________________________________

## Task 2: AgentDiscoveryResult component

**Files:**

- Create: `frontend/src/components/jobs/AgentDiscoveryResult.tsx`

**Step 1: Create the file**

```tsx
import { RawResultAccordion } from "./RawResultAccordion";

interface AgentDiscoveryStats {
  mapped: number;
  new_templates: number;
  unmapped: number;
}

function normalise(result: Record<string, unknown>): AgentDiscoveryStats {
  // Shape B: { reason, stats: {...} }
  if (typeof result.stats === "object" && result.stats !== null) {
    return result.stats as AgentDiscoveryStats;
  }
  // Shape A: stats directly on result
  return result as unknown as AgentDiscoveryStats;
}

interface StatChipProps {
  label: string;
  value: number;
  highlight?: boolean;
}

function StatChip({ label, value, highlight }: StatChipProps) {
  return (
    <div className="flex flex-col items-center gap-1 rounded-lg border border-border bg-muted/30 px-6 py-4">
      <span
        className={`text-2xl font-bold tabular-nums ${highlight ? "text-amber-600" : "text-foreground"}`}
      >
        {value}
      </span>
      <span className="text-xs text-muted-foreground">{label}</span>
    </div>
  );
}

interface AgentDiscoveryResultProps {
  result: Record<string, unknown>;
}

export function AgentDiscoveryResult({ result }: AgentDiscoveryResultProps) {
  const stats = normalise(result);
  const reason = typeof result.reason === "string" ? result.reason : undefined;

  return (
    <div className="space-y-4">
      {reason && (
        <p className="text-sm text-muted-foreground">{reason}</p>
      )}
      <div className="flex flex-wrap gap-3">
        <StatChip highlight label="New Templates" value={stats.new_templates} />
        <StatChip label="Spans Mapped" value={stats.mapped} />
        <StatChip label="Still Unmapped" value={stats.unmapped} />
      </div>
      <RawResultAccordion result={result} />
    </div>
  );
}
```

**Step 2: Verify no linter errors**

Run: `cd frontend && npx tsc --noEmit 2>&1 | grep AgentDiscovery`
Expected: no output.

**Step 3: Commit**

```bash
git add frontend/src/components/jobs/AgentDiscoveryResult.tsx
git commit -m "feat: add AgentDiscoveryResult component"
```

______________________________________________________________________

## Task 3: PromptTuningResult component

**Files:**

- Create: `frontend/src/components/jobs/PromptTuningResult.tsx`

**Step 1: Create the file**

```tsx
import { ArrowRight } from "lucide-react";
import { Link } from "@tanstack/react-router";

import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { RawResultAccordion } from "./RawResultAccordion";

interface ComparisonMetrics {
  old_prompt: { avg_score: number; span_count: number; total_cost: number; avg_latency_ms: number };
  new_prompt: { avg_score: number; span_count: number; total_cost: number; avg_latency_ms: number };
  improvement: {
    score_delta: number;
    score_delta_pct: number;
    cost_delta: number;
    cost_delta_pct: number;
    latency_delta_ms: number;
    latency_delta_pct: number;
  };
}

interface PromptTuningData {
  status?: string;
  reason?: string;
  error?: string;
  suggestion_id?: string;
  new_version?: number;
  scored_count?: number;
  comparison_test?: {
    spans_tested?: number;
    metrics?: ComparisonMetrics;
  };
}

function formatScore(n: number) {
  return `${(n * 100).toFixed(1)}%`;
}

function formatDelta(n: number, unit = "", plus = true) {
  const sign = n >= 0 && plus ? "+" : "";
  return `${sign}${n.toFixed(1)}${unit}`;
}

function DeltaBadge({ pct, label }: { pct: number; label?: string }) {
  const color =
    pct > 0 ? "text-green-600 bg-green-500/10" : pct < 0 ? "text-red-600 bg-red-500/10" : "text-amber-600 bg-amber-500/10";
  return (
    <span className={`rounded px-2 py-0.5 text-xs font-semibold ${color}`}>
      {label ?? formatDelta(pct, "%")}
    </span>
  );
}

interface MetricChipProps {
  label: string;
  value: string;
  delta?: string;
  deltaColor?: "green" | "red" | "amber" | "neutral";
}

function MetricChip({ label, value, delta, deltaColor = "neutral" }: MetricChipProps) {
  const deltaClass = {
    green: "text-green-600",
    red: "text-red-600",
    amber: "text-amber-600",
    neutral: "text-muted-foreground",
  }[deltaColor];

  return (
    <div className="flex flex-col gap-1 rounded-lg border border-border bg-muted/30 px-4 py-3 text-center">
      <span className="text-sm font-medium tabular-nums">{value}</span>
      {delta && <span className={`text-xs font-medium ${deltaClass}`}>{delta}</span>}
      <span className="text-xs text-muted-foreground">{label}</span>
    </div>
  );
}

interface PromptTuningResultProps {
  result: Record<string, unknown>;
  promptSlug?: string | null;
}

export function PromptTuningResult({ result, promptSlug }: PromptTuningResultProps) {
  const data = result as PromptTuningData;

  // Cancelled / error states
  const errorMsg = data.reason ?? data.error;
  if (
    errorMsg &&
    data.status !== "improved" &&
    data.status !== "no_improvement"
  ) {
    return (
      <div className="space-y-4">
        <Alert variant="warning">{errorMsg}</Alert>
        <RawResultAccordion result={result} />
      </div>
    );
  }

  const metrics = data.comparison_test?.metrics;
  const imp = metrics?.improvement;
  const isImproved = data.status === "improved";
  const spansCount = data.comparison_test?.spans_tested ?? 0;

  return (
    <div className="space-y-5">
      {/* View Suggestion button */}
      {isImproved && data.suggestion_id && promptSlug && (
        <div className="flex justify-end">
          <Button asChild size="sm" variant="outline">
            <Link
              params={{ slug: promptSlug }}
              search={{ tab: "suggestions" }}
              to="/agents/$slug"
            >
              View Suggestion
              <ArrowRight className="ml-1.5 size-3.5" />
            </Link>
          </Button>
        </div>
      )}

      {/* Progress arrow visualization */}
      {metrics ? (
        <div className="flex items-center gap-4 rounded-lg border border-border bg-muted/20 px-6 py-5">
          {/* Old score */}
          <div className="text-center">
            <div className="text-2xl font-bold tabular-nums text-muted-foreground">
              {formatScore(metrics.old_prompt.avg_score)}
            </div>
            <div className="mt-0.5 text-xs text-muted-foreground">Current Score</div>
          </div>

          {/* Arrow + delta */}
          <div className="flex flex-1 flex-col items-center gap-1">
            <div className="flex w-full items-center gap-1">
              <div className="h-0.5 flex-1 bg-border" />
              <ArrowRight className="size-4 shrink-0 text-muted-foreground" />
            </div>
            {imp && (
              <DeltaBadge
                label={isImproved ? formatDelta(imp.score_delta_pct, "%") : "No improvement"}
                pct={imp.score_delta_pct}
              />
            )}
          </div>

          {/* New score */}
          <div className="text-center">
            <div
              className={`text-2xl font-bold tabular-nums ${isImproved ? "text-green-600" : "text-muted-foreground"}`}
            >
              {formatScore(metrics.new_prompt.avg_score)}
            </div>
            <div className="mt-0.5 text-xs text-muted-foreground">
              {isImproved ? `New Score (v${data.new_version})` : "Test Score"}
            </div>
          </div>
        </div>
      ) : null}

      {/* Secondary metric chips */}
      {imp && (
        <div className="flex flex-wrap gap-3">
          <MetricChip
            delta={undefined}
            deltaColor="neutral"
            label="Spans Tested"
            value={String(spansCount)}
          />
          <MetricChip
            delta={formatDelta(imp.cost_delta_pct, "%")}
            deltaColor={imp.cost_delta > 0 ? "red" : "green"}
            label="Cost Delta"
            value={imp.cost_delta >= 0 ? `+$${imp.cost_delta.toFixed(5)}` : `-$${Math.abs(imp.cost_delta).toFixed(5)}`}
          />
          <MetricChip
            delta={formatDelta(imp.latency_delta_pct, "%")}
            deltaColor={imp.latency_delta_ms > 0 ? "amber" : "green"}
            label="Latency Delta"
            value={`${imp.latency_delta_ms >= 0 ? "+" : ""}${imp.latency_delta_ms.toFixed(0)} ms`}
          />
        </div>
      )}

      <RawResultAccordion result={result} />
    </div>
  );
}
```

**Step 2: Verify no linter errors**

Run: `cd frontend && npx tsc --noEmit 2>&1 | grep PromptTuning`
Expected: no output.

**Step 3: Commit**

```bash
git add frontend/src/components/jobs/PromptTuningResult.tsx
git commit -m "feat: add PromptTuningResult component with progress-arrow visualization"
```

______________________________________________________________________

## Task 4: BacktestingResult component

**Files:**

- Create: `frontend/src/components/jobs/BacktestingResult.tsx`

**Step 1: Create the file**

```tsx
import { ArrowRight, Trophy, Zap, DollarSign } from "lucide-react";
import { Link } from "@tanstack/react-router";

import { Button } from "@/components/ui/button";
import { RawResultAccordion } from "./RawResultAccordion";

interface ModelRec {
  model: string;
  avg_eval_score?: number;
  performance_delta_pct?: number;
  performance_delta_pp?: number;
  avg_latency_ms?: number;
  avg_cost_per_request?: number;
  reason?: string;
}

interface BaselineRec extends ModelRec {
  scored_span_count?: number;
}

interface Recommendations {
  summary?: string;
  verdict?: string;
  baseline?: BaselineRec;
  top_performer?: ModelRec;
  fastest?: ModelRec;
  cheapest?: ModelRec;
  best_overall?: ModelRec;
}

interface BacktestingData {
  current_model?: string;
  models_tested?: number;
  spans_tested?: number;
  suggestion_id?: string;
  recommendations?: Recommendations;
}

function fmt(n: number | undefined, digits = 2) {
  if (n === undefined || n === null) return "—";
  return n.toFixed(digits);
}

function fmtPct(n: number | undefined) {
  if (n === undefined || n === null) return null;
  const sign = n >= 0 ? "+" : "";
  return `${sign}${n.toFixed(1)}%`;
}

function truncateModel(model: string) {
  return model.length > 20 ? `${model.slice(0, 18)}…` : model;
}

interface RecommendationCardProps {
  icon: React.ReactNode;
  title: string;
  rec: ModelRec;
  highlight?: string | null;
}

function RecommendationCard({ icon, title, rec, highlight }: RecommendationCardProps) {
  return (
    <div className="flex flex-col gap-2 rounded-lg border border-border bg-muted/20 p-4">
      <div className="flex items-center gap-1.5 text-xs font-semibold text-muted-foreground">
        {icon}
        {title}
      </div>
      <div className="text-sm font-semibold" title={rec.model}>
        {truncateModel(rec.model)}
      </div>
      <div className="space-y-1 text-xs text-muted-foreground">
        {rec.avg_eval_score !== undefined && (
          <div>Score: {fmt(rec.avg_eval_score, 3)}</div>
        )}
        {rec.avg_latency_ms !== undefined && (
          <div>Latency: {fmt(rec.avg_latency_ms, 0)} ms</div>
        )}
        {rec.avg_cost_per_request !== undefined && (
          <div>Cost/req: ${rec.avg_cost_per_request.toFixed(6)}</div>
        )}
      </div>
      {highlight && (
        <span className="mt-1 self-start rounded bg-amber-500/10 px-2 py-0.5 text-xs font-semibold text-amber-600">
          {highlight}
        </span>
      )}
    </div>
  );
}

interface BacktestingResultProps {
  result: Record<string, unknown>;
  promptSlug?: string | null;
}

export function BacktestingResult({ result, promptSlug }: BacktestingResultProps) {
  const data = result as BacktestingData;
  const recs = data.recommendations;
  const baseline = recs?.baseline;

  return (
    <div className="space-y-5">
      {/* View Suggestion button */}
      {data.suggestion_id && promptSlug && (
        <div className="flex justify-end">
          <Button asChild size="sm" variant="outline">
            <Link
              params={{ slug: promptSlug }}
              search={{ tab: "suggestions" }}
              to="/agents/$slug"
            >
              View Suggestion
              <ArrowRight className="ml-1.5 size-3.5" />
            </Link>
          </Button>
        </div>
      )}

      {/* Baseline summary row */}
      {baseline && (
        <div className="rounded-lg border border-border bg-muted/20 px-4 py-3 text-sm">
          <span className="font-medium">Baseline:</span>{" "}
          <span className="font-mono">{baseline.model}</span>
          {" · "}Score {fmt(baseline.avg_eval_score, 3)}
          {baseline.avg_latency_ms !== undefined && (
            <> · {fmt(baseline.avg_latency_ms, 0)} ms</>
          )}
          {baseline.avg_cost_per_request !== undefined && (
            <> · ${baseline.avg_cost_per_request.toFixed(6)}/req</>
          )}
          {data.spans_tested !== undefined && (
            <span className="ml-2 text-xs text-muted-foreground">
              · {data.spans_tested} spans tested
            </span>
          )}
        </div>
      )}

      {/* Recommendation cards */}
      {recs && (
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {recs.top_performer && (
            <RecommendationCard
              highlight={recs.top_performer.performance_delta_pct != null ? fmtPct(recs.top_performer.performance_delta_pct) + " vs baseline" : null}
              icon={<Trophy className="size-3.5 text-amber-500" />}
              rec={recs.top_performer}
              title="Top Performer"
            />
          )}
          {recs.fastest && (
            <RecommendationCard
              highlight={recs.fastest.performance_delta_pp != null ? `${fmtPct(-recs.fastest.performance_delta_pp)} score` : null}
              icon={<Zap className="size-3.5 text-blue-500" />}
              rec={recs.fastest}
              title="Fastest"
            />
          )}
          {recs.cheapest && (
            <RecommendationCard
              highlight={recs.cheapest.performance_delta_pp != null ? `${fmtPct(-recs.cheapest.performance_delta_pp)} score` : null}
              icon={<DollarSign className="size-3.5 text-green-600" />}
              rec={recs.cheapest}
              title="Cheapest"
            />
          )}
          {recs.best_overall && !recs.top_performer && (
            <RecommendationCard
              highlight="Best Overall"
              icon={<Trophy className="size-3.5 text-amber-500" />}
              rec={recs.best_overall}
              title="Best Overall"
            />
          )}
        </div>
      )}

      {/* Summary text */}
      {recs?.summary && (
        <p className="text-sm text-muted-foreground">{recs.summary}</p>
      )}

      <RawResultAccordion result={result} />
    </div>
  );
}
```

**Step 2: Verify no linter errors**

Run: `cd frontend && npx tsc --noEmit 2>&1 | grep BacktestingResult`
Expected: no output.

**Step 3: Commit**

```bash
git add frontend/src/components/jobs/BacktestingResult.tsx
git commit -m "feat: add BacktestingResult component with recommendation cards"
```

______________________________________________________________________

## Task 5: JobResult entry point + barrel export

**Files:**

- Create: `frontend/src/components/jobs/JobResult.tsx`
- Create: `frontend/src/components/jobs/index.ts`

**Step 1: Create `JobResult.tsx`**

```tsx
import type { JobOut } from "@/api";
import { AgentDiscoveryResult } from "./AgentDiscoveryResult";
import { BacktestingResult } from "./BacktestingResult";
import { PromptTuningResult } from "./PromptTuningResult";
import { RawResultAccordion } from "./RawResultAccordion";

interface JobResultProps {
  job: JobOut;
}

export function JobResult({ job }: JobResultProps) {
  const result = job.result as Record<string, unknown> | null;
  if (!result || Object.keys(result).length === 0) return null;

  switch (job.jobType) {
    case "prompt_tuning":
      return <PromptTuningResult promptSlug={job.promptSlug} result={result} />;
    case "model_backtesting":
      return <BacktestingResult promptSlug={job.promptSlug} result={result} />;
    case "agent_discovery":
      return <AgentDiscoveryResult result={result} />;
    default:
      return <RawResultAccordion result={result} />;
  }
}
```

**Step 2: Create `index.ts`**

```ts
export { JobResult } from "./JobResult";
```

**Step 3: Verify no linter errors**

Run: `cd frontend && npx tsc --noEmit 2>&1 | grep "jobs/"`
Expected: no output.

**Step 4: Commit**

```bash
git add frontend/src/components/jobs/JobResult.tsx frontend/src/components/jobs/index.ts
git commit -m "feat: add JobResult switcher and barrel export"
```

______________________________________________________________________

## Task 6: Wire into jobs.$jobId.tsx

**Files:**

- Modify: `frontend/src/routes/_auth/jobs.$jobId.tsx` (lines 186–197)

**Step 1: Add the import near the top of the file** (after existing imports)

```tsx
import { JobResult } from "@/components/jobs";
```

**Step 2: Replace the result `<Card>` block**

Find and replace this block (lines 186–197):

```tsx
{job.result && Object.keys(job.result).length > 0 && (
  <Card>
    <CardHeader>
      <h2 className="text-base font-semibold">Result</h2>
    </CardHeader>
    <CardContent>
      <pre className="max-h-80 overflow-auto rounded-md border border-border bg-muted/30 p-4 text-xs font-mono whitespace-pre-wrap wrap-break-word">
        {JSON.stringify(job.result, null, 2)}
      </pre>
    </CardContent>
  </Card>
)}
```

Replace with:

```tsx
{job.result && Object.keys(job.result).length > 0 && (
  <Card>
    <CardHeader>
      <h2 className="text-base font-semibold">Result</h2>
    </CardHeader>
    <CardContent>
      <JobResult job={job} />
    </CardContent>
  </Card>
)}
```

**Step 3: Remove now-unused imports** (if nothing else in the file uses them)

Remove `pre` is not an import, but check if any shadcn imports are now unused. The `Card`,
`CardContent`, `CardHeader` imports are still needed. No imports need removing.

**Step 4: Verify no linter errors**

Run: `cd frontend && npx tsc --noEmit`
Expected: exit 0, no output.

**Step 5: Verify the app builds**

Run: `cd frontend && npm run build 2>&1 | tail -20`
Expected: build succeeds with no errors.

**Step 6: Commit**

```bash
git add frontend/src/routes/_auth/jobs.\$jobId.tsx
git commit -m "feat: render structured job results instead of raw JSON on job detail page"
```

______________________________________________________________________

## Verification Checklist

Before declaring done, manually verify in the browser (or read snapshot tests) that:

- [ ] A `prompt_tuning` completed job shows the progress arrow with scores and delta badge.
- [ ] A `prompt_tuning` improved job shows "View Suggestion" button linking to `/agents/<slug>?tab=suggestions`.
- [ ] A `prompt_tuning` cancelled job shows an amber Alert with the reason string.
- [ ] A `model_backtesting` job shows baseline row + recommendation cards.
- [ ] A `model_backtesting` job with `suggestion_id` shows "View Suggestion" button.
- [ ] An `agent_discovery` job shows the three stat chips.
- [ ] Unknown job type shows only the Raw Result accordion (collapsed by default).
- [ ] Clicking "Raw Result" accordion expands the JSON.
- [ ] Running jobs (no result yet) show nothing in the Result card.
