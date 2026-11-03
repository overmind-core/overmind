import { useMemo, useState } from "react";

import { useTraceData } from "@/components/traces/contexts/TraceDataContext";
import { useTraceSelection } from "@/components/traces/contexts/TraceSelectionContext";
import { buildSpanTree, type SpanTreeNode } from "@/components/traces/lib/tree-building";
import { Button } from "@/components/ui/button";
import { Icon } from "@/components/ui/icons";
import { SearchInput } from "@/components/ui/search-input";
import type { SpanRow } from "@/hooks/use-traces";
import { formatDuration, formatNumber } from "@/lib/formatters";
import { cn } from "@/lib/utils";

// Long runs stay readable: repeats fold, and rendering stops until asked.
const FOLD_REPEATS_AT = 3;
const SEARCH_FROM_STEPS = 20;
const ROW_LIMIT = 300;

type Row = {
  key: string;
  depth: number;
  span: SpanRow;
  /** Consecutive siblings with this span's name, this one included. */
  repeats: SpanRow[];
};

function spanName(span: SpanRow) {
  return span.name || span.scopeName || "Unnamed span";
}

export function stepKind(span: SpanRow): "llm" | "tool" | "retrieval" | "other" {
  const type = String(span.spanType ?? "");
  if (type === "llm_call") return "llm";
  if (type === "tool_call" || type === "tool") return "tool";
  if (type === "retrieval") return "retrieval";
  return "other";
}

export const KIND_ICON = {
  llm: Icon.model,
  other: Icon.code,
  retrieval: Icon.search,
  tool: Icon.tool,
};

const KIND_BAR = {
  llm: "bg-cat-1",
  other: "bg-muted-foreground/60",
  retrieval: "bg-cat-3",
  tool: "bg-cat-2",
};

function flatten(
  nodes: SpanTreeNode[],
  depth: number,
  open: Set<string>,
  out: Row[],
  failedOnly: Set<string> | null
) {
  let index = 0;
  while (index < nodes.length) {
    const node = nodes[index];
    let end = index + 1;
    while (
      end < nodes.length &&
      spanName(nodes[end].span) === spanName(node.span) &&
      nodes[end].children.length === 0
    ) {
      end += 1;
    }
    const run = nodes.slice(index, end);
    const folded =
      node.children.length === 0 && run.length >= FOLD_REPEATS_AT && !open.has(node.id);
    if (folded) {
      const repeats = run.map((item) => item.span);
      if (!failedOnly || repeats.some((span) => failedOnly.has(span.spanId))) {
        out.push({ depth, key: node.id, repeats, span: node.span });
      }
      index = end;
      continue;
    }
    if (!failedOnly || failedOnly.has(node.id)) {
      out.push({ depth, key: node.id, repeats: [node.span], span: node.span });
    }
    flatten(node.children, depth + 1, open, out, failedOnly);
    index += 1;
  }
}

function withAncestors(spans: SpanRow[], failing: Set<string>) {
  const byId = new Map(spans.map((span) => [span.spanId, span]));
  const kept = new Set<string>();
  for (const id of failing) {
    let current = byId.get(id);
    while (current && !kept.has(current.spanId)) {
      kept.add(current.spanId);
      current = current.parentSpanId ? byId.get(current.parentSpanId) : undefined;
    }
  }
  return kept;
}

function TimeBar({
  repeats,
  start,
  total,
  kind,
  failed,
}: {
  repeats: SpanRow[];
  start: number;
  total: number;
  kind: keyof typeof KIND_BAR;
  failed: boolean;
}) {
  const from = repeats[0].startTimeUnixNano;
  const to = Math.max(...repeats.map((span) => span.startTimeUnixNano + span.durationNano));
  if (total <= 0 || to <= from) return <span className="h-1.5" />;
  return (
    <span className="relative block h-1.5 rounded-xs bg-wash-raised">
      <span
        className={cn(
          "absolute inset-y-0 min-w-0.5 rounded-xs",
          failed ? "bg-destructive" : KIND_BAR[kind]
        )}
        style={{
          left: `${((from - start) / total) * 100}%`,
          width: `${((to - from) / total) * 100}%`,
        }}
      />
    </span>
  );
}

