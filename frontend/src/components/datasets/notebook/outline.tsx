import { useEffect, useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import { Icon } from "@/components/ui/icons";
import { Input } from "@/components/ui/input";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { cn } from "@/lib/utils";
import type { Cell } from "@/openapi";

/** The rail down the left edge: search and jump, then one mark per cell. */
export function NotebookOutline({
  cells,
  selectedId,
  activeId,
  onSelect,
}: {
  cells: Cell[];
  selectedId: string | null;
  activeId: string | null;
  onSelect: (id: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const searchRef = useRef<HTMLInputElement>(null);
  const q = query.trim().toLowerCase();
  const visible = cells.filter(
    (c) => !q || c.title.toLowerCase().includes(q) || (c.version ?? "").includes(q)
  );

  const close = () => {
    setOpen(false);
    setQuery("");
  };
  const jump = (id: string) => {
    onSelect(id);
    close();
  };
  useEffect(() => {
    if (open) searchRef.current?.focus();
  }, [open]);

  return (
    <nav
      aria-label="Notebook outline"
      className="pointer-events-none absolute inset-y-0 left-0 z-10 flex w-10 flex-col items-center pt-3"
    >
      <div className="pointer-events-auto flex flex-col items-center">
        <Popover onOpenChange={(o) => (o ? setOpen(true) : close())} open={open}>
          <PopoverTrigger asChild>
            <Button
              aria-expanded={open}
              aria-label="Search cells"
              size="icon-sm"
              type="button"
              variant="ghost"
            >
              <Icon.search />
            </Button>
          </PopoverTrigger>
          <PopoverContent align="start" className="w-72 p-2" side="right" sideOffset={8}>
            <Input
              aria-label="Jump to or search"
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Jump to or search…"
              ref={searchRef}
              size="sm"
              type="search"
              value={query}
            />
            <ul className="mt-2 max-h-80 overflow-y-auto">
              {visible.map((cell) => (
                <li key={cell.id}>
                  <button
                    aria-current={cell.id === selectedId ? "true" : undefined}
                    className={cn(
                      "flex w-full items-center gap-2 rounded-sm px-2 py-1.5 text-left transition-colors duration-150",
                      cell.id === selectedId ? "bg-primary/10" : "hover:bg-wash-subtle"
                    )}
                    onClick={() => jump(cell.id)}
                    type="button"
                  >
                    <span
                      className={cn(
                        "w-8 shrink-0 font-mono text-xs tabular-nums",
                        cell.id === activeId ? "text-success" : "text-muted-foreground"
                      )}
                    >
                      {cell.version}
                    </span>
                    <span className="min-w-0 flex-1 truncate text-sm text-foreground">
                      {cell.title}
                    </span>
                    <span className="font-mono text-xs tabular-nums text-muted-foreground">
                      {cell.state === "ok" ? cell.rows.toLocaleString() : cell.state}
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          </PopoverContent>
        </Popover>
        <div aria-hidden className="mt-1 mb-2 w-6 border-border/70 border-b" />
        <ul className="flex flex-col items-center gap-2">
          {cells.map((cell) => (
            <li key={cell.id}>
              <button
                aria-current={cell.id === selectedId ? "true" : undefined}
                aria-label={`Jump to ${cell.title}`}
                className={cn(
                  "block h-3 w-6 rounded-sm border bg-transparent outline-none focus-visible:ring-2 focus-visible:ring-ring/60",
                  cell.id === selectedId ? "border-foreground" : "border-border",
                  cell.id === activeId && "bg-success/20",
                  cell.state === "failed" && "border-destructive",
                  cell.state === "running" && "border-info"
                )}
                onClick={() => jump(cell.id)}
                title={`${cell.version} ${cell.title}`}
                type="button"
              />
            </li>
          ))}
        </ul>
      </div>
    </nav>
  );
}
