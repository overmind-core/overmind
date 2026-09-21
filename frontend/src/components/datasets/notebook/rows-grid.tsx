import { useCallback, useEffect, useMemo, useState } from "react";

import { diffText } from "@/components/datasets/notebook/diff";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { HoverCard, HoverCardContent, HoverCardTrigger } from "@/components/ui/hover-card";
import { Icon } from "@/components/ui/icons";
import { Input } from "@/components/ui/input";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { SearchInput } from "@/components/ui/search-input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { TablePagination } from "@/components/ui/table-pagination";
import {
  isVisibleDatasetColumn,
  type RowFilter,
  useColumnsQuery,
  useRowsQuery,
} from "@/hooks/use-datasets";
import { useDebouncedValue } from "@/hooks/use-debounced-value";
import { errorMessage } from "@/lib/notify";
import { cn } from "@/lib/utils";
import type { ColumnStat } from "@/openapi";

const FILTER_OPS: Array<{ value: RowFilter["op"]; label: string; needsValue: boolean }> = [
  { label: "contains", needsValue: true, value: "contains" },
  { label: "does not contain", needsValue: true, value: "not_contains" },
  { label: "equals", needsValue: true, value: "equals" },
  { label: "does not equal", needsValue: true, value: "not_equals" },
  { label: "is empty", needsValue: false, value: "empty" },
  { label: "is not empty", needsValue: false, value: "not_empty" },
];

type Row = Record<string, unknown> & { _index: number };

function fullText(value: unknown): string {
  if (value === null || value === undefined) return "";
  if (typeof value === "string") return value;
  if (typeof value === "object") {
    try {
      return JSON.stringify(value, null, 2);
    } catch {
      return String(value);
    }
  }
  return String(value);
}

function ChangedValue({
  before,
  after,
  wrap,
}: {
  before: unknown;
  after: unknown;
  wrap?: boolean;
}) {
  const ops = diffText(cellText(before), cellText(after));
  return (
    <span className={wrap ? "whitespace-pre-wrap break-words" : "whitespace-pre"}>
      {ops.map((op, i) =>
        op.kind === "same" ? (
          op.text
        ) : (
          <span
            className={cn(
              "rounded-xs",
              op.kind === "del"
                ? "bg-destructive/15 text-destructive line-through"
                : "bg-success/20 text-success"
            )}
            key={`${i}-${op.kind}`}
          >
            {op.text}
          </span>
        )
      )}
    </span>
  );
}

function TypeMark({ type }: { type: string }) {
  if (type === "number" || type === "integer") {
    return <span className="w-3 text-center font-mono text-muted-foreground">#</span>;
  }
  if (type === "datetime")
    return <Icon.history aria-hidden className="size-3 text-muted-foreground" />;
  if (type === "json")
    return <span className="w-3 text-center font-mono text-muted-foreground">{"{}"}</span>;
  if (type === "boolean")
    return <span className="w-3 text-center font-mono text-muted-foreground">?</span>;
  return <span className="w-3 text-center font-mono text-muted-foreground">A</span>;
}

function cellText(value: unknown): string {
  if (value === null || value === undefined) return "";
  if (typeof value === "string") return value;
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  try {
    return JSON.stringify(value);
  } catch {
    return String(value);
  }
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex flex-col gap-0.5 rounded-sm border border-border/70 bg-wash-raised px-2 py-1.5">
      <span className="pixel-label text-xs text-muted-foreground">{label}</span>
      <span className="truncate font-mono text-sm tabular-nums text-foreground" title={value}>
        {value}
      </span>
    </div>
  );
}

/** One column's profile: fill, distinct values, range or lengths, and the
 *  values that occur most, each with its share of the rows. */
