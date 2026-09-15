import { Button } from "@/components/ui/button";
import { Icon } from "@/components/ui/icons";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { cn, paginationFromPageLimit } from "@/lib/utils";

interface TablePaginationProps {
  className?: string;
  /** `xs` is the notebook cell's 32px bar; default is the 40px table footer. */
  size?: "default" | "xs";
  page: number;
  pageSize: number;
  count: number;
  onPageChange: (page: number) => void;
  onPageSizeChange: (size: number) => void;
}

export function TablePagination({
  className,
  size = "default",
  count,
  page,
  pageSize,
  onPageChange,
  onPageSizeChange,
}: TablePaginationProps) {
  const pagination = paginationFromPageLimit({ count, page, pageSize });

  return (
    <nav
      aria-label="Table pagination"
      // The workshop table's footer scale, so every table surface carries the same one.
      className={cn(
        "flex items-center justify-between gap-4 text-xs text-muted-foreground",
        size === "xs" ? "px-2.5 py-1" : "px-4 py-1.5",
        className
      )}
    >
      <div className="flex-1 tabular-nums">
        {pagination.count > 0 ? (
          <>
            {pagination.startItem}–{pagination.endItem} of {pagination.total}{" "}
            {pagination.total === 1 ? "row" : "rows"}
          </>
        ) : (
          "0 rows"
        )}
      </div>
      <div className="flex items-center gap-4 sm:gap-6">
        <div className="flex items-center gap-2">
          <span>Rows per page</span>
          <Select onValueChange={(v) => onPageSizeChange(Number(v))} value={String(pageSize)}>
            {/* w-20, not 70px — three digits plus the chevron need the room. */}
            <SelectTrigger className="w-20" size={size === "xs" ? "xs" : "sm"}>
              <SelectValue placeholder={pageSize} />
            </SelectTrigger>
            <SelectContent side="top">
              {[10, 25, 50, 100].map((size) => (
                <SelectItem key={size} value={String(size)}>
                  {size}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        <div className="tabular-nums">
          Page {page} of {pagination.totalPages}
        </div>
        <div className="flex items-center gap-1">
          <Button
            disabled={!pagination.hasPrevious}
            onClick={() => onPageChange(page - 1)}
            size={size === "xs" ? "icon-xs" : "icon-sm"}
            variant="secondary"
          >
            <span className="sr-only">Go to previous page</span>
            <Icon.chevronLeft />
          </Button>
          <Button
            disabled={!pagination.hasNext}
            onClick={() => onPageChange(page + 1)}
            size={size === "xs" ? "icon-xs" : "icon-sm"}
            variant="secondary"
          >
            <span className="sr-only">Go to next page</span>
            <Icon.chevronRight />
          </Button>
        </div>
      </div>
    </nav>
  );
}
