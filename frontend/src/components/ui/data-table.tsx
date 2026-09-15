/** table-fixed with a pinned pixel width per column, so a resize moves only that
 *  column's right edge. */

import {
  type CSSProperties,
  Fragment,
  type ReactNode,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";

import {
  type ColumnDef,
  type ColumnSizingState,
  flexRender,
  getCoreRowModel,
  type OnChangeFn,
  type RowSelectionState,
  type Table as TanstackTable,
  useReactTable,
  type VisibilityState,
} from "@tanstack/react-table";

import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { EmptyState } from "@/components/ui/empty-state";
import { Icon } from "@/components/ui/icons";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { TablePagination } from "@/components/ui/table-pagination";
import { cycleDrfOrdering, parseDrfOrdering } from "@/lib/drf-ordering";
import { errorMessage } from "@/lib/notify";
import { cn } from "@/lib/utils";

/** Structurally matches every generated `Paginated*List` model. */
interface DrfPage<T> {
  count: number;
  results: T[];
}

/** Floor for resizable columns — TanStack's default (20) is too small for labels. */
const DATA_TABLE_MIN_COL_PX = 80;

/** Rows a PageUp/PageDown jump moves. Fixed, not viewport-measured, so the jump
 *  means the same thing on every screen. */
export const DATA_TABLE_PAGE_JUMP_ROWS = 10;

/** Ceiling as a multiple of each column's designed `size` (content-fit width). */
const DATA_TABLE_MAX_COL_RATIO = 1.5;

function columnIdOf<T>(c: ColumnDef<T, unknown>): string {
  return String(c.id ?? (c as { accessorKey?: string }).accessorKey ?? "");
}

/** Caps resizable cols so a drag can't grow them forever. Non-resizable cols
 *  (checkbox/actions) pin to their designed size — no 80px floor. */
function capColumnDefs<T>(cols: ColumnDef<T, unknown>[]): ColumnDef<T, unknown>[] {
  return cols.map((c) => {
    if (c.enableResizing === false) {
      const size = c.size ?? 0;
      return { ...c, maxSize: c.maxSize ?? size, minSize: c.minSize ?? size };
    }
    return {
      ...c,
      maxSize: c.maxSize ?? Math.round((c.size ?? 150) * DATA_TABLE_MAX_COL_RATIO),
      minSize: c.minSize ?? DATA_TABLE_MIN_COL_PX,
    };
  });
}

/** Clamp persisted/dragged widths into each column's min/max. Rebuilt from `cols`
 *  rather than spread from `sizing`, so ids for removed or renamed columns drop out
 *  instead of accumulating in localStorage. */
function clampColumnSizing<T>(
  sizing: ColumnSizingState,
  cols: ColumnDef<T, unknown>[]
): ColumnSizingState {
  const out: ColumnSizingState = {};
  for (const c of cols) {
    const id = columnIdOf(c);
    if (!id || sizing[id] == null) continue;
    const min = c.minSize ?? (c.enableResizing === false ? (c.size ?? 0) : DATA_TABLE_MIN_COL_PX);
    const max =
      c.maxSize ??
      (c.enableResizing === false
        ? (c.size ?? 0)
        : Math.round((c.size ?? 150) * DATA_TABLE_MAX_COL_RATIO));
    out[id] = Math.min(max, Math.max(min, sizing[id]));
  }
  return out;
}

/** Spends leftover container width by scaling the flexible columns by one factor, so
 *  the designed proportions hold. Scale floors at 1: an overflowing or not-yet-measured
 *  table keeps its designed sizes and scrolls. */
export function fillColumnWidths(
  columns: { id: string; size: number; canResize: boolean }[],
  available: number
): Record<string, number> {
  const fixed = columns.reduce((sum, c) => (c.canResize ? sum : sum + c.size), 0);
  const flex = columns.reduce((sum, c) => (c.canResize ? sum + c.size : sum), 0);
  const scale = flex > 0 ? Math.max(1, (available - fixed) / flex) : 1;
  const out: Record<string, number> = {};
  for (const c of columns) out[c.id] = c.canResize ? Math.floor(c.size * scale) : c.size;
  return out;
}

/** Callback ref rather than `useRef` + `useEffect`: the scroll container mounts only
 *  once data has loaded, so a mount-keyed effect finds no node and never observes. */
function useMeasuredWidth<T extends HTMLElement>(): [(node: T | null) => void, number] {
  const [width, setWidth] = useState(0);
  const observerRef = useRef<ResizeObserver | null>(null);
  const measuredRef = useCallback((node: T | null) => {
    observerRef.current?.disconnect();
    if (!node) return;
    setWidth(node.clientWidth);
    const observer = new ResizeObserver(([entry]) => setWidth(entry.contentRect.width));
    observer.observe(node);
    observerRef.current = observer;
  }, []);
  return [measuredRef, width];
}

type DataTableColumnMeta = {
  /** DRF OrderingFilter field name (e.g. `"created_at"`). Enables sort UI. */
  orderingField?: string;
  noTruncate?: boolean;
  label?: string;
};

export interface DataTableProps<T> {
  columns: ColumnDef<T, unknown>[];
  data: DrfPage<T> | undefined;
  isLoading?: boolean;
  page: number;
  pageSize: number;
  onPageChange: (page: number) => void;
  onPageSizeChange: (size: number) => void;
  /** DRF `?ordering=` string, e.g. `"-created_at"`. */
  ordering?: string;
  onOrderingChange?: (ordering: string | undefined) => void;
  /** Persist column widths in localStorage under this key. */
  storageKey?: string;
  onRowClick?: (row: T) => void;
  onRowMouseEnter?: (row: T) => void;
  getRowId?: (row: T, index: number) => string;
  emptyState?: ReactNode;
  /** Load failure for `data`. Renders an error surface instead of `emptyState`, so a
   *  failed fetch never reads as "you have no data". */
  error?: unknown;
  toolbar?: ReactNode | ((table: TanstackTable<T>) => ReactNode);
  banner?: ReactNode;
  className?: string;
  hidePagination?: boolean;
  rowClassName?: (row: T) => string | undefined;
  /** Row(s) backing an open detail panel: active tint + `aria-current`. */
  isRowActive?: (row: T) => boolean;
  /** Blank/null keys are ungrouped (no header). Consecutive equal keys share one header. */
  getGroupKey?: (row: T) => string | null;
  renderGroupHeader?: (key: string, count: number) => ReactNode;
  onGroupHeaderClick?: (key: string) => void;
  /** Replaces `emptyState` with the shared no-results state, which carries a way out. */
  hasActiveFilters?: boolean;
  onClearFilters?: () => void;
  enableRowSelection?: boolean;
  rowSelection?: RowSelectionState;
  onRowSelectionChange?: OnChangeFn<RowSelectionState>;
  columnVisibility?: VisibilityState;
  onColumnVisibilityChange?: OnChangeFn<VisibilityState>;
}

declare module "@tanstack/react-table" {
  interface ColumnMeta<TData, TValue> extends DataTableColumnMeta {
    /** Phantom refs so TData/TValue stay part of the merge signature. */
    readonly __dataTableTypes?: { data: TData; value: TValue };
  }
}

function columnWidthStyle(size: number): CSSProperties {
  return { maxWidth: size, minWidth: size, width: size };
}

export function DataTable<T>({
  columns,
  data,
  isLoading,
  page,
  pageSize,
  onPageChange,
  onPageSizeChange,
  ordering,
  onOrderingChange,
  storageKey,
  onRowClick,
  onRowMouseEnter,
  getRowId,
  emptyState,
  error,
  toolbar,
  banner,
  className,
  hidePagination,
  rowClassName,
  isRowActive,
  getGroupKey,
  renderGroupHeader,
  onGroupHeaderClick,
  hasActiveFilters,
  onClearFilters,
  enableRowSelection,
  rowSelection,
  onRowSelectionChange,
  columnVisibility,
  onColumnVisibilityChange,
}: DataTableProps<T>) {
  const rows = data?.results ?? [];
  const count = data?.count ?? 0;
  const sort = parseDrfOrdering(ordering);
  // `data === undefined`, not `!isLoading`: a disabled or not-yet-started query reports
  // `isLoading` false while holding nothing, and the empty state would render over rows
  // still in flight. Callers synthesizing `data` must pass a truthful `isLoading`.
  const unsettled = isLoading || (data === undefined && error == null);
  const showError = !unsettled && error != null && rows.length === 0;
  const showEmpty = !unsettled && !showError && rows.length === 0;

  const cappedColumns = useMemo(() => capColumnDefs(columns), [columns]);

  const [columnSizing, setColumnSizing] = useState<ColumnSizingState>({});
  useEffect(() => {
    if (!storageKey) return;
    try {
      const raw = localStorage.getItem(storageKey);
      setColumnSizing(
        raw ? clampColumnSizing(JSON.parse(raw) as ColumnSizingState, cappedColumns) : {}
      );
    } catch {
      setColumnSizing({});
    }
  }, [storageKey, cappedColumns]);
  useEffect(() => {
    if (!storageKey) return;
    try {
      localStorage.setItem(storageKey, JSON.stringify(columnSizing));
    } catch {
      // quota / disabled storage
    }
  }, [storageKey, columnSizing]);

  const table = useReactTable({
    columnResizeMode: "onChange",
    columns: cappedColumns,
    data: rows,
    enableColumnResizing: true,
    enableRowSelection: !!enableRowSelection,
    getCoreRowModel: getCoreRowModel(),
    getRowId: getRowId as ((row: T, index: number) => string) | undefined,
    onColumnSizingChange: (updater) => {
      setColumnSizing((prev) => {
        const next = typeof updater === "function" ? updater(prev) : updater;
        return clampColumnSizing(next, cappedColumns);
      });
    },
    // TanStack treats an explicit `undefined` updater as "none" and overwrites
    // its internal setter, so an un-wired View menu no-ops.
    ...(onColumnVisibilityChange ? { onColumnVisibilityChange } : {}),
    ...(onRowSelectionChange ? { onRowSelectionChange } : {}),
    state: {
      columnSizing,
      ...(columnVisibility ? { columnVisibility } : {}),
      ...(rowSelection ? { rowSelection } : {}),
    },
  });

  const showPagination = !hidePagination && (count > 0 || page > 1);

  // Roving tabindex (WAI-ARIA grid pattern): exactly one row is tabbable, so Tab enters
  // at the last-focused row and leaves instead of walking hundreds of rows.
  const bodyRef = useRef<HTMLTableSectionElement>(null);
  const [focusedIndex, setFocusedIndex] = useState(0);
  // Cleared only on a blur that names a target outside the body, so a blur caused by the
  // focused row unmounting still counts as "ours".
  const hadFocusRef = useRef(false);
  // Clamped at render, not just in the effect below: a shrinking page must never leave a
  // frame in which no row is tabbable.
  const rovingIndex = rows.length > 0 ? Math.min(focusedIndex, rows.length - 1) : 0;

  const focusRowAt = useCallback((index: number) => {
    setFocusedIndex(index);
    bodyRef.current?.querySelector<HTMLTableRowElement>(`tr[data-row-index="${index}"]`)?.focus();
  }, []);

  // When the focused row unmounts (paging, filtering, sorting) focus falls to <body> and
  // strands a keyboard user, so pull it to whichever row took that position. Guarded on
  // <body> holding focus, so it never steals focus from a control the user moved to.
  // Keyed on the row array, not its length: page 2 replaces every node at the same count.
  useEffect(() => {
    if (!onRowClick || rows.length === 0) return;
    setFocusedIndex(rovingIndex);
    const body = bodyRef.current;
    const active = document.activeElement;
    if (!hadFocusRef.current || !body || (active && active !== document.body)) return;
    body.querySelector<HTMLTableRowElement>(`tr[data-row-index="${rovingIndex}"]`)?.focus();
  }, [rows, rovingIndex, onRowClick]);

  function rowIndexForKey(key: string, from: number): number | null {
    const last = rows.length - 1;
    switch (key) {
      case "ArrowDown":
        return Math.min(from + 1, last);
      case "ArrowUp":
        return Math.max(from - 1, 0);
      case "Home":
        return 0;
      case "End":
        return last;
      case "PageDown":
        return Math.min(from + DATA_TABLE_PAGE_JUMP_ROWS, last);
      case "PageUp":
        return Math.max(from - DATA_TABLE_PAGE_JUMP_ROWS, 0);
      default:
        return null;
    }
  }

  const [scrollRef, viewportWidth] = useMeasuredWidth<HTMLDivElement>();
  // Recomputed per render rather than memoized: a few dozen arithmetic ops whose inputs
  // (sizes, drags, viewport) a dependency list would have to re-derive anyway.
  const widths = fillColumnWidths(
    (table.getHeaderGroups().at(-1)?.headers ?? []).map((h) => ({
      canResize: h.column.getCanResize(),
      id: h.column.id,
      size: h.getSize(),
    })),
    viewportWidth
  );
  const widthOf = (id: string, fallback: number) => widths[id] ?? fallback;
  const totalWidth = Object.values(widths).reduce((sum, w) => sum + w, 0) || table.getTotalSize();

  const toolbarNode = typeof toolbar === "function" ? toolbar(table) : toolbar;

  const emptyNode = hasActiveFilters ? (
    <EmptyState
      action={
        onClearFilters ? (
          <Button onClick={onClearFilters} size="sm" variant="secondary">
            <Icon.close />
            Clear filters
          </Button>
        ) : undefined
      }
      className="flex-1"
      description="No results match the current filters."
      icon={Icon.search}
      size="section"
      title="No results"
    />
  ) : (
    (emptyState ?? (
      <EmptyState className="flex-1" icon={Icon.search} size="section" title="No results" />
    ))
  );

  return (
    <div className={cn("flex min-h-0 flex-1 flex-col gap-3", className)}>
      {toolbarNode ? (
        <div className="flex shrink-0 flex-wrap items-center gap-2">{toolbarNode}</div>
      ) : null}
      {banner}

      {/* All four states carry the same border, so the outline doesn't appear (and shift
          the content under it) when a page arrives. */}
      {unsettled ? (
        // Bars sized per column (designed `size`, pre-resize), so the skeleton doesn't
        // reflow once real column widths land.
        <div className="min-h-0 flex-1 space-y-2 overflow-y-auto rounded-md border border-border p-2">
          {Array.from({ length: Math.min(pageSize, 8) }).map((_, i) => (
            <div className="flex items-center gap-2 px-2 py-1.5" key={i}>
              {cappedColumns.map((col, ci) => (
                <Skeleton
                  className="h-4 shrink-0"
                  key={columnIdOf(col) || ci}
                  style={{ width: col.size ?? DATA_TABLE_MIN_COL_PX }}
                />
              ))}
            </div>
          ))}
        </div>
      ) : showError ? (
        <div className="min-h-0 flex-1 overflow-auto rounded-md border border-border p-4">
          <Alert variant="destructive">{errorMessage(error, "Couldn't load this list.")}</Alert>
        </div>
      ) : showEmpty ? (
        <div className="flex min-h-0 flex-1 flex-col overflow-hidden rounded-md border border-border">
          {emptyNode}
        </div>
      ) : (
        <div className="flex min-h-0 flex-1 flex-col overflow-hidden rounded-md border border-border">
          <Table
            className="table-fixed"
            containerClassName="min-h-0 flex-1"
            containerRef={scrollRef}
            // `grid` makes `aria-selected` on a row legal and promises arrow-key
            // navigation (roving tabindex above). Read-only tables stay plain tables.
            role={onRowClick ? "grid" : undefined}
            style={{ minWidth: "100%", width: totalWidth }}
          >
            <TableHeader>
              {table.getHeaderGroups().map((hg) => (
                <TableRow key={hg.id}>
                  {hg.headers.map((header) => {
                    const meta = header.column.columnDef.meta;
                    const field = meta?.orderingField;
                    const canSort = !!field && !!onOrderingChange;
                    const active = canSort && sort?.field === field;
                    const size = widthOf(header.column.id, header.getSize());
                    // The chevron is the only visual sort cue; screen readers get the
                    // state from `aria-sort` and the column name from this label.
                    const label =
                      meta?.label ??
                      (typeof header.column.columnDef.header === "string"
                        ? header.column.columnDef.header
                        : undefined);
                    return (
                      <TableHead
                        aria-sort={
                          canSort
                            ? active
                              ? sort?.dir === "asc"
                                ? "ascending"
                                : "descending"
                              : "none"
                            : undefined
                        }
                        className="relative overflow-hidden whitespace-nowrap text-left"
                        key={header.id}
                        scope="col"
                        style={columnWidthStyle(size)}
                      >
                        {header.isPlaceholder ? null : canSort ? (
                          <button
                            aria-label={label ? `Sort by ${label}` : "Sort by this column"}
                            className="group -mx-1 flex w-full max-w-full items-center justify-start gap-1 rounded-sm px-1 text-left align-middle transition-colors hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                            onClick={() => onOrderingChange(cycleDrfOrdering(ordering, field))}
                            type="button"
                          >
                            <span className="truncate">
                              {flexRender(header.column.columnDef.header, header.getContext())}
                            </span>
                            {active ? (
                              sort?.dir === "asc" ? (
                                <Icon.chevronUp className="size-4 shrink-0 text-foreground" />
                              ) : (
                                <Icon.chevronDown className="size-4 shrink-0 text-foreground" />
                              )
                            ) : (
                              <Icon.chevronDown className="size-4 shrink-0 opacity-0 transition-opacity group-hover:opacity-40 group-focus-visible:opacity-40" />
                            )}
                          </button>
                        ) : (
                          <span className="flex w-full max-w-full justify-start truncate text-left">
                            {flexRender(header.column.columnDef.header, header.getContext())}
                          </span>
                        )}
                        {header.column.getCanResize() && (
                          <span
                            className={cn(
                              "absolute top-0 right-0 z-10 h-full w-1 cursor-col-resize touch-none select-none hover:bg-foreground/20",
                              header.column.getIsResizing() && "bg-foreground/40"
                            )}
                            onDoubleClick={() => header.column.resetSize()}
                            onMouseDown={header.getResizeHandler()}
                            onTouchStart={header.getResizeHandler()}
                          />
                        )}
                      </TableHead>
                    );
                  })}
                  {/* Absorbs leftover viewport width so real cols stay left-packed. */}
                  <TableHead aria-hidden className="p-0" />
                </TableRow>
              ))}
            </TableHeader>
            <TableBody ref={bodyRef}>
              {table.getRowModel().rows.map((row, rowIndex) => {
                const active = isRowActive?.(row.original) ?? false;
                const modelRows = table.getRowModel().rows;
                const groupKey = getGroupKey?.(row.original) ?? null;
                const prevGroupKey =
                  rowIndex > 0 ? (getGroupKey?.(modelRows[rowIndex - 1].original) ?? null) : null;
                const showGroupHeader = !!groupKey && groupKey !== prevGroupKey;
                let groupCount = 0;
                if (showGroupHeader && groupKey) {
                  for (let j = rowIndex; j < modelRows.length; j++) {
                    if ((getGroupKey?.(modelRows[j].original) ?? null) !== groupKey) break;
                    groupCount++;
                  }
                }
                const groupHeader =
                  showGroupHeader && groupKey ? (
                    <TableRow className="bg-wash-subtle hover:bg-wash-subtle">
                      <TableCell
                        className="bg-wash-subtle py-1.5 text-xs text-muted-foreground"
                        colSpan={row.getVisibleCells().length + 1}
                      >
                        {onGroupHeaderClick ? (
                          <button
                            className="flex w-full items-center text-left hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring/50"
                            onClick={() => onGroupHeaderClick(groupKey)}
                            type="button"
                          >
                            {renderGroupHeader?.(groupKey, groupCount) ?? (
                              <span>
                                {groupKey} · {groupCount}
                              </span>
                            )}
                          </button>
                        ) : (
                          (renderGroupHeader?.(groupKey, groupCount) ?? (
                            <span>
                              {groupKey} · {groupCount}
                            </span>
                          ))
                        )}
                      </TableCell>
                    </TableRow>
                  ) : null;
                const mainRow = (
                  <TableRow
                    aria-current={active ? "true" : undefined}
                    aria-selected={enableRowSelection ? row.getIsSelected() : undefined}
                    className={cn(
                      onRowClick &&
                        "cursor-pointer focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring/50",
                      active && "bg-primary/20 hover:bg-primary/20",
                      rowClassName?.(row.original)
                    )}
                    data-row-index={onRowClick ? rowIndex : undefined}
                    key={row.id}
                    onBlur={
                      onRowClick
                        ? (e) => {
                            const to = e.relatedTarget as Node | null;
                            if (to && !bodyRef.current?.contains(to)) hadFocusRef.current = false;
                          }
                        : undefined
                    }
                    onClick={onRowClick ? () => onRowClick(row.original) : undefined}
                    onFocus={
                      onRowClick
                        ? (e) => {
                            hadFocusRef.current = true;
                            if (e.target === e.currentTarget) setFocusedIndex(rowIndex);
                          }
                        : undefined
                    }
                    // Only when focus is on the row itself, so Space on a checkbox or
                    // arrows inside a cell's input keep their meaning.
                    onKeyDown={
                      onRowClick
                        ? (e) => {
                            if (e.target !== e.currentTarget) return;
                            if (e.key === "Enter" || e.key === " ") {
                              e.preventDefault();
                              onRowClick(row.original);
                              return;
                            }
                            const next = rowIndexForKey(e.key, rowIndex);
                            // Other keys pass through without preventDefault, so the
                            // page keeps its shortcuts.
                            if (next === null) return;
                            e.preventDefault();
                            focusRowAt(next);
                          }
                        : undefined
                    }
                    onMouseEnter={onRowMouseEnter ? () => onRowMouseEnter(row.original) : undefined}
                    role="row"
                    tabIndex={onRowClick ? (rowIndex === rovingIndex ? 0 : -1) : undefined}
                  >
                    {row.getVisibleCells().map((cell) => {
                      const meta = cell.column.columnDef.meta;
                      const size = widthOf(cell.column.id, cell.column.getSize());
                      return (
                        <TableCell
                          className={cn(
                            "overflow-hidden text-left",
                            !meta?.noTruncate && "truncate whitespace-nowrap"
                          )}
                          key={cell.id}
                          style={columnWidthStyle(size)}
                        >
                          {flexRender(cell.column.columnDef.cell, cell.getContext())}
                        </TableCell>
                      );
                    })}
                    <TableCell aria-hidden className="p-0" />
                  </TableRow>
                );
                if (!groupHeader) return mainRow;
                return (
                  <Fragment key={row.id}>
                    {groupHeader}
                    {mainRow}
                  </Fragment>
                );
              })}
            </TableBody>
          </Table>
          {showPagination && (
            <div className="shrink-0 border-border/70 border-t">
              <TablePagination
                count={count}
                onPageChange={onPageChange}
                onPageSizeChange={onPageSizeChange}
                page={page}
                pageSize={pageSize}
              />
            </div>
          )}
        </div>
      )}
    </div>
  );
}
