import { useState } from "react";

import { Link } from "@tanstack/react-router";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { DateTime } from "@/components/ui/datetime";
import { Icon } from "@/components/ui/icons";
import { lazyChart } from "@/components/ui/lazy-chart";
import { useSignalInsightsQuery } from "@/hooks/use-signals";
import { humanizeKey } from "@/lib/label-case";
import { LABEL } from "@/lib/typography";
import { cn } from "@/lib/utils";
import type { SignalInsight } from "@/openapi";

const DAYS = 7;
const SHOWN = 5;
const COLORS = [
  "var(--chart-1)",
  "var(--chart-2)",
  "var(--chart-3)",
  "var(--chart-4)",
  "var(--chart-5)",
];
const OTHER_COLOR = "var(--cat-6)";

const SignalInsightsChart = lazyChart(() => import("@/components/traces/signal-insights-chart"), {
  minHeight: 176,
});

function insightKey(insight: SignalInsight) {
  return `${insight.signalType}:${insight.capability ?? ""}`;
}

function insightLabel(insight: SignalInsight) {
  const name = humanizeKey(insight.signalType);
  return insight.capabilityName ? `${name} · ${insight.capabilityName}` : name;
}

const STATUS = {
  falling: { label: "Falling", variant: "success" },
  new: { label: "New", variant: "warning" },
  ongoing: { label: "Ongoing", variant: "neutral" },
  rising: { label: "Rising", variant: "error" },
} as const;

function plural(count: number, noun: string) {
  return `${count} ${noun}${count === 1 ? "" : "s"}`;
}

function Sparkline({ daily, color }: { daily: number[]; color?: string }) {
  const peak = Math.max(...daily, 1);
  return (
    <span aria-hidden="true" className="flex h-5 items-end gap-px">
      {daily.map((count, index) => (
        <span
          className={cn("w-1.5 rounded-xs", count ? !color && "bg-foreground/70" : "bg-border")}
          key={index}
          style={{
            backgroundColor: count ? color : undefined,
            height: count ? `${Math.max(20, (count / peak) * 100)}%` : "2px",
          }}
        />
      ))}
    </span>
  );
}

function Insight({
  insight,
  color,
  projectId,
  focused,
  onFocus,
  onCreateDataset,
}: {
  insight: SignalInsight;
  color?: string;
  projectId: string;
  focused: boolean;
  onFocus: (insight: SignalInsight) => void;
  onCreateDataset?: (insight: SignalInsight) => void;
}) {
  const status = STATUS[insight.status];
  const [cause, ...others] = insight.causes;
  const change =
    insight.status === "rising" || insight.status === "falling"
      ? `${insight.previousEvents} → ${insight.events} vs prior ${DAYS} days`
      : null;

  return (
    <li className={cn("flex flex-col gap-2 px-4 py-3", focused && "bg-wash-raised")}>
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
        <Badge size="chip" variant={status.variant}>
          {status.label}
        </Badge>
        <span className="flex items-center gap-2 text-sm text-foreground">
          {color && (
            <span
              aria-hidden="true"
              className="size-2 shrink-0 rounded-xs"
              style={{ backgroundColor: color }}
            />
          )}
          {humanizeKey(insight.signalType)}
          {insight.capabilityName && (
            <span className="text-muted-foreground"> · {insight.capabilityName}</span>
          )}
        </span>
        <span className="ml-auto flex items-center gap-2 text-xs text-muted-foreground">
          <Sparkline color={color} daily={insight.daily} />
          <span className="tabular-nums">{plural(insight.events, "event")}</span>
        </span>
      </div>

      <p className="text-sm text-foreground">
        {cause ? cause.label : insight.message}
        {cause && cause.count > 1 && (
          <span className="ml-2 tabular-nums text-muted-foreground">×{cause.count}</span>
        )}
      </p>
      {others.length > 0 && (
        <p className="text-xs text-muted-foreground">
          Also: {others.map((other) => `${other.label} ×${other.count}`).join(" · ")}
        </p>
      )}
      {insight.remedy && (
        <p className="text-xs text-muted-foreground">
          <span className="text-foreground">Next:</span> {insight.remedy}
        </p>
      )}

      <div className="flex flex-wrap items-center gap-x-3 gap-y-2 text-xs text-muted-foreground">
        <span className="tabular-nums">{plural(insight.traces, "trace")}</span>
        {insight.sessions > 0 && (
          <span className="tabular-nums">{plural(insight.sessions, "session")}</span>
        )}
        {change && <span className="tabular-nums">{change}</span>}
        <span>
          Last <DateTime value={insight.lastSeen} />
        </span>
        <span className="ml-auto flex flex-wrap gap-2">
          <Button
            aria-pressed={focused}
            onClick={() => onFocus(insight)}
            size="xs"
            variant="secondary"
          >
            <Icon.observability />
            {focused ? "Showing events" : "Events"}
          </Button>
          {onCreateDataset && (
            <Button onClick={() => onCreateDataset(insight)} size="xs" variant="secondary">
              <Icon.datasetAdd />
              Create dataset
            </Button>
          )}
          {insight.capability && (
            <Button asChild size="xs" variant="secondary">
              <Link
                search={{ capabilityId: insight.capability, optimize: true, projectId }}
                to="/optimiser"
              >
                <Icon.optimiser />
                Optimise
              </Link>
            </Button>
          )}
        </span>
      </div>
    </li>
  );
}

