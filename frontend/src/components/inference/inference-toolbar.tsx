import { useMemo } from "react";

import { ListToolbar } from "@/components/ui/list-toolbar";
import { SearchInput } from "@/components/ui/search-input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { useProjectCapabilitiesQuery } from "@/hooks/use-evaluations";
import type { InferenceSearch } from "@/lib/schemas";

/** Agentless sentinel — the datasets list and the backend filter use this value too. */
export const NO_CAPABILITY = "__none__";

const SORT_OPTIONS = [
  ["-created_at", "Newest models"],
  ["-recent_requests", "Requests: high to low"],
  ["recent_requests", "Requests: low to high"],
  ["-recent_success_rate", "Success rate: high to low"],
  ["recent_success_rate", "Success rate: low to high"],
  ["recent_latency", "Latency: low to high"],
  ["-recent_latency", "Latency: high to low"],
  ["-recent_speed", "Speed: high to low"],
  ["recent_speed", "Speed: low to high"],
] as const;

export interface InferenceToolbarProps {
  deploymentFilter?: InferenceSearch["inf_deployment"];
  onDeploymentFilterChange?: (value: InferenceSearch["inf_deployment"]) => void;
  ordering?: InferenceSearch["inf_ordering"];
  onOrderingChange?: (value: InferenceSearch["inf_ordering"]) => void;
  projectId?: string;
  /** A capability id, `NO_CAPABILITY`, or `"all"`. */
  capabilityFilter: string;
  onCapabilityFilterChange: (value: string) => void;
  /** Draft text; the route debounces it into the URL. */
  search: string;
  onSearchChange: (value: string) => void;
  onSearchClear: () => void;
  hasActiveFilters: boolean;
  onClearFilters: () => void;
  resultCount?: number;
}

/** Capability options come from the project, never the loaded page of rows. */
export function InferenceToolbar({
  deploymentFilter = "all",
  onDeploymentFilterChange,
  ordering = "-created_at",
  onOrderingChange,
  projectId,
  capabilityFilter,
  onCapabilityFilterChange,
  search,
  onSearchChange,
  onSearchClear,
  hasActiveFilters,
  onClearFilters,
  resultCount,
}: InferenceToolbarProps) {
  const { data: capabilitiesData } = useProjectCapabilitiesQuery(projectId);

  const capabilityOptions = useMemo(() => {
    const byId = new Map<string, string>();
    for (const a of capabilitiesData?.results ?? []) {
      if (a.id) byId.set(a.id, a.name || `Capability ${a.id.slice(0, 8)}`);
    }
    if (
      capabilityFilter !== "all" &&
      capabilityFilter !== NO_CAPABILITY &&
      !byId.has(capabilityFilter)
    ) {
      byId.set(capabilityFilter, "Unknown capability");
    }
    return [...byId]
      .map(([id, name]) => ({ id, name }))
      .sort((a, b) => a.name.localeCompare(b.name));
  }, [capabilitiesData, capabilityFilter]);

  const showCapabilitySelect = capabilityOptions.length > 0 || capabilityFilter !== "all";

  return (
    <ListToolbar
      actions={
        resultCount === undefined ? undefined : (
          <span aria-live="polite" className="sr-only">
            {resultCount === 1 ? "1 model" : `${resultCount.toLocaleString()} models`}
          </span>
        )
      }
      filters={
        <>
          {onDeploymentFilterChange && (
            <Select
              onValueChange={(value) =>
                onDeploymentFilterChange(value as InferenceSearch["inf_deployment"])
              }
              value={deploymentFilter}
            >
              <SelectTrigger aria-label="Filter by deployment type" className="w-[160px]">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="all">All deployments</SelectItem>
                <SelectItem value="serverless">Serverless</SelectItem>
                <SelectItem value="dedicated">Dedicated</SelectItem>
              </SelectContent>
            </Select>
          )}
          {showCapabilitySelect && (
            <Select onValueChange={onCapabilityFilterChange} value={capabilityFilter}>
              <SelectTrigger aria-label="Filter by capability" className="w-[160px]" size="default">
                <SelectValue placeholder="Capability" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="all">All capabilities</SelectItem>
                {capabilityOptions.map((a) => (
                  <SelectItem key={a.id} value={a.id}>
                    {a.name}
                  </SelectItem>
                ))}
                <SelectItem value={NO_CAPABILITY}>No capability</SelectItem>
              </SelectContent>
            </Select>
          )}
          {onOrderingChange && (
            <Select
              onValueChange={(value) => onOrderingChange(value as InferenceSearch["inf_ordering"])}
              value={ordering}
            >
              <SelectTrigger aria-label="Sort models" className="w-[210px]">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {SORT_OPTIONS.map(([value, label]) => (
                  <SelectItem key={value} value={value}>
                    {label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          )}
        </>
      }
      hasActiveFilters={hasActiveFilters}
      onClearFilters={onClearFilters}
      search={
        <SearchInput
          className="min-w-[200px] flex-1"
          label="Search models"
          onChange={(e) => onSearchChange(e.target.value)}
          onClear={onSearchClear}
          placeholder="Search model name, base model or ID…"
          value={search}
        />
      }
      shareable
    />
  );
}
