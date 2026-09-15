import { useCallback, useEffect, useMemo, useState } from "react";

import { useQuery } from "@tanstack/react-query";

import apiClient from "@/client";
import {
  resolveTimeRange,
  TimeRangeButton,
  type TimeRangePreset,
} from "@/components/traces/traces-table-toolbar";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Spinner } from "@/components/ui/spinner";
import { TooltipProvider } from "@/components/ui/tooltip";
import type { TraceSelectionSpec } from "@/hooks/use-datasets";
import { useTracesList } from "@/hooks/use-traces";

type StatusFilter = "all" | "ok" | "error";

/** What the filters select right now. `ready` with `count: 0` is a valid, empty answer. */
export type TraceSelectionState =
  | { status: "counting" }
  | { status: "error" }
  | { status: "incomplete"; hint: string }
  | { status: "ready"; selection: TraceSelectionSpec; count: number };

interface Props {
  projectId: string;
  onChange: (state: TraceSelectionState) => void;
}

export function TraceBulkSourcePicker({ projectId, onChange }: Props) {
  const [preset, setPreset] = useState<TimeRangePreset>("past24h");
  const [customTimeRange, setCustomTimeRange] = useState<{ gte?: string; lte?: string }>({});
  const [capabilityId, setCapabilityId] = useState<string>("all");
  const [status, setStatus] = useState<StatusFilter>("all");

  const capabilitiesQuery = useQuery({
    enabled: !!projectId,
    queryFn: () => apiClient.capabilities.capabilitiesList({ pageSize: 100, project: projectId }),
    queryKey: ["capabilities", projectId, "bulk-trace-picker"],
  });
  const capabilities = capabilitiesQuery.data?.results ?? [];

  const resolvedRange = useMemo(
    () => resolveTimeRange(preset, preset === "custom" ? customTimeRange : undefined),
    [preset, customTimeRange]
  );
  const rangeIncomplete = preset === "custom" && !resolvedRange.gte && !resolvedRange.lte;

  const filters = useMemo(() => {
    const next: Record<string, string | undefined> = {
      // In the filters, not just the list request's own argument: these travel
      // on as the selection, which the server resolves against every project
      // the user belongs to.
      project: projectId,
      received_at__gte: resolvedRange.gte,
      received_at__lte: resolvedRange.lte,
    };
    if (capabilityId !== "all") next.capability = capabilityId;
    // Same mapping as the traces page toolbar (URL error__isnull → API has_error).
    if (status === "ok") next.has_error = "false";
    if (status === "error") next.has_error = "true";
    return next;
  }, [projectId, resolvedRange, capabilityId, status]);

  const previewQuery = useTracesList({
    enabled: !!projectId && !rangeIncomplete,
    filters,
    ordering: "-start_time_ns",
    page: 1,
    pageSize: 1,
    project_id: projectId,
  });
  const matchCount = previewQuery.data?.count ?? 0;

  useEffect(() => {
    if (rangeIncomplete) {
      onChange({ hint: "Pick a from or to time for the custom range", status: "incomplete" });
    } else if (previewQuery.isFetching) {
      onChange({ status: "counting" });
    } else if (previewQuery.isError) {
      onChange({ status: "error" });
    } else if (previewQuery.data) {
      onChange({
        count: matchCount,
        selection: { filters, ordering: "-start_time_ns" },
        status: "ready",
      });
    }
  }, [
    filters,
    matchCount,
    onChange,
    previewQuery.data,
    previewQuery.isError,
    previewQuery.isFetching,
    rangeIncomplete,
  ]);

  const handleTimeRangeChange = useCallback(
    (next: TimeRangePreset, custom?: { gte?: string; lte?: string }) => {
      setPreset(next);
      setCustomTimeRange(next === "custom" ? (custom ?? {}) : {});
    },
    []
  );

  return (
    <div className="w-full space-y-3">
      <TooltipProvider>
        <div className="space-y-1.5">
          <Label className="text-xs text-muted-foreground">Time range</Label>
          <TimeRangeButton
            className="w-full justify-start"
            customTimeRange={customTimeRange}
            onChange={handleTimeRangeChange}
            value={preset}
          />
        </div>
      </TooltipProvider>

      <div className="flex w-full flex-col gap-3 sm:flex-row">
        <div className="min-w-0 flex-1 space-y-1.5">
          <Label className="text-xs text-muted-foreground">Capability</Label>
          <Select onValueChange={setCapabilityId} value={capabilityId}>
            <SelectTrigger className="w-full text-xs" size="lg">
              <SelectValue placeholder="All capabilities" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">All capabilities</SelectItem>
              {capabilities.map((a) => (
                <SelectItem key={a.id} value={a.id}>
                  {a.name || a.id.slice(0, 8)}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        <div className="min-w-0 flex-1 space-y-1.5">
          <Label className="text-xs text-muted-foreground">Status</Label>
          <Select onValueChange={(v) => setStatus(v as StatusFilter)} value={status}>
            <SelectTrigger className="w-full text-xs" size="lg">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">All</SelectItem>
              <SelectItem value="ok">OK</SelectItem>
              <SelectItem value="error">Error</SelectItem>
            </SelectContent>
          </Select>
        </div>
      </div>

      <div className="rounded-md border border-border/60 bg-background px-3 py-2.5 text-xs">
        {rangeIncomplete ? (
          <span className="text-muted-foreground">Pick a from or to time</span>
        ) : previewQuery.isFetching ? (
          <span className="inline-flex items-center gap-1.5 text-muted-foreground">
            <Spinner className="size-3" /> Counting matching traces
          </span>
        ) : previewQuery.isError ? (
          <span className="text-destructive">Couldn't count matching traces</span>
        ) : (
          <span>
            <span className="font-semibold tabular-nums">{matchCount.toLocaleString()}</span>{" "}
            matching {matchCount === 1 ? "trace" : "traces"}
          </span>
        )}
      </div>
    </div>
  );
}
