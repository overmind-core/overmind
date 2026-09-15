import { useEffect, useMemo, useRef, useState } from "react";

import { Link } from "@tanstack/react-router";

import { ModelProviderChip } from "@/components/model-provider-chip";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { DateTime } from "@/components/ui/datetime";
import { EmptyState } from "@/components/ui/empty-state";
import { Icon } from "@/components/ui/icons";
import { useListShortcuts } from "@/components/ui/list-toolbar";
import { SearchInput } from "@/components/ui/search-input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { TITLE } from "@/lib/typography";
import { cn } from "@/lib/utils";
import type { CapabilityList } from "@/openapi";

export type { CapabilityList };

/** Modality from the codebase analysis; empty when the capability was never analysed. */
const capabilityType = (capability: CapabilityList): string | null =>
  capability.modality?.trim() || null;

const formatTypeLabel = (raw: string): string =>
  raw
    .replace(/[-_]+/g, " ")
    .replace(/\b\w/g, (c) => c.toUpperCase())
    .trim();

function MetricRow({
  icon,
  label,
  value,
  valueTitle,
  children,
}: {
  icon: React.ReactNode;
  label: string;
  value?: string;
  valueTitle?: string;
  children?: React.ReactNode;
}) {
  return (
    <div className="flex min-w-0 items-center gap-2.5 py-1.5">
      <span className="flex size-5 shrink-0 items-center justify-center text-muted-foreground">
        {icon}
      </span>
      <span className="shrink-0 text-sm text-muted-foreground">{label}</span>
      <div className="min-w-0 flex-1 overflow-hidden text-right text-sm font-medium text-foreground">
        {children ? (
          <span className="inline-flex max-w-full min-w-0 justify-end">{children}</span>
        ) : (
          <span className="block truncate font-mono text-xs" title={valueTitle ?? value}>
            {value}
          </span>
        )}
      </div>
    </div>
  );
}

function CapabilityCard({ capability }: { capability: CapabilityList }) {
  const toolNames = capability.toolNames ?? [];

  return (
    // The wash, not bg-card: these cards sit on the shell's card surface, which
    // bg-card would give them no separation from.
    <div className="flex h-full w-full min-w-0 cursor-pointer flex-col rounded-md border border-border bg-wash-subtle px-5 pb-4 pt-4 transition-all hover:border-[var(--accent-warm)] hover:bg-wash-raised">
      <div className="mb-1.5 flex items-start justify-between gap-2">
        <h3 className={cn(TITLE.card, "min-w-0 flex-1 truncate capitalize text-foreground")}>
          {capability.name}
        </h3>
        {capability.observed ? (
          <Badge className="shrink-0 text-xs leading-5" variant="neutral">
            Observed
          </Badge>
        ) : null}
      </div>

      <p className="mb-3 truncate font-mono text-xs leading-5 text-muted-foreground">
        {capability.slug}
      </p>

      <div className="mt-auto min-w-0 divide-y divide-border/70">
        {capability.model && (
          <MetricRow icon={<Icon.model className="size-3" />} label="Model">
            <span className="ml-auto flex min-w-0 items-center gap-1.5">
              <ModelProviderChip className="min-w-0 text-xs" compact model={capability.model} />
              {/* Neutral, not success: routing is a configuration fact, not health. */}
              {capability.activeModel ? (
                <Badge className="shrink-0 text-xs leading-5" variant="neutral">
                  Routed
                </Badge>
              ) : null}
            </span>
          </MetricRow>
        )}
        {capability.analyzerModel && (
          <MetricRow icon={<Icon.ai className="size-3" />} label="Analyser">
            <ModelProviderChip
              className="ml-auto min-w-0 text-xs"
              compact
              model={capability.analyzerModel}
            />
          </MetricRow>
        )}
        {toolNames.length > 0 && (
          <MetricRow
            icon={<Icon.tool className="size-3" />}
            label="Tools"
            value={String(toolNames.length)}
            valueTitle={toolNames.join(", ")}
          />
        )}
        {capability.datasetSize != null && capability.datasetSize > 0 && (
          <MetricRow
            icon={<Icon.dataset className="size-3" />}
            label="Dataset"
            value={`${capability.datasetSize} sample${capability.datasetSize !== 1 ? "s" : ""}${capability.datasetHasExpectedOutput ? " (w/ expected)" : ""}`}
          />
        )}
        {capability.sourcePath && (
          <MetricRow
            icon={<Icon.terminal className="size-3" />}
            label="Path"
            value={capability.sourcePath}
            valueTitle={capability.sourcePath}
          />
        )}
        {capability.traceCount != null && capability.traceCount > 0 && (
          <MetricRow
            icon={<Icon.observability className="size-3" />}
            label="Traces"
            value={String(capability.traceCount)}
          />
        )}
        <MetricRow icon={<Icon.job className="size-3" />} label="Updated">
          <DateTime className="truncate font-mono text-xs" value={capability.updatedAt} />
        </MetricRow>
      </div>
    </div>
  );
}

