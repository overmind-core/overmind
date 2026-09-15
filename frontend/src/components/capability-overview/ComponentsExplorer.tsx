import { useLayoutEffect, useMemo, useRef, useState } from "react";

import { Tabs as TabsPrimitive } from "radix-ui";

import { FileRefList } from "@/components/capability-detail/file-ref";
import { ModelProviderChip } from "@/components/model-provider-chip";
import { Badge } from "@/components/ui/badge";
import { Chip } from "@/components/ui/chip";
import { Icon } from "@/components/ui/icons";
import { Input } from "@/components/ui/input";
import { PROSE } from "@/lib/typography";
import { cn } from "@/lib/utils";
import type { Capability, CapabilityFlowToolArgument } from "@/openapi";
import {
  buildComponentEntries,
  type ComponentEntry,
  type ComponentKind,
  KIND_META,
  KIND_ORDER,
} from "./component-entries";

type Flow = Capability["flow"];

// A ceiling, not a fixed height: both panes are grid items in one row, so they
// stretch to the taller one and stop at 26rem.
const PANE_HEIGHT = "max-h-[26rem] min-h-[14rem]";

type Filter = "all" | ComponentKind;

function RailRow({ entry }: { entry: ComponentEntry }) {
  return (
    <TabsPrimitive.Trigger
      className={cn(
        "group flex w-full flex-col gap-0.5 rounded-sm px-2 py-1.5 text-left transition-colors duration-150",
        "hover:bg-wash-raised focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring",
        "data-[state=active]:bg-muted"
      )}
      value={entry.id}
    >
      <span className="flex w-full items-center gap-1.5">
        <span className="min-w-0 flex-1 truncate font-mono text-xs text-muted-foreground group-data-[state=active]:text-foreground">
          {entry.name}
        </span>
      </span>
      {(entry.signal || entry.purpose) && (
        <span className="flex w-full min-w-0 items-baseline gap-1.5">
          {entry.signal && (
            <span className="shrink-0 text-xs text-muted-foreground">{entry.signal}</span>
          )}
          {entry.signal && entry.purpose && (
            <span aria-hidden className="shrink-0 text-xs text-muted-foreground">
              ·
            </span>
          )}
          {entry.purpose && (
            <span className="min-w-0 flex-1 truncate text-xs text-muted-foreground">
              {entry.purpose}
            </span>
          )}
        </span>
      )}
    </TabsPrimitive.Trigger>
  );
}

