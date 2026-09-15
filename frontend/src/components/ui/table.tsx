import type * as React from "react";

import { cn } from "@/lib/utils";

function Table({
  className,
  containerClassName,
  containerRef,
  ...props
}: React.ComponentProps<"table"> & {
  containerClassName?: string;
  containerRef?: React.Ref<HTMLDivElement>;
}) {
  return (
    // The table's only scrollport, and a sticky header sticks to it, never to an
    // ancestor: a caller wanting `sticky top-0` on TableHeader must bound this element's
    // height through containerClassName (e.g. `min-h-0 flex-1`) rather than wrap it in a
    // second scrolling div. min-w-max keeps the table as wide as its nowrap cells.
    <div
      className={cn("relative w-full overflow-auto", containerClassName)}
      data-slot="table-container"
      ref={containerRef}
    >
      <table
        className={cn("w-full min-w-max caption-bottom text-sm", className)}
        data-slot="table"
        {...props}
      />
    </div>
  );
}

function TableHeader({ className, ...props }: React.ComponentProps<"thead">) {
  return (
    <thead
      // Bottom rule on the cells, not the row: with border-collapse a row border
      // under a sticky thead drops out while scrolling.
      className={cn("sticky top-0 z-10 bg-card [&_th]:border-b", className)}
      data-slot="table-header"
      {...props}
    />
  );
}

function TableBody({ className, ...props }: React.ComponentProps<"tbody">) {
  return (
    <tbody
      className={cn("[&_tr:last-child]:border-0", className)}
      data-slot="table-body"
      {...props}
    />
  );
}

function TableRow({ className, ...props }: React.ComponentProps<"tr">) {
  return (
    <tr
      className={cn(
        "hover:bg-wash-raised data-[state=selected]:bg-muted border-b transition-colors",
        className
      )}
      data-slot="table-row"
      {...props}
    />
  );
}

function TableHead({ className, ...props }: React.ComponentProps<"th">) {
  return (
    <th
      className={cn(
        // No translate-y on checkboxes — upstream's optical nudge pulls a corner
        // select-all off-square. h-8/py-1 matches the Traces table header density (the
        // app-wide reference). whitespace-nowrap keeps header text as the column
        // min-width floor, so table-fixed layouts scroll instead of collapsing.
        "pixel-label text-xs font-bold text-muted-foreground h-8 overflow-hidden whitespace-nowrap bg-card px-2 py-1 text-left align-middle [&:has([role=checkbox])]:pr-0",
        className
      )}
      data-slot="table-head"
      {...props}
    />
  );
}

function TableCell({ className, ...props }: React.ComponentProps<"td">) {
  return (
    <td
      // px-2/py-1.5 matches the Traces table row density (the app-wide reference look).
      className={cn(
        "whitespace-nowrap px-2 py-1.5 align-middle [&:has([role=checkbox])]:pr-0",
        className
      )}
      data-slot="table-cell"
      {...props}
    />
  );
}

export { Table, TableBody, TableCell, TableHead, TableHeader, TableRow };