type DatasetFilter = "all" | "with" | "without";
type SortKey = "newest" | "oldest" | "updated" | "name-asc" | "name-desc";

// Mirrors the API's `-created_at` default ordering, so the grid is untouched
// until the user picks a different sort.
const DEFAULT_SORT: SortKey = "newest";

const SORT_OPTIONS: { value: SortKey; label: string }[] = [
  { label: "Newest first", value: "newest" },
  { label: "Oldest first", value: "oldest" },
  { label: "Recently updated", value: "updated" },
  { label: "Name A–Z", value: "name-asc" },
  { label: "Name Z–A", value: "name-desc" },
];

const SORT_COMPARATORS: Record<SortKey, (a: CapabilityList, b: CapabilityList) => number> = {
  "name-asc": (a, b) => a.name.localeCompare(b.name, undefined, { sensitivity: "base" }),
  "name-desc": (a, b) => b.name.localeCompare(a.name, undefined, { sensitivity: "base" }),
  newest: (a, b) => b.createdAt.getTime() - a.createdAt.getTime(),
  oldest: (a, b) => a.createdAt.getTime() - b.createdAt.getTime(),
  updated: (a, b) => b.updatedAt.getTime() - a.updatedAt.getTime(),
};

const matchesQuery = (capability: CapabilityList, q: string): boolean =>
  capability.name.toLowerCase().includes(q) ||
  (capability.slug ?? "").toLowerCase().includes(q) ||
  (capability.description ?? "").toLowerCase().includes(q) ||
  (capability.model ?? "").toLowerCase().includes(q) ||
  (capability.sourcePath ?? "").toLowerCase().includes(q);

const useDebouncedValue = (value: string, delayMs: number): string => {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const t = setTimeout(() => setDebounced(value), delayMs);
    return () => clearTimeout(t);
  }, [value, delayMs]);
  return debounced;
};

