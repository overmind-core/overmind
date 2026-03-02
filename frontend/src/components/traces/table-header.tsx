import type { Column } from "@tanstack/react-table";
import { ArrowDown, ArrowUp, ChevronsVertical as ChevronsUpDown } from "pixelarticons/react";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

interface DataTableColumnHeaderProps<TData, TValue> extends React.HTMLAttributes<HTMLDivElement> {
  column: Column<TData, TValue>;
  title: string;
}

export function DataTableColumnHeader<TData, TValue>({
  column,
  title,
  className,
}: DataTableColumnHeaderProps<TData, TValue>) {
  if (!column.getCanSort()) {
    return <div className={cn(className)}>{title}</div>;
  }

  const sorted = column.getIsSorted();

  const handleClick = () => {
    // Toggle between asc and desc (default to asc if unsorted)
    column.toggleSorting(sorted === "asc");
  };

  return (
    <div className={cn("flex items-center", className)}>
      <Button className="-ml-3 h-8 gap-1" onClick={handleClick} size="sm" variant="ghost">
        <span>{title}</span>
        {sorted === "desc" ? (
          <ArrowDown className="size-3.5" />
        ) : sorted === "asc" ? (
          <ArrowUp className="size-3.5" />
        ) : (
          <ChevronsUpDown className="size-3.5 text-muted-foreground" />
        )}
      </Button>
    </div>
  );
}
