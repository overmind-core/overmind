import type { ReactNode } from "react";

import { Icon } from "@/components/ui/icons";
import { TableHead } from "@/components/ui/table";
import { cn } from "@/lib/utils";

type SortDirection = "asc" | "desc";

export interface SortState<K extends string> {
  key: K;
  dir: SortDirection;
}

/** Render in place of a `<TableHead>` — it renders one internally. `centered` adds a
 *  leading spacer mirroring the sort-icon slot, so the icon doesn't pull the label
 *  off-centre from its column's values. */
export function SortableHeader<K extends string>({
  centered,
  children,
  className,
  label,
  onSort,
  sort,
  sortKey,
}: {
  centered?: boolean;
  children?: ReactNode;
  className?: string;
  label: string;
  onSort: (key: K) => void;
  sort: SortState<K> | null;
  sortKey: K;
}) {
  const active = sort?.key === sortKey;
  return (
    <TableHead
      aria-sort={active ? (sort?.dir === "asc" ? "ascending" : "descending") : "none"}
      className={cn(centered && "text-center", className)}
      scope="col"
    >
      {/* The chevron is the only visual sort cue; `aria-sort` above carries the state,
          this carries the column name. */}
      <button
        aria-label={`Sort by ${label}`}
        className={cn(
          "group flex min-h-6 w-full items-center gap-1 rounded-sm text-left align-middle transition-colors hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
          centered && "justify-center text-center"
        )}
        onClick={() => onSort(sortKey)}
        type="button"
      >
        {centered && <span aria-hidden className="size-4 shrink-0" />}
        <span>{children ?? label}</span>
        {active ? (
          sort?.dir === "asc" ? (
            <Icon.chevronUp className="size-4 text-foreground" />
          ) : (
            <Icon.chevronDown className="size-4 text-foreground" />
          )
        ) : (
          <Icon.chevronDown className="size-4 opacity-0 transition-opacity group-hover:opacity-40 group-focus-visible:opacity-40" />
        )}
      </button>
    </TableHead>
  );
}
