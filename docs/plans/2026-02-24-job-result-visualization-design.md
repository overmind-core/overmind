# Job Result Visualization — Design

**Date:** 2026-02-24
**Status:** Approved
**Scope:** `frontend/src/routes/_auth/jobs.$jobId.tsx` and new `frontend/src/components/jobs/`

______________________________________________________________________

## Problem

The job detail page currently dumps `job.result` as raw JSON inside a `<pre>` block. For
result-producing job types (`prompt_tuning`, `model_backtesting`, `agent_discovery`) this is
unreadable. Users cannot easily see what happened, compare options, or act on recommendations.

______________________________________________________________________

## Goals

- Replace the raw JSON block with structured, readable result panels per job type.
- For `prompt_tuning`: show a progress-arrow before/after score visualization with metric deltas.
- For `model_backtesting`: show recommendation cards (top performer, fastest, cheapest) with a
  baseline summary row.
- For `agent_discovery`: show stat chips for new templates, mapped spans, and unmapped spans.
- When a job produced a Suggestion, show a prominent "View Suggestion" link.
- Keep a collapsed "Raw Result" accordion for debugging / unknown result shapes.

______________________________________________________________________

## Architecture

### Approach

**Co-located renderer components (Option B).**
`jobs.$jobId.tsx` stays thin — it imports `<JobResult job={job} />` which switches on
`job.jobType` and delegates to the appropriate renderer. No charting library required; all
visualizations use CSS/Tailwind.

### File Structure

```
frontend/src/components/jobs/
├── index.ts                      # barrel export
├── JobResult.tsx                 # entry point — switches on jobType
├── PromptTuningResult.tsx        # renderer for prompt_tuning
├── BacktestingResult.tsx         # renderer for model_backtesting
├── AgentDiscoveryResult.tsx      # renderer for agent_discovery
└── RawResultAccordion.tsx        # collapsed JSON fallback (shared)
```

`jobs.$jobId.tsx` — replace the `<pre>` result block with:

```tsx
<JobResult job={job} />
```

______________________________________________________________________

## Component Designs

### 1. `JobResult.tsx`

Switches on `job.jobType`:

| `jobType`           | Renderer                    |
| ------------------- | --------------------------- |
| `prompt_tuning`     | `<PromptTuningResult>`      |
| `model_backtesting` | `<BacktestingResult>`       |
| `agent_discovery`   | `<AgentDiscoveryResult>`    |
| anything else       | `<RawResultAccordion>` only |

All renderers also render `<RawResultAccordion>` at the bottom.

______________________________________________________________________

### 2. `PromptTuningResult.tsx`

**Data shape (from `job.result`):**

```ts
interface PromptTuningResultData {
  status: "improved" | "no_improvement" | "cancelled";
  reason?: string;                     // when cancelled/failed
  scored_count?: number;
  spans_analyzed?: number;
  suggestions_count?: number;
  suggestion_id?: string;
  new_version?: number;
  comparison_test?: {
    spans_tested: number;
    spans_created?: number;
    metrics: {
      old_prompt: { avg_score: number; span_count: number; total_cost: number; avg_latency_ms: number };
      new_prompt: { avg_score: number; span_count: number; total_cost: number; avg_latency_ms: number };
      improvement: {
        score_delta: number; score_delta_pct: number;
        cost_delta: number; cost_delta_pct: number;
        latency_delta_ms: number; latency_delta_pct: number;
      };
    };
  };
}
```

**Layout (improved):**

```
┌─────────────────────────────────────────────────────────┐
│  Prompt Tuning Result              [View Suggestion →]  │
│                                                         │
│  ● 70.0%  ──────────────────────→  ● 85.0%             │
│  Current Score                New Score  (+21.4%)       │
│                                                         │
│  ┌───────────────┐  ┌──────────────┐  ┌─────────────┐  │
│  │ Spans Tested  │  │  Cost Delta  │  │   Latency   │  │
│  │      50       │  │   +$0.002    │  │   +10 ms    │  │
│  └───────────────┘  └──────────────┘  └─────────────┘  │
│                                                         │
│  [▸ Raw Result]                                         │
└─────────────────────────────────────────────────────────┘
```

- Progress arrow: two colored circles connected by a line, with score percentage labels.
- Delta badge on the new score: green if `score_delta > 0`, amber if `= 0`, red if `< 0`.
- Secondary metric chips: spans tested, cost delta, latency delta.

**Layout (no_improvement):** Same arrow layout, amber delta badge reading "No improvement".

**Layout (cancelled / reason present):** Single `Alert variant="warning"` with the `reason` string.

**Layout (error):** Single `Alert variant="destructive"` with `result.reason` or `result.error`.

