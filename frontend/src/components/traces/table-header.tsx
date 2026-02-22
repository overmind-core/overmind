import type { Column } from "@tanstack/react-table";
import { ArrowDown, ArrowUp, ChevronsUpDown, EyeOff } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip";
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

  return (
    <div className={cn("group flex items-center gap-0.5", className)}>
      <Button
        className="-ml-3 h-8 gap-1"
        onClick={() => column.toggleSorting(column.getIsSorted() === "asc")}
        size="sm"
        variant="ghost"
      >
        <span>{title}</span>
        {column.getIsSorted() === "desc" ? (
          <ArrowDown className="size-3.5" />
        ) : column.getIsSorted() === "asc" ? (
          <ArrowUp className="size-3.5" />
        ) : (
          <ChevronsUpDown className="size-3.5 text-muted-foreground/60" />
        )}
      </Button>
      {column.getCanHide() && (
        <TooltipProvider>
          <Tooltip>
            <TooltipTrigger asChild>
              <Button
                className="size-6 opacity-0 transition-opacity group-hover:opacity-100"
                onClick={() => column.toggleVisibility(false)}
                size="icon"
                variant="ghost"
              >
                <EyeOff className="size-3.5 text-muted-foreground/60" />
              </Button>
            </TooltipTrigger>
            <TooltipContent>Hide column</TooltipContent>
          </Tooltip>
        </TooltipProvider>
      )}
    </div>
  );
}
