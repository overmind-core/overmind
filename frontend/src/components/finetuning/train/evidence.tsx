import { useState } from "react";

import {
  confidenceLabel,
  formatScore,
  matchStanding,
  ordinal,
} from "@/components/finetuning/train/model-config";
import { Badge } from "@/components/ui/badge";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
import { Icon } from "@/components/ui/icons";
import { count } from "@/lib/formatters";
import { PROSE } from "@/lib/typography";
import { cn, plural } from "@/lib/utils";
import type {
  ConfidenceEnum,
  FinetuningEvidence,
  FinetuningExcludedModel,
  FinetuningSkillScore,
} from "@/openapi";

export function MatchScore({
  match,
  matchRank,
  matchPool,
  className,
}: {
  match: number | null;
  matchRank: number | null;
  matchPool: number;
  className?: string;
}) {
  const standing = matchStanding(matchRank, matchPool);
  const label =
    match == null
      ? "Not graded"
      : [`Match ${formatScore(match)} out of 100`, standing].filter(Boolean).join(", ");
  return (
    <Badge
      aria-label={label}
      className={cn("shrink-0 tabular-nums", className)}
      size="chip"
      title={label}
      variant={match == null ? "neutral" : "outline"}
    >
      {match == null ? "Not graded" : `Match ${formatScore(match)}/100`}
    </Badge>
  );
}

/** Self-reported rows never interleave with measured ones, however high they score. */
export function sortEvidence(rows: FinetuningEvidence[]): FinetuningEvidence[] {
  return [...rows].sort(
    (a, b) =>
      Number(a.provenance === "lab_claimed") - Number(b.provenance === "lab_claimed") ||
      b.percentile - a.percentile ||
      a.benchmark.localeCompare(b.benchmark)
  );
}

export interface SkillBar {
  skill: string;
  weight: number;
  /** Standing among the candidates for this task, or null when no benchmark covers
   *  the skill. Never the global percentile — that population lives in the table. */
  percentile: number | null;
  rank: number | null;
  fieldN: number;
}

/** One bar per skill the task weights, heaviest first, so the skill that decides the
 *  pick reads first. A skill the task ignores is left out however well the model
 *  scores on it. */
export function skillBars(
  skillScores: FinetuningSkillScore[],
  weights: Record<string, number>
): SkillBar[] {
  const bySkill = new Map(skillScores.map((score) => [score.skill, score]));
  return Object.entries(weights)
    .filter(([, weight]) => weight > 0)
    .map(([skill, weight]) => {
      const score = bySkill.get(skill);
      return {
        fieldN: score?.fieldN ?? 0,
        percentile: score?.percentileInField ?? null,
        rank: score?.rankInField ?? null,
        skill,
        weight,
      };
    })
    .sort((a, b) => b.weight - a.weight || a.skill.localeCompare(b.skill));
}

/** The candidate field every bar was read against, or null when the skills were scored
 *  against different-sized pools and no single number describes the chart. */
export function fieldSize(bars: SkillBar[]): number | null {
  const sizes = new Set(bars.filter((bar) => bar.rank != null).map((bar) => bar.fieldN));
  return sizes.size === 1 ? [...sizes][0] : null;
}

const clampPercent = (value: number): number => Math.max(0, Math.min(100, value));

// Half the field sits below the median candidate, so it lands on the 50th percentile of
// the same field the bars are read against.
const MEDIAN_PERCENTILE = 50;

const COLUMNS = "grid-cols-[minmax(4rem,10rem)_minmax(0,1fr)_auto_auto_auto]";
const HEADINGS = ["Skill", "", "Standing", "Weight", "Points"];

function SkillBarRow({ bar }: { bar: SkillBar }) {
  const value = bar.percentile == null ? null : Math.round(bar.percentile);

  return (
    <li className="col-span-5 grid grid-cols-subgrid items-center">
      <span className="min-w-0 truncate text-xs text-foreground" title={bar.skill}>
        {bar.skill}
      </span>
      <span
        aria-label={
          value == null ? "No benchmark data" : `${ordinal(value)} percentile of ${bar.fieldN}`
        }
        className="relative h-2 min-w-0 rounded-xs bg-border/60"
        role="img"
      >
        {value != null && (
          <>
            <span
              className="block h-full rounded-r-xs bg-primary"
              style={{ width: `${clampPercent(value)}%` }}
            />
            {/* --primary is --foreground: a rule clipped to the track would vanish under
                any bar that clears it, so it overhangs. */}
            <span
              aria-hidden
              className="absolute -inset-y-1 border-l border-dashed border-foreground/70"
              style={{ left: `${MEDIAN_PERCENTILE}%` }}
            />
          </>
        )}
      </span>
      <span
        className={cn(
          "text-right text-xs tabular-nums",
          value == null ? "text-muted-foreground" : "text-foreground"
        )}
      >
        {value ?? "—"}
      </span>
      <span className="text-right text-xs tabular-nums text-muted-foreground">
        &times;{Math.round(bar.weight * 100)}%
      </span>
      <span className="text-right text-xs tabular-nums text-foreground">
        {formatScore(contribution(bar))}
      </span>
    </li>
  );
}

