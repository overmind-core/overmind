import { useMemo, useState } from "react";

import { useQuery } from "@tanstack/react-query";

import apiClient from "@/client";
import { Button } from "@/components/ui/button";
import {
  Command,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
} from "@/components/ui/command";
import { Icon } from "@/components/ui/icons";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { LoadingState } from "@/components/ui/spinner";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { useDebouncedValue } from "@/hooks/use-debounced-value";
import { count } from "@/lib/formatters";

const PAGE_SIZE = 25;

export function TrajectoryRunPicker({
  project,
  capability,
  selected,
  highlighted,
  onSelect,
}: {
  project: string;
  capability: string;
  selected?: string;
  highlighted: boolean;
  onSelect: (id: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const [search, setSearch] = useState("");
  const [page, setPage] = useState(1);
  const [status, setStatus] = useState("all");
  const [range, setRange] = useState("all");
  const query = useDebouncedValue(search.trim(), 250);
  const since = useMemo(
    () => (range === "all" ? undefined : (Date.now() - Number(range) * 86_400_000) * 1e6),
    [range]
  );
  const runs = useQuery({
    enabled: open,
    queryFn: () =>
      apiClient.traces.tracesList({
        capability,
        hasError: status === "all" ? undefined : status === "error",
        ordering: "-start_time_ns",
        page,
        pageSize: PAGE_SIZE,
        project,
        search: query || undefined,
        startTimeNsGte: since,
      }),
    queryKey: ["trajectory-run-search", project, capability, query, page, status, since],
  });
  const choose = (id: string) => {
    onSelect(id);
    setOpen(false);
  };
  const label = selected ? `Run ${selected.slice(0, 8)}` : "Select run";

  return (
    <Popover onOpenChange={setOpen} open={open}>
      <Tooltip>
        <TooltipTrigger asChild>
          <PopoverTrigger asChild>
            <Button
              aria-label={`Select trajectory run: ${label}`}
              className="max-w-48"
              size="sm"
              variant="secondary"
            >
              <span className="truncate">{label}</span>
              <Icon.chevronDown className="size-3 text-muted-foreground" />
            </Button>
          </PopoverTrigger>
        </TooltipTrigger>
        <TooltipContent side="bottom">
          {selected || "Select a run to highlight its route and show its scores"}
        </TooltipContent>
      </Tooltip>
      <PopoverContent
        align="end"
        aria-label="Select trajectory run"
        className="nodrag nopan nowheel w-80 max-w-[calc(100vw-2rem)] p-0"
        onEscapeKeyDown={(event) => event.stopPropagation()}
      >
        <Command label="Search runs" shouldFilter={false}>
          <CommandInput
            aria-label="Search runs"
            onValueChange={(value) => {
              setSearch(value);
              setPage(1);
            }}
            placeholder="Search runs…"
            value={search}
          />
          <div className="flex gap-2 px-3 py-2">
            <Select
              onValueChange={(value) => {
                setStatus(value);
                setPage(1);
              }}
              value={status}
            >
              <SelectTrigger aria-label="Run outcome" className="flex-1" size="sm">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="all">All outcomes</SelectItem>
                <SelectItem value="error">With errors</SelectItem>
                <SelectItem value="no-error">Without errors</SelectItem>
              </SelectContent>
            </Select>
            <Select
              onValueChange={(value) => {
                setRange(value);
                setPage(1);
              }}
              value={range}
            >
              <SelectTrigger aria-label="Run date range" className="flex-1" size="sm">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="all">All time</SelectItem>
                <SelectItem value="1">Past 24 hours</SelectItem>
                <SelectItem value="7">Past 7 days</SelectItem>
                <SelectItem value="30">Past 30 days</SelectItem>
              </SelectContent>
            </Select>
          </div>
          <div
            aria-hidden
            className="flex items-center justify-between px-3 py-1 text-xs text-muted-foreground"
          >
            <span>Run ID</span>
            <span className="pr-5">Time</span>
          </div>
          <CommandList className="max-h-80">
            {runs.isLoading || query !== search.trim() ? (
              <LoadingState label="Finding runs" />
            ) : runs.isError ? (
              <div className="space-y-2 p-4 text-sm" role="alert">
                <p>Couldn't load runs.</p>
                <Button onClick={() => runs.refetch()} size="sm" variant="secondary">
                  Try again
                </Button>
              </div>
            ) : !runs.data?.results.length ? (
              <p className="p-4 text-sm text-muted-foreground" role="status">
                No runs match these filters.
              </p>
            ) : (
              <CommandGroup>
                {runs.data.results.map((run) => (
                  <CommandItem
                    aria-label={`Run ${run.traceId}, ${new Date(run.startTimeNs / 1e6).toLocaleString()}. ${selected === run.traceId && highlighted ? "Highlighted; select to stop highlighting" : "Select to highlight"}`}
                    className="gap-2 py-1.5 text-xs"
                    key={run.traceId}
                    onSelect={() => choose(run.traceId)}
                    value={run.traceId}
                  >
                    <span className="min-w-0 flex-1 truncate font-mono">
                      {run.traceId.slice(0, 12)}
                    </span>
                    <time
                      className="shrink-0 text-muted-foreground"
                      dateTime={new Date(run.startTimeNs / 1e6).toISOString()}
                    >
                      {new Date(run.startTimeNs / 1e6).toLocaleString(undefined, {
                        day: "numeric",
                        hour: "2-digit",
                        minute: "2-digit",
                        month: "short",
                        second: "2-digit",
                      })}
                    </time>
                    <span
                      aria-hidden
                      className="flex size-3.5 shrink-0 items-center justify-center rounded-sm border border-border"
                    >
                      {selected === run.traceId && highlighted && (
                        <Icon.success className="size-3 text-info" />
                      )}
                    </span>
                  </CommandItem>
                ))}
              </CommandGroup>
            )}
          </CommandList>
          <div className="flex items-center justify-between gap-3 bg-wash-subtle px-3 py-2">
            <span aria-live="polite" className="text-xs text-muted-foreground">
              {runs.data
                ? `${count(runs.data.count, "run")} · page ${page}`
                : "Search all capability runs"}
            </span>
            <div className="flex gap-1">
              <Button
                aria-label="Previous runs"
                disabled={page === 1 || runs.isFetching}
                onClick={() => setPage((value) => value - 1)}
                size="icon-sm"
                variant="secondary"
              >
                <Icon.chevronLeft />
              </Button>
              <Button
                aria-label="Next runs"
                disabled={!runs.data?.next || runs.isFetching}
                onClick={() => setPage((value) => value + 1)}
                size="icon-sm"
                variant="secondary"
              >
                <Icon.chevronRight />
              </Button>
            </div>
          </div>
        </Command>
      </PopoverContent>
    </Popover>
  );
}