function ArgumentsTable({ args }: { args: CapabilityFlowToolArgument[] }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-xs">
        <thead>
          <tr className="pixel-label border-b border-border/60 text-left text-xs text-muted-foreground">
            <th className="py-1.5 pr-4 font-medium">Name</th>
            <th className="py-1.5 pr-4 font-medium">Type</th>
            <th className="w-full py-1.5 font-medium">Description</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-border/70">
          {args.map((arg) => (
            <tr key={arg.name}>
              <td className="whitespace-nowrap py-2 pr-4 align-top">
                <span className="font-mono text-xs text-foreground">{arg.name}</span>
                {!arg.required && (
                  <span className="ml-1.5 text-xs text-muted-foreground">optional</span>
                )}
              </td>
              <td className="whitespace-nowrap py-2 pr-4 align-top">
                {arg.type?.trim() ? (
                  <code className="rounded-sm border border-border/60 bg-wash-raised px-1.5 py-0.5 text-xs leading-4 text-foreground">
                    {arg.type.trim()}
                  </code>
                ) : (
                  <span className="text-muted-foreground">—</span>
                )}
              </td>
              <td
                className={cn(
                  PROSE,
                  "py-2 align-top text-sm leading-relaxed text-muted-foreground"
                )}
              >
                {arg.description?.trim() || <span className="text-muted-foreground">—</span>}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function DetailSection({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <section className="space-y-2">
      <p className="pixel-label text-xs text-muted-foreground">{label}</p>
      {children}
    </section>
  );
}

function ComponentDetail({ entry }: { entry: ComponentEntry }) {
  const isModel = entry.kind !== "tool" && entry.signal;

  return (
    <div className="space-y-4 p-4">
      <div className="space-y-2">
        <div className="flex flex-wrap items-center gap-x-2 gap-y-1.5">
          <h3 className="min-w-0 break-all font-mono text-sm font-semibold leading-5 text-foreground">
            {entry.name}
          </h3>
          <Badge className="h-5 px-1.5 text-xs font-medium" variant="neutral">
            {KIND_META[entry.kind].label}
          </Badge>
        </div>

        {entry.purpose && (
          <p className={cn(PROSE, "max-w-[70ch] text-sm leading-relaxed text-foreground")}>
            {entry.purpose}
          </p>
        )}

        {(entry.signal || entry.traits.length > 0) && (
          <div className="flex flex-wrap items-center gap-1.5">
            {isModel ? (
              <ModelProviderChip compact model={entry.signal} />
            ) : entry.signal ? (
              <Badge className="h-5 px-1.5 text-xs font-medium" variant="outline">
                {entry.signal}
              </Badge>
            ) : null}
            {entry.traits.map((trait) => (
              <Badge className="h-5 px-1.5 text-xs font-medium" key={trait} variant="outline">
                {trait}
              </Badge>
            ))}
          </div>
        )}
      </div>

      {entry.facts.length > 0 && (
        <dl className="grid grid-cols-[max-content_minmax(0,1fr)] gap-x-4 gap-y-2">
          {entry.facts.map((fact) => (
            <div className="contents" key={fact.label}>
              <dt className="pixel-label text-xs leading-relaxed text-muted-foreground">
                {fact.label}
              </dt>
              <dd className="min-w-0 max-w-[70ch] break-words text-sm leading-relaxed text-foreground">
                {fact.value}
              </dd>
            </div>
          ))}
        </dl>
      )}

      {entry.args.length > 0 && (
        <DetailSection label={`Arguments · ${entry.args.length}`}>
          <ArgumentsTable args={entry.args} />
        </DetailSection>
      )}

      {entry.prompt && (
        <DetailSection label="Prompt">
          <pre className="max-h-56 overflow-auto whitespace-pre-wrap break-words rounded-sm border border-border/60 bg-wash-subtle p-3 font-mono text-xs leading-relaxed text-foreground">
            {entry.prompt}
          </pre>
        </DetailSection>
      )}

      {entry.provenance.length > 0 && (
        <DetailSection label="Source">
          <FileRefList references={entry.provenance} />
        </DetailSection>
      )}
    </div>
  );
}

function FilterChip({
  active,
  count,
  label,
  onClick,
}: {
  active: boolean;
  count: number;
  label: string;
  onClick: () => void;
}) {
  return (
    <Chip onClick={onClick} selected={active} size="sm">
      {label}
      <span className="tabular-nums opacity-70">{count}</span>
    </Chip>
  );
}

export function ComponentsExplorer({ flow }: { flow: Flow }) {
  const entries = useMemo(() => buildComponentEntries(flow), [flow]);
  const [filter, setFilter] = useState<Filter>("all");
  const [query, setQuery] = useState("");
  const [selected, setSelected] = useState<string | null>(null);
  const detailRef = useRef<HTMLDivElement>(null);

  const counts = useMemo(() => {
    const byKind = { task: 0, tool: 0, utility: 0 } as Record<ComponentKind, number>;
    for (const entry of entries) {
      byKind[entry.kind] += 1;
    }
    return { byKind };
  }, [entries]);

  const visibleEntries = useMemo(() => {
    const q = query.trim().toLowerCase();
    return entries.filter((entry) => {
      if (filter !== "all" && entry.kind !== filter) return false;
      if (!q) return true;
      return (
        entry.name.toLowerCase().includes(q) ||
        entry.purpose.toLowerCase().includes(q) ||
        entry.signal.toLowerCase().includes(q)
      );
    });
  }, [entries, filter, query]);

  // Keep a valid selection as filters narrow the list out from under it.
  const activeId = visibleEntries.some((entry) => entry.id === selected)
    ? (selected as string)
    : (visibleEntries[0]?.id ?? "");

  // The detail pane owns its scroll offset, so moving from a long component to a
  // short one would drop you into the middle of the new one.
  // biome-ignore lint/correctness/useExhaustiveDependencies: activeId is the reset trigger, not a value the effect reads.
  useLayoutEffect(() => {
    if (detailRef.current) detailRef.current.scrollTop = 0;
  }, [activeId]);

  if (entries.length === 0) return null;

  const visible = visibleEntries;
  const activeEntry = visible.find((entry) => entry.id === activeId);

  return (
    <section className="overflow-hidden rounded-md border border-border bg-card">
      <div className="flex flex-wrap items-center justify-between gap-x-3 gap-y-2 border-b border-border/70 bg-wash-raised px-4 py-2.5">
        <span className="inline-flex items-center gap-1.5 text-xs text-muted-foreground">
          <Icon.components className="size-3.5" />
          Components
        </span>
      </div>

      <div className="flex flex-wrap items-center justify-between gap-x-3 gap-y-2 border-b border-border/70 px-3 py-2">
        <div className="flex flex-wrap items-center gap-1">
          <FilterChip
            active={filter === "all"}
            count={entries.length}
            label="All"
            onClick={() => setFilter("all")}
          />
          {KIND_ORDER.filter((kind) => counts.byKind[kind] > 0).map((kind) => (
            <FilterChip
              active={filter === kind}
              count={counts.byKind[kind]}
              key={kind}
              label={KIND_META[kind].plural}
              onClick={() => setFilter(kind)}
            />
          ))}
        </div>
        <div className="relative w-full sm:w-56">
          <Icon.search
            aria-hidden
            className="pointer-events-none absolute left-2 top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground"
          />
          <Input
            aria-label="Filter components"
            className="pl-7 text-xs"
            onChange={(event) => setQuery(event.target.value)}
            placeholder="Filter by name or purpose"
            size="sm"
            value={query}
          />
        </div>
      </div>

      {visible.length === 0 ? (
        <div className="flex h-40 items-center justify-center px-4">
          <p className="text-sm text-muted-foreground">No components match this filter.</p>
        </div>
      ) : (
        <TabsPrimitive.Root
          activationMode="automatic"
          onValueChange={setSelected}
          orientation="vertical"
          value={activeId}
        >
          <div className="flex flex-col md:grid md:grid-cols-[minmax(0,17rem)_minmax(0,1fr)]">
            <TabsPrimitive.List
              aria-label="Capability components"
              className={cn(
                "flex shrink-0 flex-col gap-0.5 overflow-y-auto border-b border-border/70 p-2 md:border-b-0 md:border-r",
                PANE_HEIGHT
              )}
            >
              {filter === "all"
                ? KIND_ORDER.map((kind) => {
                    const kindEntries = visible.filter((entry) => entry.kind === kind);
                    if (kindEntries.length === 0) return null;
                    return (
                      <div key={kind}>
                        <p
                          className="pixel-label px-2 pb-1 pt-3 text-xs text-muted-foreground first:pt-1"
                          title={KIND_META[kind].description}
                        >
                          {KIND_META[kind].plural}
                        </p>
                        <div className="flex flex-col gap-0.5">
                          {kindEntries.map((entry) => (
                            <RailRow entry={entry} key={entry.id} />
                          ))}
                        </div>
                      </div>
                    );
                  })
                : visible.map((entry) => <RailRow entry={entry} key={entry.id} />)}
            </TabsPrimitive.List>

            {/* A single Content bound to the active id, not one per entry: keeps
                Radix's trigger→panel wiring without 30 scroll containers. */}
            <TabsPrimitive.Content
              className={cn(
                "min-w-0 overflow-y-auto focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring",
                PANE_HEIGHT
              )}
              ref={detailRef}
              value={activeId}
            >
              {activeEntry && <ComponentDetail entry={activeEntry} />}
            </TabsPrimitive.Content>
          </div>
        </TabsPrimitive.Root>
      )}
    </section>
  );
}