______________________________________________________________________

### 3. `BacktestingResult.tsx`

**Data shape (from `job.result`):**

```ts
interface BacktestingResultData {
  current_model?: string;
  models_tested?: number;
  spans_tested?: number;
  suggestion_id?: string;
  recommendations?: {
    summary?: string;
    baseline?: { model: string; avg_eval_score: number; avg_latency_ms: number; avg_cost_per_request: number; scored_span_count: number };
    top_performer?: { model: string; avg_eval_score: number; performance_delta_pct: number; avg_latency_ms: number; avg_cost_per_request: number; reason: string };
    fastest?: { model: string; avg_latency_ms: number; performance_delta_pp: number; avg_eval_score: number; reason: string };
    cheapest?: { model: string; avg_cost_per_request: number; performance_delta_pp: number; avg_eval_score: number; reason: string };
    best_overall?: { model: string; avg_eval_score: number; avg_latency_ms: number; avg_cost_per_request: number; reason: string };
    verdict?: string;
  };
}
```

**Layout:**

```
┌──────────────────────────────────────────────────────────────┐
│  Backtesting Result                   [View Suggestion →]    │
│                                                              │
│  Baseline: gpt-4o-mini · Score 0.80 · 220ms · $0.000012     │
│  Tested 3 models across 20 spans                            │
│                                                              │
│  ┌──────────────────┐  ┌────────────────┐  ┌─────────────┐  │
│  │ 🏆 Top Performer │  │ ⚡ Fastest     │  │ 💰 Cheapest │  │
│  │  claude-3-5-...  │  │  gpt-4o-mini  │  │  gemini...  │  │
│  │  Score: 0.91     │  │  180ms (-18%) │  │  $0.000004  │  │
│  │  +13.8% vs base  │  │  ~same score  │  │  -67% cost  │  │
│  └──────────────────┘  └────────────────┘  └─────────────┘  │
│                                                              │
│  Summary: "Consider switching from gpt-4o-mini to..."       │
│                                                              │
│  [▸ Raw Result]                                              │
└──────────────────────────────────────────────────────────────┘
```

- Only render cards that exist in the response (if no `top_performer` key, no card shown).
- Baseline summary row is always shown if `baseline` is present.
- `summary` string rendered as a muted paragraph below cards.

______________________________________________________________________

### 4. `AgentDiscoveryResult.tsx`

**Data shape (from `job.result`):**

```ts
// Direct stats object (new templates found):
interface AgentDiscoveryStats {
  mapped: number;
  new_templates: number;
  unmapped: number;
}

// Wrapped (no new templates):
interface AgentDiscoveryNoTemplates {
  reason: string;
  stats: AgentDiscoveryStats;
}
```

Normalise both shapes into stats + optional reason before rendering.

**Layout:**

```
┌───────────────────────────────────────────────┐
│  Agent Discovery Result                       │
│                                               │
│  ┌───────────────┐  ┌──────────┐  ┌────────┐  │
│  │ New Templates │  │  Mapped  │  │Unmapped│  │
│  │      2        │  │   15     │  │   3    │  │
│  └───────────────┘  └──────────┘  └────────┘  │
│                                               │
│  [▸ Raw Result]                               │
└───────────────────────────────────────────────┘
```

______________________________________________________________________

### 5. `RawResultAccordion.tsx`

A shadcn `Collapsible` (or a simple `<details>` styled with Tailwind) that hides/shows the
`<pre>` JSON block. Rendered at the bottom of every result panel.

______________________________________________________________________

## Type Safety

Each renderer receives `result: Record<string, unknown>` (the raw `job.result`) and casts it to a
local interface using a type guard or `as`. No changes to the generated OpenAPI models.

______________________________________________________________________

## Navigation to Suggestion

When `result.suggestion_id` is present, render:

```tsx
<Button asChild size="sm" variant="outline">
  <Link to="/agents/$slug/suggestions/$id" params={{ ... }}>
    View Suggestion →
  </Link>
</Button>
```

The suggestion route needs the `promptSlug` from `job.promptSlug` and the `suggestion_id` from
`result.suggestion_id`.

______________________________________________________________________

## Implementation Plan (high level)

1. Create `RawResultAccordion.tsx`
1. Create `AgentDiscoveryResult.tsx`
1. Create `PromptTuningResult.tsx`
1. Create `BacktestingResult.tsx`
1. Create `JobResult.tsx` (entry point switcher)
1. Add barrel `index.ts`
1. Update `jobs.$jobId.tsx` to use `<JobResult>`

______________________________________________________________________

## Out of Scope

- Changes to the jobs list page (`jobs.tsx`)
- Changes to backend API or result schemas
- Per-span detail tables inside the result panel
