import type { Table } from "@tanstack/react-table";
import { Search } from "pixelarticons/react";

import { type FilterEntry, TracesFilters } from "@/components/traces/filters";
import type { SpanRow } from "@/hooks/use-traces";
import { DataTableViewOptions } from "@/components/traces/table-column-toggle";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Button } from "@/components/ui/button";
import { Link, useSearch } from "@tanstack/react-router";

interface TracesTableToolbarProps<TData> {
  table: Table<TData>;
  searchValue: string;
  onSearchChange: (value: string) => void;
  timeRange: string;
  onTimeRangeChange: (value: string) => void;
  pageSize: number;
  onPageSizeChange: (value: number) => void;
  status: string;
  onStatusChange: (value: string) => void;
  filters: FilterEntry[];
  onFiltersChange: (filters: FilterEntry[]) => void;
  projectId: string;
}

export function TracesTableToolbar<TData extends SpanRow>({
  table,
  searchValue,
  onSearchChange,
  timeRange,
  onTimeRangeChange,
  status,
  onStatusChange,
  filters,
  onFiltersChange,
  projectId,
}: TracesTableToolbarProps<TData>) {
  const { flatten } = useSearch({ from: "/_auth/projects/$projectId/traces" });
  return (
    <div className="flex flex-wrap items-center gap-2">
      <TracesFilters filters={filters} onFiltersChange={onFiltersChange} projectId={projectId} />
      <div className="relative flex-1 min-w-[200px]">
        <Search className="absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
        <Input
          className="pl-9 h-9"
          onChange={(e) => onSearchChange(e.target.value)}
          placeholder="Search by name or trace ID..."
          value={searchValue}
        />
      </div>
      <Button variant={flatten ? "secondary" : "outline"} asChild>
        <Link search={(prev) => ({ ...prev, flatten: !prev.flatten })} to="." resetScroll={false}>
          Flat Spans
        </Link>
      </Button>
      <Select onValueChange={onStatusChange} value={status}>
        <SelectTrigger className="h-9 w-[130px]">
          <SelectValue placeholder="Status" />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value="all">All status</SelectItem>
          <SelectItem value="success">Success</SelectItem>
          <SelectItem value="error">Error</SelectItem>
        </SelectContent>
      </Select>
      <Select onValueChange={onTimeRangeChange} value={timeRange}>
        <SelectTrigger className="h-9 w-[160px]">
          <SelectValue placeholder="Time range" />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value="all">All time</SelectItem>
          <SelectItem value="past24h">Last 24h</SelectItem>
          <SelectItem value="past7d">Last 7 days</SelectItem>
          <SelectItem value="past30d">Last 30 days</SelectItem>
        </SelectContent>
      </Select>
      <DataTableViewOptions table={table} />
    </div>
  );
}
