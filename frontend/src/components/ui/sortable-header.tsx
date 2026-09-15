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
  className,
  label,
  onSort,
  sort,
  sortKey,
}: {
  centered?: boolean;
  className?: string;
  label: string;
  onSort: (key: K) => void;
  sort: SortState<K>;
  sortKey: K;
}) {
  const active = sort.key === sortKey;
  return (
    <TableHead
      aria-sort={active ? (sort.dir === "asc" ? "ascending" : "descending") : "none"}
      className={cn(centered && "text-center", className)}
      scope="col"
    >
      {/* The chevron is the only visual sort cue; `aria-sort` above carries the state,
          this carries the column name. */}
      <button
        aria-label={`Sort by ${label}`}
        className="group -mx-1 inline-flex items-center gap-1 rounded-sm px-1 align-middle transition-colors hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        onClick={() => onSort(sortKey)}
        type="button"
      >
        {centered && <span aria-hidden className="size-4 shrink-0" />}
        <span>{label}</span>
        {active ? (
          sort.dir === "asc" ? (
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