export function SignalInsights({
  projectId,
  session,
  focus,
  onFocus,
  onCreateDataset,
}: {
  projectId: string;
  session?: string;
  focus: { signal?: string; capability?: string };
  onFocus: (insight: SignalInsight | null) => void;
  onCreateDataset?: (insight: SignalInsight) => void;
}) {
  const insights = useSignalInsightsQuery(projectId, { days: DAYS, session });
  const [expanded, setExpanded] = useState(false);
  const items = insights.data?.insights ?? [];
  if (!insights.data) return null;

  const fresh = items.filter((item) => item.status === "new").length;
  const rising = items.filter((item) => item.status === "rising").length;
  const headline =
    fresh || rising
      ? [fresh && `${fresh} new`, rising && `${rising} rising`].filter(Boolean).join(", ")
      : items.length
        ? "Nothing new or rising"
        : "No signals";
  const isFocused = (item: SignalInsight) =>
    focus.signal === item.signalType && (focus.capability ?? null) === (item.capability ?? null);
  const charted = items.slice(0, COLORS.length);
  const rest = items.slice(COLORS.length);
  const colors = new Map(charted.map((item, index) => [insightKey(item), COLORS[index]]));
  const series = [
    ...charted.map((item) => ({
      color: colors.get(insightKey(item)) as string,
      daily: item.daily,
      insight: item,
      key: insightKey(item),
      label: insightLabel(item),
    })),
    ...(rest.length
      ? [
          {
            color: OTHER_COLOR,
            daily: insights.data.days.map((_, index) =>
              rest.reduce((sum, item) => sum + (item.daily[index] ?? 0), 0)
            ),
            key: "other",
            label: `${rest.length} other issues`,
          },
        ]
      : []),
  ];
  const focusedItem = items.find(isFocused);

  return (
    <section className="flex flex-col gap-2">
      <h2
        className={cn(
          LABEL.pixel,
          "flex items-center justify-between text-xs text-muted-foreground"
        )}
      >
        <span>
          Last {DAYS} days · <span className="text-foreground">{headline}</span>
        </span>
        {!insights.data.complete && <span>Causes from the newest events</span>}
      </h2>
      {items.length > 0 && (
        <div className="overflow-hidden rounded-md border border-border bg-card">
          <div className="border-b border-border/70 p-3">
            <SignalInsightsChart
              days={insights.data.days}
              focused={focusedItem ? insightKey(focusedItem) : null}
              onFocus={(item) =>
                item.insight && onFocus(isFocused(item.insight) ? null : item.insight)
              }
              series={series}
            />
          </div>
          <ul className="divide-y divide-border/70">
            {(expanded ? items : items.slice(0, SHOWN)).map((item) => {
              const focused = isFocused(item);
              return (
                <Insight
                  color={colors.get(insightKey(item))}
                  focused={focused}
                  insight={item}
                  key={insightKey(item)}
                  onCreateDataset={onCreateDataset}
                  onFocus={() => onFocus(focused ? null : item)}
                  projectId={projectId}
                />
              );
            })}
          </ul>
        </div>
      )}
      {items.length > SHOWN && (
        <Button
          className="self-start"
          onClick={() => setExpanded((open) => !open)}
          size="xs"
          variant="secondary"
        >
          {expanded ? "Show fewer" : `Show all ${items.length}`}
        </Button>
      )}
    </section>
  );
}