export function CapabilityBrowser({
  capabilities,
  projectId,
}: {
  capabilities: CapabilityList[];
  projectId: string;
}) {
  const [search, setSearch] = useState("");
  const [typeFilter, setTypeFilter] = useState("all");
  const [datasetFilter, setDatasetFilter] = useState<DatasetFilter>("all");
  const [sort, setSort] = useState<SortKey>(DEFAULT_SORT);
  const searchRef = useRef<HTMLInputElement | null>(null);

  const query = useDebouncedValue(search, 200).trim().toLowerCase();

  const typeOptions = useMemo(() => {
    const types = new Set<string>();
    for (const capability of capabilities) {
      const type = capabilityType(capability);
      if (type) types.add(type);
    }
    return [...types].sort();
  }, [capabilities]);

  const datasetIsMixed = useMemo(
    () =>
      capabilities.some((a) => a.datasetSize > 0) && capabilities.some((a) => a.datasetSize === 0),
    [capabilities]
  );

  const visibleCapabilities = useMemo(() => {
    const filtered = capabilities.filter((capability) => {
      if (query && !matchesQuery(capability, query)) return false;
      if (typeFilter !== "all" && capabilityType(capability) !== typeFilter) return false;
      if (datasetFilter === "with" && capability.datasetSize === 0) return false;
      if (datasetFilter === "without" && capability.datasetSize > 0) return false;
      return true;
    });
    return filtered.sort(SORT_COMPARATORS[sort]);
  }, [capabilities, query, typeFilter, datasetFilter, sort]);

  const handleClearFilters = () => {
    setSearch("");
    setTypeFilter("all");
    setDatasetFilter("all");
  };

  // Not a ListToolbar, but the list shortcuts are the same everywhere.
  useListShortcuts({ onClearFilters: handleClearFilters, searchRef });

  if (capabilities.length < 2) {
    return (
      <div className="min-w-0 space-y-4">
        <CapabilityGrid capabilities={capabilities} projectId={projectId} />
      </div>
    );
  }

  return (
    <div className="min-w-0 space-y-4">
      <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
        <div className="flex min-w-0 flex-1 flex-wrap items-center gap-2">
          <SearchInput
            className="min-w-[200px] flex-1"
            label="Search capabilities"
            onChange={(e) => setSearch(e.target.value)}
            onClear={() => setSearch("")}
            placeholder="Search capabilities…"
            ref={searchRef}
            value={search}
          />

          {typeOptions.length >= 2 && (
            <Select onValueChange={setTypeFilter} value={typeFilter}>
              <SelectTrigger aria-label="Filter by type" className="w-44" size="default">
                <SelectValue placeholder="Type" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="all">All types</SelectItem>
                {typeOptions.map((type) => (
                  <SelectItem key={type} value={type}>
                    {formatTypeLabel(type)}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          )}

          {datasetIsMixed && (
            <Select
              onValueChange={(v) => setDatasetFilter(v as DatasetFilter)}
              value={datasetFilter}
            >
              <SelectTrigger aria-label="Filter by dataset" className="w-36" size="default">
                <SelectValue placeholder="Dataset" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="all">Any dataset</SelectItem>
                <SelectItem value="with">Has dataset</SelectItem>
                <SelectItem value="without">No dataset</SelectItem>
              </SelectContent>
            </Select>
          )}

          <Select onValueChange={(v) => setSort(v as SortKey)} value={sort}>
            <SelectTrigger aria-label="Sort capabilities" className="w-44" size="default">
              <SelectValue placeholder="Sort" />
            </SelectTrigger>
            <SelectContent>
              {SORT_OPTIONS.map((option) => (
                <SelectItem key={option.value} value={option.value}>
                  {option.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
      </div>

      {visibleCapabilities.length === 0 ? (
        <EmptyState
          action={
            <Button onClick={handleClearFilters} size="sm" variant="secondary">
              <Icon.close />
              Clear filters
            </Button>
          }
          className="rounded-md border border-dashed border-border"
          description="No capabilities match the current filters."
          icon={Icon.search}
          size="section"
          title="No matching capabilities"
        />
      ) : (
        <CapabilityGrid capabilities={visibleCapabilities} projectId={projectId} />
      )}
    </div>
  );
}

export function CapabilityGrid({
  capabilities,
  projectId,
}: {
  capabilities: CapabilityList[];
  projectId: string;
}) {
  return (
    <div className="grid min-w-0 grid-cols-1 gap-4 md:grid-cols-2 lg:grid-cols-3">
      {capabilities.map((capability) => (
        <Link
          className="flex h-full min-w-0 flex-col rounded-md outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
          key={capability.id}
          params={{ capabilityId: capability.id }}
          search={projectId ? { projectId } : undefined}
          to="/capabilities/$capabilityId"
        >
          <CapabilityCard capability={capability} />
        </Link>
      ))}
    </div>
  );
}