/** What this skill puts into the match. Weights span the whole task, so a skill with no
 *  benchmark behind it contributes nothing rather than being renormalised away. */
function contribution(bar: SkillBar): number {
  return bar.weight * (bar.percentile ?? 0);
}

/** The working behind the match: every skill the task weights, where the model stands on
 *  it, and the points that pair is worth. The points sum to the score in the card header,
 *  so the number is read off the chart rather than asserted beside it. */
export function SkillChart({ bars, className }: { bars: SkillBar[]; className?: string }) {
  if (bars.length === 0) return null;
  const field = fieldSize(bars);

  return (
    <ul className={cn("grid min-w-0 items-center gap-x-3 gap-y-2.5", COLUMNS, className)}>
      <li aria-hidden className="col-span-5 grid grid-cols-subgrid">
        {HEADINGS.map((heading, i) => (
          <span
            className={cn("pixel-label text-xs text-muted-foreground", i > 1 && "text-right")}
            key={heading || String(i)}
          >
            {heading}
          </span>
        ))}
      </li>

      {bars.map((bar) => (
        <SkillBarRow bar={bar} key={bar.skill} />
      ))}

      {bars.some((bar) => bar.rank != null) && (
        // Labels the reference tick where it stands rather than restating it in a legend;
        // hidden from the tree because each bar already reads out its standing.
        <li aria-hidden className="col-span-5 grid grid-cols-subgrid">
          <span className="relative col-start-2 block h-3 min-w-0">
            <span
              className="absolute -translate-x-1/2 whitespace-nowrap text-xs text-muted-foreground"
              style={{ left: `${MEDIAN_PERCENTILE}%` }}
            >
              {field == null ? "median candidate" : `median of ${plural(field, "model")}`}
            </span>
          </span>
        </li>
      )}
    </ul>
  );
}

export function EvidencePanel({
  confidence,
  evidence,
  nBenchmarks,
  skillScores,
  weights,
  className,
}: {
  confidence: ConfidenceEnum;
  evidence: FinetuningEvidence[];
  nBenchmarks: number;
  skillScores: FinetuningSkillScore[];
  weights: Record<string, number>;
  className?: string;
}) {
  const [showBenchmarks, setShowBenchmarks] = useState(false);
  const bars = skillBars(skillScores, weights);
  const benchmarks = new Set(evidence.map((row) => row.benchmark)).size;
  const toggle = `${showBenchmarks ? "Hide" : "Show"} ${count(benchmarks, "benchmark")}`;

  return (
    <div className={cn("flex min-w-0 flex-col gap-3", className)}>
      <SkillChart bars={bars} />

      <Collapsible onOpenChange={setShowBenchmarks} open={showBenchmarks}>
        <div className="flex min-w-0 flex-wrap items-center justify-end gap-x-4 gap-y-1.5">
          {evidence.length > 0 && (
            <CollapsibleTrigger
              className="group flex shrink-0 items-center gap-1.5 rounded-sm text-xs text-muted-foreground outline-none transition-colors hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring/60"
              title={confidenceLabel(confidence, nBenchmarks)}
            >
              <Icon.chevronDown className="size-3.5 transition-transform group-data-[state=open]:rotate-180" />
              {toggle}
            </CollapsibleTrigger>
          )}
        </div>
        <CollapsibleContent>
          <EvidenceTable className="mt-3" rows={evidence} />
        </CollapsibleContent>
      </Collapsible>
    </div>
  );
}

const CELL = "px-2 py-1.5 align-top";
const HEAD = "pixel-label px-2 py-1 text-left font-bold text-muted-foreground";

