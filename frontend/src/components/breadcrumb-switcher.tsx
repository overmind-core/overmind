import { useMemo, useState } from "react";

import { useNavigate } from "@tanstack/react-router";

import { Icon } from "@/components/ui/icons";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip";
import {
  type EntitySibling,
  SWITCHABLE_KINDS,
  useEntitySiblings,
} from "@/hooks/use-entity-siblings";
import { selectProject } from "@/hooks/use-project-search-sync";
import { cn } from "@/lib/utils";

const KIND_NOUN: Record<string, string> = {
  capabilities: "capability",
  datasets: "dataset",
  projects: "project",
};

export function BreadcrumbSwitcher({
  kind,
  currentSlug,
  projectId,
  label,
}: {
  kind: string;
  currentSlug: string;
  projectId: string | undefined;
  label: string;
}) {
  const navigate = useNavigate();
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const { data: siblings } = useEntitySiblings(
    SWITCHABLE_KINDS.has(kind) ? kind : undefined,
    projectId
  );

  const filtered = useMemo<EntitySibling[]>(() => {
    const q = query.trim().toLowerCase();
    const list = siblings ?? [];
    return q ? list.filter((s) => s.label.toLowerCase().includes(q)) : list;
  }, [siblings, query]);

  // Must match the crumb slots in the `_auth` Breadcrumb: h-8 plus the same
  // NeueBit optical nudge, so the leaf shares the row's midline.
  const plain = (
    <span className="inline-flex h-8 min-w-0 items-center leading-none -translate-y-px font-bold text-foreground">
      {label}
    </span>
  );
  if (!SWITCHABLE_KINDS.has(kind) || !siblings || siblings.length <= 1) return plain;

  const go = (id: string) => {
    setOpen(false);
    if (id === currentSlug) return;
    switch (kind) {
      case "capabilities":
        navigate({
          params: { capabilityId: id },
          search: (p) => p,
          to: "/capabilities/$capabilityId",
        });
        break;
      case "projects":
        selectProject(id);
        navigate({
          params: { projectId: id },
          search: (p) => ({ ...p, projectId: id }),
          to: "/projects/$projectId",
        });
        break;
      case "datasets":
        navigate({ params: { datasetId: id }, search: (p) => p, to: "/datasets/$datasetId" });
        break;
    }
  };

  const showSearch = siblings.length > 7;
  const switchLabel = `Switch ${KIND_NOUN[kind] ?? "item"}`;

  return (
    <Popover
      onOpenChange={(o) => {
        setOpen(o);
        if (!o) setQuery("");
      }}
      open={open}
    >
      <TooltipProvider delayDuration={150}>
        <Tooltip>
          <TooltipTrigger asChild>
            <PopoverTrigger asChild>
              <button
                aria-label={switchLabel}
                className="group/bc inline-flex h-8 max-w-full min-w-0 items-center gap-1 p-0 leading-none font-bold text-foreground transition-colors hover:text-foreground/70 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/60"
                type="button"
              >
                {/* NeueBit ~1px low; ∨ pixelarticon is top-heavy — half-pixel down */}
                <span className="truncate leading-none -translate-y-px">{label}</span>
                <Icon.chevronDown className="block size-3.5 shrink-0 translate-y-[0.5px] text-muted-foreground transition-transform duration-150 group-data-[state=open]/bc:rotate-180" />
              </button>
            </PopoverTrigger>
          </TooltipTrigger>
          <TooltipContent side="bottom" sideOffset={6}>
            {switchLabel}
          </TooltipContent>
        </Tooltip>
      </TooltipProvider>
      <PopoverContent
        align="start"
        className="w-[min(24rem,calc(100vw-2rem))] overflow-hidden p-0 font-sans"
        sideOffset={6}
      >
        {showSearch && (
          <div className="flex items-center gap-2 border-b border-border/70 px-2.5">
            <Icon.search className="size-4 shrink-0 text-muted-foreground" />
            {/* No autoFocus: Radix already focuses this, the first focusable child. */}
            <input
              className="h-9 w-full bg-transparent text-sm text-foreground outline-none placeholder:text-muted-foreground"
              onChange={(e) => setQuery(e.target.value)}
              placeholder={`Search ${KIND_NOUN[kind] ?? "item"}s…`}
              value={query}
            />
          </div>
        )}
        <div className="max-h-72 overflow-y-auto p-1">
          {filtered.length === 0 ? (
            <p className="px-2 py-6 text-center text-sm text-muted-foreground">No matches</p>
          ) : (
            filtered.map((s) => {
              const isCurrent = s.id === currentSlug;
              return (
                <button
                  className={cn(
                    "flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-sm text-foreground transition-colors hover:bg-accent focus-visible:bg-accent focus-visible:outline-none",
                    isCurrent && "bg-accent/60"
                  )}
                  key={s.id}
                  onClick={() => go(s.id)}
                  type="button"
                >
                  <Icon.success
                    className={cn(
                      "size-4 shrink-0 text-primary",
                      isCurrent ? "opacity-100" : "opacity-0"
                    )}
                  />
                  <span className={cn("min-w-0 flex-1 truncate", isCurrent && "font-medium")}>
                    {s.label}
                  </span>
                </button>
              );
            })
          )}
        </div>
      </PopoverContent>
    </Popover>
  );
}