function ColumnStatsCard({ column, total }: { column: ColumnStat; total: number }) {
  const filled = Math.round((1 - column.nullRate) * 100);
  const numeric = column.mean !== undefined && column.mean !== null;
  const stats: Array<{ label: string; value: string }> = [
    { label: "Distinct", value: column.distinct.toLocaleString() },
  ];
  if (numeric) {
    stats.push(
      { label: "Min", value: cellText(column.min) },
      { label: "Mean", value: Number(column.mean).toLocaleString() },
      { label: "Max", value: cellText(column.max) }
    );
  } else if (column.meanLen !== undefined && column.meanLen !== null) {
    stats.push(
      { label: "Mean length", value: Math.round(column.meanLen).toLocaleString() },
      { label: "Max length", value: (column.maxLen ?? 0).toLocaleString() }
    );
  }
  const top = (column.top ?? []).slice(0, 5) as Array<{ value: unknown; count: number }>;
  const topMax = Math.max(1, ...top.map((t) => t.count));
  return (
    <HoverCard openDelay={250}>
      <HoverCardTrigger asChild>
        <button
          aria-label={`Profile of ${column.name}`}
          className="rounded-sm p-0.5 text-muted-foreground/60 hover:bg-muted hover:text-foreground"
          type="button"
        >
          <Icon.chartBar className="size-3" />
        </button>
      </HoverCardTrigger>
      <HoverCardContent align="start" className="w-72 p-2.5">
        <div className="flex items-center gap-1.5">
          <TypeMark type={column.type} />
          <span className="font-mono text-sm text-foreground">{column.name}</span>
          <span className="pixel-label ml-auto text-xs text-muted-foreground">{column.type}</span>
        </div>
        <div className="mt-2.5 flex flex-col gap-1">
          <div className="flex items-center justify-between text-xs">
            <span className="pixel-label text-muted-foreground">Filled</span>
            <span className="font-mono tabular-nums text-foreground">
              {filled}% of {total.toLocaleString()}
            </span>
          </div>
          <div className="h-1.5 w-full overflow-hidden rounded-xs bg-muted">
            <div
              className={cn("h-full", filled < 50 ? "bg-warning" : "bg-foreground/60")}
              style={{ width: `${filled}%` }}
            />
          </div>
        </div>
        <div
          className={cn(
            "mt-2.5 grid gap-1.5",
            stats.length === 4 ? "grid-cols-2" : stats.length === 3 ? "grid-cols-3" : "grid-cols-1"
          )}
        >
          {stats.map((stat) => (
            <Stat key={stat.label} label={stat.label} value={stat.value} />
          ))}
        </div>
        {top.length > 0 && (
          <div className="mt-2.5 flex flex-col gap-1">
            <span className="pixel-label text-xs text-muted-foreground">Most common</span>
            {top.map((item, i) => {
              const text = cellText(item.value);
              return (
                <div className="flex flex-col gap-0.5" key={`${i}-${text}`}>
                  <div className="flex items-center justify-between gap-2 text-xs">
                    <span
                      className={cn("truncate font-mono", !text && "italic text-muted-foreground")}
                    >
                      {text || "null"}
                    </span>
                    <span className="shrink-0 font-mono tabular-nums text-muted-foreground">
                      {item.count.toLocaleString()}
                    </span>
                  </div>
                  <div className="h-1 w-full overflow-hidden rounded-xs bg-muted">
                    <div
                      className="h-full bg-info/70"
                      style={{ width: `${(item.count / topMax) * 100}%` }}
                    />
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </HoverCardContent>
    </HoverCard>
  );
}

function FilterAdd({ columns, onAdd }: { columns: string[]; onAdd: (f: RowFilter) => void }) {
  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState<RowFilter>({
    field: columns[0] ?? "",
    op: "contains",
    value: "",
  });
  useEffect(() => {
    if (!draft.field && columns[0]) setDraft((d) => ({ ...d, field: columns[0] }));
  }, [columns, draft.field]);
  const needsValue = FILTER_OPS.find((o) => o.value === draft.op)?.needsValue ?? true;
  const add = () => {
    if (!draft.field) return;
    if (needsValue && !(draft.value ?? "").trim()) return;
    onAdd({ ...draft, value: needsValue ? draft.value : undefined });
    setDraft({ ...draft, value: "" });
    setOpen(false);
  };
  return (
    <Popover onOpenChange={setOpen} open={open}>
      <PopoverTrigger asChild>
        <Button size="xs" variant="outline">
          <Icon.filter />
          Filter
        </Button>
      </PopoverTrigger>
      <PopoverContent align="start" className="w-80 p-3">
        <div className="grid grid-cols-[auto_1fr] items-center gap-x-3 gap-y-2 text-xs">
          <span className="text-muted-foreground">Column</span>
          <Select onValueChange={(v) => setDraft({ ...draft, field: v })} value={draft.field}>
            <SelectTrigger aria-label="Column" className="w-full" size="sm">
              <SelectValue placeholder="Column" />
            </SelectTrigger>
            <SelectContent>
              {columns.map((c) => (
                <SelectItem key={c} value={c}>
                  {c}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <span className="text-muted-foreground">Condition</span>
          <Select
            onValueChange={(v) => setDraft({ ...draft, op: v as RowFilter["op"] })}
            value={draft.op}
          >
            <SelectTrigger aria-label="Condition" className="w-full" size="sm">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {FILTER_OPS.map((o) => (
                <SelectItem key={o.value} value={o.value}>
                  {o.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          {needsValue && (
            <>
              <span className="text-muted-foreground">Value</span>
              <Input
                aria-label="Value"
                autoFocus
                onChange={(e) => setDraft({ ...draft, value: e.target.value })}
                onKeyDown={(e) => {
                  if (e.key === "Enter") add();
                }}
                size="sm"
                value={draft.value ?? ""}
              />
            </>
          )}
        </div>
        <div className="mt-3 flex justify-end">
          <Button onClick={add} size="sm">
            Add
          </Button>
        </div>
      </PopoverContent>
    </Popover>
  );
}

export interface RowsGridProps {
  datasetId: string;
  /** The cell whose frame is shown. */
  cellId: string;
  /** Mark rows added and values changed against the cell before. */
  diff?: boolean;
  pageSize?: number;
  className?: string;
  enabled?: boolean;
}

/** Paged grid over one cell's frame, with server-side sort, search, filters,
 *  and git-style marks: new rows tinted, changed values tinted with the old
 *  value on hover. */
export function RowsGrid({
  datasetId,
  cellId,
  diff = false,
  pageSize = 10,
  className,
  enabled = true,
}: RowsGridProps) {
  const [page, setPage] = useState(1);
  const [limit, setLimit] = useState(pageSize);
  const [sort, setSort] = useState<{ field: string; dir: "asc" | "desc" } | null>(null);
  const [search, setSearch] = useState("");
  const [filters, setFilters] = useState<RowFilter[]>([]);
  const [openRow, setOpenRow] = useState<number | null>(null);
  const debouncedSearch = useDebouncedValue(search, 300);

  // biome-ignore lint/correctness/useExhaustiveDependencies: back to the first page on any narrowing
  useEffect(() => {
    setPage(1);
  }, [debouncedSearch, filters, sort]);

  const rowsQuery = useRowsQuery(
    datasetId,
    cellId,
    {
      diff,
      dir: sort?.dir,
      filters,
      limit,
      offset: (page - 1) * limit,
      search: debouncedSearch,
      sort: sort?.field,
    },
    enabled
  );
  const columnsQuery = useColumnsQuery(datasetId, cellId, enabled);
  const statsByName = useMemo(() => {
    const map = new Map<string, ColumnStat>();
    for (const c of columnsQuery.data ?? []) map.set(c.name, c);
    return map;
  }, [columnsQuery.data]);

  const data = rowsQuery.data;
  // biome-ignore lint/correctness/useExhaustiveDependencies: a new page has new rows
  useEffect(() => setOpenRow(null), [data]);
  const columns = useMemo(
    () =>
      (data?.columns ?? [])
        .filter(isVisibleDatasetColumn)
        .map((c) => ({ name: c.name, type: c.type })),
    [data?.columns]
  );
  const rows = (data?.rows ?? []) as Row[];
  const marks = data?.marks ?? {};

  const toggleSort = useCallback(
    (field: string) =>
      setSort((prev) =>
        prev?.field !== field
          ? { dir: "asc", field }
          : prev.dir === "asc"
            ? { dir: "desc", field }
            : null
      ),
    []
  );

  if (!enabled) return null;
  const narrowing = !!debouncedSearch || filters.length > 0;

  return (
    <div className={cn("flex min-h-0 flex-col", className)}>
      <div className="flex flex-wrap items-center gap-1.5 px-2.5 py-1">
        <SearchInput
          className="w-44"
          label="Search rows"
          onChange={(e) => setSearch(e.target.value)}
          onClear={() => setSearch("")}
          placeholder="Search"
          size="xs"
          value={search}
        />
        <FilterAdd
          columns={columns.map((c) => c.name)}
          onAdd={(f) => setFilters([...filters, f])}
        />
        {filters.map((f, i) => (
          <Badge
            className="gap-1 rounded-sm border-border font-mono"
            key={`${f.field}-${f.op}-${i}`}
            size="chip"
            variant="outline"
          >
            {f.field} {FILTER_OPS.find((o) => o.value === f.op)?.label}
            {f.value !== undefined ? ` "${f.value}"` : ""}
            <button
              aria-label="Remove filter"
              onClick={() => setFilters(filters.filter((_, j) => j !== i))}
              type="button"
            >
              <Icon.close className="size-3" />
            </button>
          </Badge>
        ))}
        {sort && (
          <Badge className="gap-1 rounded-sm border-border font-mono" size="chip" variant="outline">
            <Icon.sort className="size-3" />
            {sort.field} {sort.dir}
            <button aria-label="Clear sort" onClick={() => setSort(null)} type="button">
              <Icon.close className="size-3" />
            </button>
          </Badge>
        )}
        <span className="flex-1" />
        {data && narrowing && (
          <span className="text-xs tabular-nums text-muted-foreground">
            {data.total.toLocaleString()} match
          </span>
        )}
      </div>

      <div className="min-h-0 flex-1 overflow-auto border-t border-border/70">
        {rowsQuery.isPending ? (
          <div className="flex flex-col gap-1.5 p-2">
            <Skeleton className="h-5 w-full" />
            <Skeleton className="h-5 w-full" />
            <Skeleton className="h-5 w-2/3" />
          </div>
        ) : rowsQuery.isError ? (
          <p className="p-2 text-xs text-muted-foreground">{errorMessage(rowsQuery.error)}</p>
        ) : rows.length === 0 ? (
          <p className="p-2 text-xs text-muted-foreground">
            {narrowing ? "No rows match." : "No rows."}
          </p>
        ) : (
          <table className="w-max min-w-full border-separate border-spacing-0 text-xs">
            <thead className="sticky top-0 z-10 bg-wash-raised">
              <tr>
                <th className="w-9 border-r border-b border-border/70 px-2 py-1.5 text-left font-normal text-muted-foreground">
                  <span className="sr-only">Row</span>
                </th>
                {columns.map((c) => {
                  const stat = statsByName.get(c.name);
                  const active = sort?.field === c.name;
                  return (
                    <th
                      className="max-w-[26rem] border-r border-b border-border/70 px-2 py-1.5 text-left font-normal last:border-r-0"
                      key={c.name}
                      scope="col"
                    >
                      <span className="flex items-center gap-1">
                        <TypeMark type={c.type} />
                        <button
                          className={cn(
                            "flex items-center gap-1 text-foreground hover:underline",
                            active && "font-semibold"
                          )}
                          onClick={() => toggleSort(c.name)}
                          type="button"
                        >
                          {c.name}
                          {active ? (
                            sort?.dir === "asc" ? (
                              <Icon.arrowUp className="size-3" />
                            ) : (
                              <Icon.arrowDown className="size-3" />
                            )
                          ) : null}
                        </button>
                        {stat && <ColumnStatsCard column={stat} total={data?.total ?? 0} />}
                      </span>
                    </th>
                  );
                })}
              </tr>
            </thead>
            <tbody>
              {rows.map((row, rowIndex) => {
                const sourceRow = row.source_row;
                const mark =
                  sourceRow === null || sourceRow === undefined
                    ? undefined
                    : marks[String(sourceRow)];
                const open = openRow === rowIndex;
                return (
                  <tr
                    aria-expanded={open}
                    className={cn(
                      "cursor-pointer hover:bg-accent/40",
                      mark?.added && "bg-success/10",
                      open && "bg-accent/40"
                    )}
                    key={row._index}
                    onClick={() => setOpenRow(open ? null : rowIndex)}
                  >
                    <td className="border-r border-b border-border/60 px-2 py-1 font-mono tabular-nums text-muted-foreground">
                      {row._index}
                    </td>
                    {columns.map((c) => {
                      const value = row[c.name];
                      const text = open ? fullText(value) : cellText(value);
                      const before = mark?.before && c.name in mark.before ? mark.before : null;
                      return (
                        <td
                          className={cn(
                            "max-w-[26rem] border-r border-b border-border/60 px-2 py-1 align-top last:border-r-0",
                            // Open: the same cells, wrapped to their full text.
                            open ? "whitespace-pre-wrap break-words" : "truncate",
                            c.type === "number" || c.type === "integer"
                              ? "text-right tabular-nums"
                              : "",
                            text === "" && !before && "italic text-muted-foreground"
                          )}
                          key={c.name}
                          title={!open && text.length > 80 ? text.slice(0, 400) : undefined}
                        >
                          {before ? (
                            <ChangedValue after={value} before={before[c.name]} wrap={open} />
                          ) : text === "" ? (
                            "null"
                          ) : (
                            text
                          )}
                        </td>
                      );
                    })}
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </div>

      {data && data.total > limit && (
        <div className="border-t border-border/60">
          <TablePagination
            count={data.total}
            onPageChange={setPage}
            onPageSizeChange={(s) => {
              setLimit(s);
              setPage(1);
            }}
            page={page}
            pageSize={limit}
            size="xs"
          />
        </div>
      )}
    </div>
  );
}