export function EvidenceTable({
  rows,
  className,
}: {
  rows: FinetuningEvidence[];
  className?: string;
}) {
  if (rows.length === 0) return null;

  return (
    <div className={cn("flex min-w-0 flex-col gap-2", className)}>
      <p className={cn(PROSE, "text-xs text-muted-foreground")}>
        Standing among every model the benchmark tracks, not among the candidates above. Each row
        links to the work that defines it.
      </p>
      <table className="w-full table-fixed border-collapse text-xs">
        <caption className="sr-only">Benchmarks behind this model&apos;s standing</caption>
        <thead>
          <tr className="border-b border-border/70">
            <th className={cn(HEAD, "w-[30%]")} scope="col">
              Benchmark
            </th>
            <th className={cn(HEAD, "w-[28%]")} scope="col">
              Skill
            </th>
            <th className={cn(HEAD, "w-[16%] text-right")} scope="col">
              Standing
            </th>
            <th className={cn(HEAD, "w-[14%] text-right")} scope="col">
              Cohort
            </th>
            <th className={cn(HEAD, "w-[12%] text-right")} scope="col">
              Reference
            </th>
          </tr>
        </thead>
        <tbody>
          {sortEvidence(rows).map((row) => (
            <tr className="border-b border-border/60 last:border-0" key={row.benchmark}>
              <td className={CELL}>
                <span className="block truncate text-foreground" title={row.benchmark}>
                  {row.benchmark}
                </span>
                {row.provenance === "lab_claimed" && (
                  <span className="block truncate text-muted-foreground">self-reported</span>
                )}
              </td>
              <td className={CELL}>
                <span className="block truncate text-muted-foreground" title={row.skill}>
                  {row.skill}
                </span>
              </td>
              <td className={cn(CELL, "text-right tabular-nums text-foreground")}>
                {ordinal(row.percentile)} pct
              </td>
              <td className={cn(CELL, "text-right tabular-nums text-muted-foreground")}>
                {row.cohortN.toLocaleString()}
              </td>
              <td className={cn(CELL, "text-right")}>
                <a
                  aria-label={`${row.benchmark} on ${row.source}`}
                  className="inline-flex max-w-full items-center gap-1 rounded-sm text-muted-foreground outline-none transition-colors hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring/60"
                  href={row.url}
                  rel="noopener noreferrer"
                  target="_blank"
                  title={row.source}
                >
                  <span className="truncate">{row.source}</span>
                  <Icon.externalLink aria-hidden className="size-3 shrink-0" />
                </a>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function UngradedNote({ className }: { className?: string }) {
  return (
    <p className={cn(PROSE, "text-xs text-muted-foreground", className)}>
      No benchmark data. Ranked below every graded model.
    </p>
  );
}

// The reason strings are written by recommendation/constraints.py; the context one
// carries per-model numbers, so grouping matches on its shape rather than its text.
const REASON_GROUPS: [RegExp, string][] = [
  [/^SFT context/i, "below context length"],
  [/tool-calling/i, "without tool-calling support"],
  [/fine-tuning method/i, "cannot be fine-tuned"],
];

function excludedGroups(excluded: FinetuningExcludedModel[]): { label: string; count: number }[] {
  const counts = new Map<string, number>();
  for (const item of excluded) {
    const label = REASON_GROUPS.find(([pattern]) => pattern.test(item.reason))?.[1] ?? item.reason;
    counts.set(label, (counts.get(label) ?? 0) + 1);
  }
  return [...counts.entries()]
    .map(([label, count]) => ({ count, label }))
    .sort((a, b) => b.count - a.count || a.label.localeCompare(b.label));
}

export function ExcludedSummary({
  excluded,
  className,
}: {
  excluded: FinetuningExcludedModel[];
  className?: string;
}) {
  if (excluded.length === 0) return null;
  const groups = excludedGroups(excluded);

  return (
    <Collapsible className={cn("shrink-0 rounded-md border border-border/60 bg-card", className)}>
      <CollapsibleTrigger className="group flex w-full items-start gap-1.5 px-3 py-2 text-left text-xs text-muted-foreground transition-colors hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/60">
        <Icon.chevronDown className="mt-px size-3.5 shrink-0 transition-transform group-data-[state=open]:rotate-180" />
        <span className="min-w-0 flex-1">
          <span className="text-foreground">{plural(excluded.length, "model")} excluded</span>
          {groups.map((group) => ` · ${group.count} ${group.label}`).join("")}
        </span>
      </CollapsibleTrigger>
      <CollapsibleContent>
        <ul className="flex flex-col gap-2 border-t border-border/70 px-3 py-2">
          {excluded.map((item) => (
            <li className="flex min-w-0 flex-col text-xs" key={item.model}>
              <span className="truncate font-mono text-foreground" title={item.model}>
                {item.model}
              </span>
              <span className="truncate text-muted-foreground" title={item.reason}>
                {item.reason}
              </span>
            </li>
          ))}
        </ul>
      </CollapsibleContent>
    </Collapsible>
  );
}
