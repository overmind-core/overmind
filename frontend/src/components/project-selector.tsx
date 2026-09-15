import { useMemo, useRef, useState } from "react";

import { useNavigate, useRouterState, useSearch } from "@tanstack/react-router";

import { Button } from "@/components/ui/button";
import { Icon } from "@/components/ui/icons";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip";
import { useGuestGate } from "@/hooks/use-guest-gate";
import { selectProject } from "@/hooks/use-project-search-sync";
import { useProjectsList } from "@/hooks/use-projects";
import { cn } from "@/lib/utils";

export function ProjectSelector() {
  const { projectId = "" } = useSearch({ from: "/_auth" });
  const guard = useGuestGate();
  const navigate = useNavigate();
  const pathname = useRouterState({ select: (s) => s.location.pathname });
  const { data } = useProjectsList();
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const searchRef = useRef<HTMLInputElement>(null);

  const projects = data?.projects ?? [];
  const hasProjects = projects.length > 0;
  const current = projects.find((p) => p.projectId === projectId);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    return q ? projects.filter((p) => p.name.toLowerCase().includes(q)) : projects;
  }, [projects, query]);

  const pick = (id: string) => {
    setOpen(false);
    selectProject(id);
    // Nested paths and search params both belong to the outgoing project, so
    // collapse to the section root and replace search wholesale.
    const segments = pathname.split("/").filter(Boolean);
    const to = (segments.length > 1 ? `/${segments[0]}` : pathname || "/") as "/";
    void navigate({ search: { projectId: id }, to });
  };

  const openConfig = (id: string) => {
    setOpen(false);
    selectProject(id);
    void navigate({
      params: { projectId: id },
      search: (prev) => ({ ...prev, projectId: id }),
      to: "/projects/$projectId",
    });
  };

  const showSearch = projects.length > 7;

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
              <Button
                aria-label="Switch project"
                className="max-w-56 justify-between gap-1.5 font-sans"
                disabled={!hasProjects}
                variant="secondary"
              >
                <span className="min-w-0 truncate">
                  {current?.name ?? (hasProjects ? "Select project" : "No projects")}
                </span>
                <Icon.chevronDown className="size-3.5 shrink-0 text-muted-foreground" />
              </Button>
            </PopoverTrigger>
          </TooltipTrigger>
          <TooltipContent side="bottom" sideOffset={6}>
            Switch project
          </TooltipContent>
        </Tooltip>
      </TooltipProvider>
      <PopoverContent
        align="end"
        className="w-80 overflow-hidden p-0 font-sans"
        // Radix would focus the first tabbable element — the "View all projects"
        // button — so typing would press a button instead of filtering.
        onOpenAutoFocus={(e) => {
          if (searchRef.current) {
            e.preventDefault();
            searchRef.current.focus();
          }
        }}
        sideOffset={6}
      >
        <div className="flex items-center justify-between border-b border-border/70 py-1.5 pl-3 pr-1.5">
          <p className="text-sm font-medium text-foreground">Projects</p>
          <Button
            className="gap-0.5 text-muted-foreground hover:text-foreground"
            onClick={guard(() => {
              setOpen(false);
              void navigate({ to: "/projects" });
            })}
            size="xs"
            variant="secondary"
          >
            View all projects
            <Icon.chevronRight className="size-3" />
          </Button>
        </div>

        {showSearch && (
          <div className="flex items-center gap-2 border-b border-border/70 px-2.5">
            <Icon.search className="size-4 shrink-0 text-muted-foreground" />
            <input
              className="h-9 w-full bg-transparent text-sm text-foreground outline-none placeholder:text-muted-foreground"
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Search projects…"
              ref={searchRef}
              value={query}
            />
          </div>
        )}

        <div className="max-h-80 overflow-y-auto p-1 pb-0">
          {filtered.length === 0 ? (
            <p className="px-2 py-6 text-center text-sm text-muted-foreground">No matches</p>
          ) : (
            filtered.map((p) => {
              const isCurrent = p.projectId === projectId;
              return (
                <div
                  className={cn(
                    "group flex items-center rounded-md transition-colors hover:bg-accent",
                    isCurrent && "bg-accent/60"
                  )}
                  key={p.projectId}
                >
                  <button
                    className="flex min-w-0 flex-1 items-center gap-2 rounded-md px-2 py-1.5 text-left text-sm text-foreground focus-visible:bg-accent focus-visible:outline-none"
                    onClick={() => pick(p.projectId)}
                    type="button"
                  >
                    <Icon.success
                      className={cn(
                        "size-4 shrink-0 text-primary",
                        isCurrent ? "opacity-100" : "opacity-0"
                      )}
                    />
                    <span className={cn("min-w-0 flex-1 truncate", isCurrent && "font-medium")}>
                      {p.name}
                    </span>
                    {p.memberCount > 1 && (
                      <span className="flex shrink-0 items-center gap-1 text-xs tabular-nums text-muted-foreground">
                        <Icon.user className="size-3.5" />
                        {p.memberCount}
                      </span>
                    )}
                  </button>
                  <Button
                    aria-label={`Open ${p.name} settings`}
                    className="mr-1 shrink-0 text-muted-foreground hover:text-foreground"
                    onClick={() => openConfig(p.projectId)}
                    size="icon-xs"
                    variant="ghost"
                  >
                    <Icon.settings />
                  </Button>
                </div>
              );
            })
          )}
        </div>

        <div className="p-1">
          <Button
            className="w-full justify-center gap-2"
            onClick={guard(() => {
              setOpen(false);
              void navigate({ search: { createProject: "new" }, to: "/projects" });
            })}
            size="sm"
            variant="secondary"
          >
            <Icon.projectAdd className="size-4" />
            New project
          </Button>
        </div>
      </PopoverContent>
    </Popover>
  );
}