/** The run in order: nesting and timing in one list, each failure saying why where it happened. */
export function RunSteps({ reasons }: { reasons: Map<string, string> }) {
  const { spans } = useTraceData();
  const { selectedSpanId, setSelectedSpanId } = useTraceSelection();
  const [open, setOpen] = useState<Set<string>>(new Set());
  const [failuresOnly, setFailuresOnly] = useState(false);
  const [search, setSearch] = useState("");
  const [showAll, setShowAll] = useState(false);

  const roots = useMemo(() => buildSpanTree(spans), [spans]);
  const failing = useMemo(
    () =>
      new Set(
        spans
          .filter((span) => span.statusCode === 2 || reasons.has(span.spanId))
          .map((span) => span.spanId)
      ),
    [reasons, spans]
  );
  const { start, total } = useMemo(() => {
    const timed = spans.filter((span) => span.startTimeUnixNano > 0);
    if (!timed.length) return { start: 0, total: 0 };
    const from = Math.min(...timed.map((span) => span.startTimeUnixNano));
    const to = Math.max(...timed.map((span) => span.startTimeUnixNano + span.durationNano));
    return { start: from, total: to - from };
  }, [spans]);

  const rows = useMemo(() => {
    const out: Row[] = [];
    const filter = failuresOnly ? withAncestors(spans, failing) : null;
    flatten(roots, 0, open, out, filter);
    const query = search.trim().toLowerCase();
    if (!query) return out;
    return out.filter(({ repeats }) =>
      repeats.some((span) =>
        [span.name, span.scopeName, span.model, span.spanId].some((value) =>
          value?.toLowerCase().includes(query)
        )
      )
    );
  }, [failing, failuresOnly, open, roots, search, spans]);
  const shown = showAll ? rows : rows.slice(0, ROW_LIMIT);

  return (
    <div className="flex h-full min-h-0 min-w-0 flex-col">
      <div className="flex shrink-0 items-center gap-2 border-b border-border/70 px-3 py-2">
        <span className="text-sm text-foreground">
          {formatNumber(spans.length)} {spans.length === 1 ? "step" : "steps"}
        </span>
        {failing.size > 0 && (
          <Button
            aria-pressed={failuresOnly}
            className="ml-auto"
            onClick={() => setFailuresOnly((value) => !value)}
            size="xs"
            variant={failuresOnly ? "default" : "secondary"}
          >
            <Icon.failed />
            {formatNumber(failing.size)} failed
          </Button>
        )}
      </div>
      {spans.length >= SEARCH_FROM_STEPS && (
        <div className="shrink-0 border-b border-border/70 px-3 py-2">
          <SearchInput
            label="Search steps"
            onChange={(event) => setSearch(event.target.value)}
            onClear={() => setSearch("")}
            placeholder="Search steps…"
            value={search}
          />
        </div>
      )}
      <ul className="min-h-0 flex-1 overflow-auto py-1">
        {shown.map(({ key, depth, span, repeats }) => {
          const kind = stepKind(span);
          const KindIcon = KIND_ICON[kind];
          const failedRepeat = repeats.find((item) => failing.has(item.spanId));
          const selected = repeats.some((item) => item.spanId === selectedSpanId);
          const reason = failedRepeat
            ? reasons.get(failedRepeat.spanId) || failedRepeat.statusMessage
            : "";
          const elapsed =
            Math.max(...repeats.map((item) => item.startTimeUnixNano + item.durationNano)) -
            repeats[0].startTimeUnixNano;
          return (
            <li key={key}>
              <button
                aria-current={selected || undefined}
                className={cn(
                  "grid w-full grid-cols-[minmax(0,1fr)_6rem_3.5rem] items-center gap-x-3 border-l-2 border-l-transparent py-1.5 pr-3 text-left text-xs hover:bg-wash-subtle focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-ring",
                  selected && "border-l-foreground bg-wash-raised hover:bg-wash-raised"
                )}
                onClick={() => {
                  setSelectedSpanId((failedRepeat ?? repeats[0]).spanId);
                  if (repeats.length > 1) setOpen((prev) => new Set(prev).add(key));
                }}
                style={{ paddingLeft: 10 + Math.min(depth, 8) * 14 }}
                type="button"
              >
                <span className="flex min-w-0 items-center gap-2">
                  {failedRepeat ? (
                    <Icon.failed
                      aria-label="Failed"
                      className="size-3.5 shrink-0 text-destructive"
                    />
                  ) : (
                    <KindIcon aria-hidden className="size-3.5 shrink-0 text-muted-foreground" />
                  )}
                  <span
                    className={cn("truncate", selected ? "text-foreground" : "text-foreground/90")}
                  >
                    {spanName(span)}
                  </span>
                  {repeats.length > 1 && (
                    <span className="shrink-0 tabular-nums text-muted-foreground">
                      ×{repeats.length}
                    </span>
                  )}
                </span>
                <TimeBar
                  failed={!!failedRepeat}
                  kind={kind}
                  repeats={repeats}
                  start={start}
                  total={total}
                />
                <span className="text-right tabular-nums text-muted-foreground">
                  {elapsed > 0 ? formatDuration(elapsed / 1_000_000) : "—"}
                </span>
                {reason && (
                  <span
                    className="col-span-3 mt-0.5 truncate text-destructive"
                    style={{ paddingLeft: 22 }}
                    title={reason}
                  >
                    {reason}
                  </span>
                )}
              </button>
            </li>
          );
        })}
        {rows.length === 0 && (
          <li className="px-3 py-6 text-center text-xs text-muted-foreground">No matching steps</li>
        )}
        {rows.length > shown.length && (
          <li className="px-3 py-2">
            <Button
              className="w-full"
              onClick={() => setShowAll(true)}
              size="xs"
              variant="secondary"
            >
              Show all {formatNumber(rows.length)} rows
            </Button>
          </li>
        )}
      </ul>
    </div>
  );
}
