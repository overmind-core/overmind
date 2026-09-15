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
import { humanizeKey } from "@/lib/label-case";
import type { InferenceSearch } from "@/lib/schemas";
import { DeployedModelsListStatusEnum } from "@/openapi";

/** Agentless sentinel — the datasets list and the backend filter use this value too. */
export const NO_CAPABILITY = "__none__";

const STATUS_OPTIONS = Object.values(DeployedModelsListStatusEnum);

export interface InferenceToolbarProps {
  projectId?: string;
  /** A capability id, `NO_CAPABILITY`, or `"all"`. */
  capabilityFilter: string;
  onCapabilityFilterChange: (value: string) => void;
  statusFilter?: InferenceSearch["status"];
  onStatusFilterChange: (value: InferenceSearch["status"]) => void;
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
  projectId,
  capabilityFilter,
  onCapabilityFilterChange,
  statusFilter,
  onStatusFilterChange,
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
          <Select
            onValueChange={(v) =>
              onStatusFilterChange(v === "all" ? undefined : (v as DeployedModelsListStatusEnum))
            }
            value={statusFilter ?? "all"}
          >
            <SelectTrigger aria-label="Filter by status" className="w-[150px]" size="default">
              <SelectValue placeholder="Status" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">All statuses</SelectItem>
              {STATUS_OPTIONS.map((status) => (
                <SelectItem key={status} value={status}>
                  {humanizeKey(status)}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
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
          placeholder="Search models…"
          value={search}
        />
      }
      shareable
    />
  );
}
