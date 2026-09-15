import { useCallback, useEffect, useRef, useState } from "react";

import { useQuery } from "@tanstack/react-query";
import { useNavigate, useSearch } from "@tanstack/react-router";
import { useCommandState } from "cmdk";
import { Dialog as DialogPrimitive } from "radix-ui";

import apiClient from "@/client";
import { navLinks } from "@/components/app-sidebar";
import {
  Dialog,
  DialogDescription,
  DialogOverlay,
  DialogPortal,
  DialogTitle,
} from "@/components/ui/dialog";
import { Icon } from "@/components/ui/icons";
import { useAuthContext } from "@/contexts/auth-context";
import { useProjectsList } from "@/hooks/use-projects";
import { type RecentItem, useRecentItems } from "@/hooks/use-recent-items";
import { isGuestAllowedPath } from "@/lib/guest";
import { isMac } from "@/lib/platform";
import { cn } from "@/lib/utils";
import {
  Command,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
  CommandSeparator,
} from "./ui/command";

type ResourceDef = {
  id: string;
  label: string;
  icon: React.ComponentType<{ className?: string }>;
  to: string;
  keywords?: string[];
};

type ActionDef = {
  id: string;
  label: string;
  icon: React.ComponentType<{ className?: string }>;
  to: string;
  search?: Record<string, string>;
  keywords: string[];
  // Project-scoped entries only make sense once a project is active.
  projectScoped?: boolean;
};

type PaletteRoute = { to: string; search?: Record<string, string> };

// Search synonyms per route, so US and UK spellings both find the page.
const NAV_KEYWORDS: Record<string, string[]> = {
  "/": [
    "home",
    "overmind",
    "capabilities",
    "alias",
    "live model",
    "delete capability",
    "remove capability",
    "archive capability",
    "set live model",
    "change model",
    "switch model",
    "pin model",
    "make live",
  ],
  "/datasets": ["datasets", "data"],
  "/evaluations": [
    "evaluations",
    "eval",
    "evals",
    "eval set",
    "eval sets",
    "sets",
    "rubric",
    "delete evaluator",
    "remove evaluator",
    "share",
  ],
  "/inference": [
    "inference",
    "models",
    "deployed",
    "serving",
    "api snippet",
    "make live",
    "delete model",
    "decommission",
    "undeploy",
    "remove deployment",
    "model ref",
  ],
  "/observability": ["observability", "traces", "tracing", "spans", "logs"],
  "/observability/integrations": [
    "integrations",
    "connectors",
    "langsmith",
    "langfuse",
    "braintrust",
    "galileo",
    "disconnect",
    "delete connector",
    "remove connector",
  ],
  "/optimiser": [
    "optimiser",
    "optimizer",
    "optimize",
    "optimisation",
    "model comparison",
    "delete experiment",
    "remove experiment",
  ],
  "/projects": ["projects", "create project", "new project", "delete project", "remove project"],
  "/settings": [
    "settings",
    "billing",
    "plan",
    "credits",
    "upgrade",
    "top up",
    "ledger",
    "spend",
    "subscription",
    "cancel plan",
    "renew",
    "buy credits",
    "invoice",
    "usage",
  ],
  "/training": [
    "training",
    "finetuning",
    "fine-tune",
    "fine tuning",
    "ft",
    "model pr",
    "wizard",
    "delete job",
    "remove job",
  ],
};

// Navigation-only entries: they take the user to the page where the action can
// be done, rather than performing it. Deliberately excludes capability
// creation, eval-run lifecycle, feedback, MCP management, and row search.
const ACTIONS: ActionDef[] = [
  {
    icon: Icon.datasetAdd,
    id: "action:create-dataset",
    keywords: ["new dataset", "create dataset", "upload dataset", "import traces", "data workshop"],
    label: "New dataset",
    projectScoped: true,
    search: { create: "true" },
    to: "/datasets",
  },
  {
    icon: Icon.projectAdd,
    id: "action:create-project",
    keywords: ["create project", "new project", "add project", "start a project"],
    label: "Create project",
    to: "/projects",
  },
  {
    icon: Icon.delete,
    id: "action:delete-project",
    keywords: ["delete project", "remove project", "destroy project", "delete workspace"],
    label: "Delete project",
    to: "/projects",
  },
  {
    icon: Icon.checkbox,
    id: "action:onboarding",
    keywords: ["onboarding", "setup", "first time", "welcome", "complete setup"],
    label: "Onboarding",
    to: "/onboarding",
  },
  {
    icon: Icon.capabilityOff,
    id: "action:delete-capability",
    keywords: [
      "delete capability",
      "remove capability",
      "archive capability",
      "disable capability",
      "delete alias",
    ],
    label: "Delete capability",
    projectScoped: true,
    to: "/",
  },
  {
    icon: Icon.model,
    id: "action:set-live-model",
    keywords: ["set live model", "change model", "switch model", "pin model", "make live"],
    label: "Set live model",
    projectScoped: true,
    to: "/",
  },
  {
    icon: Icon.delete,
    id: "action:delete-eval-set",
    keywords: [
      "delete eval set",
      "remove eval set",
      "delete evaluator",
      "remove evaluator",
      "delete rubric",
    ],
    label: "Delete eval set",
    projectScoped: true,
    search: { view: "sets" },
    to: "/evaluations",
  },
  {
    icon: Icon.model,
    id: "action:delete-model-ref",
    keywords: ["delete model ref", "remove model ref", "delete model reference"],
    label: "Delete model ref",
    projectScoped: true,
    to: "/inference",
  },
  {
    icon: Icon.delete,
    id: "action:decommission-model",
    keywords: [
      "decommission model",
      "delete deployment",
      "remove deployment",
      "undeploy",
      "stop serving",
      "delete deployed model",
    ],
    label: "Decommission model",
    projectScoped: true,
    to: "/inference",
  },
  {
    icon: Icon.integrations,
    id: "action:disconnect-connector",
    keywords: [
      "disconnect connector",
      "delete connector",
      "remove connector",
      "remove integration",
      "stop sync",
      "remove langfuse",
      "remove langsmith",
      "remove braintrust",
      "remove galileo",
    ],
    label: "Disconnect connector",
    projectScoped: true,
    to: "/observability/integrations",
  },
  {
    icon: Icon.delete,
    id: "action:delete-finetune-job",
    keywords: [
      "delete finetune job",
      "delete training job",
      "remove job",
      "delete fine tune",
      "remove training run",
    ],
    label: "Delete finetune job",
    projectScoped: true,
    to: "/training",
  },
  {
    icon: Icon.optimiser,
    id: "action:delete-experiment",
    keywords: [
      "delete experiment",
      "remove experiment",
      "delete optimizer experiment",
      "delete optimisation",
    ],
    label: "Delete experiment",
    projectScoped: true,
    to: "/optimiser",
  },
];

const RESOURCES: ResourceDef[] = navLinks.map((leaf) => ({
  icon: leaf.icon as ResourceDef["icon"],
  id: `resource:${leaf.to}`,
  keywords: NAV_KEYWORDS[leaf.to] ?? [leaf.label],
  label: leaf.label,
  to: leaf.to,
}));

interface CommandPaletteProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

export function CommandPalette({ open, onOpenChange }: CommandPaletteProps) {
  const navigate = useNavigate();
  const { recents, addRecent, removeRecent } = useRecentItems();
  const { isGuest } = useAuthContext();
  // Every action is a mutation; a guest gets the pages they can open.
  const actions = isGuest ? [] : ACTIONS;
  const resources = isGuest ? RESOURCES.filter((r) => isGuestAllowedPath(r.to)) : RESOURCES;
  const [query, setQuery] = useState("");
  const { projectId } = useSearch({ from: "/_auth" });

  // Search is strictly project-scoped: there is no cross-project fallback.
  const { data: projectsData } = useProjectsList();
  const projects = projectsData?.projects ?? [];
  const activeProjectId = projectId;
  const subtitle = projects.find((p) => p.projectId === activeProjectId)?.name;

  // Shares the capabilities page's query key, so a visited page costs no extra request.
  const { data: capabilitiesData, isLoading: capabilitiesLoading } = useQuery({
    enabled: !!activeProjectId,
    queryFn: () =>
      apiClient.capabilities.capabilitiesList({ ordering: "name", project: activeProjectId! }),
    queryKey: ["capabilities", activeProjectId],
    staleTime: 60_000,
  });
  const capabilities = capabilitiesData?.results ?? [];

  const handleClose = useCallback(() => {
    onOpenChange(false);
    setQuery("");
  }, [onOpenChange]);

  const handleSelect = useCallback(
    (item: RecentItem) => {
      // Stored routes can be stale or corrupted; navigating one would break.
      if (!item.to.startsWith("/") || item.to.startsWith("//")) {
        removeRecent(item.id);
        return;
      }
      addRecent(item);
      navigate({ search: item.search, to: item.to });
      handleClose();
    },
    [addRecent, removeRecent, navigate, handleClose]
  );

  const isQueryEmpty = query.trim() === "";

  const handleSwitchProject = useCallback(
    (nextProjectId: string) => {
      navigate({
        search: ((prev: Record<string, unknown>) => ({
          ...prev,
          projectId: nextProjectId,
        })) as never,
      });
      handleClose();
    },
    [navigate, handleClose]
  );

  const handleRouteSelect = useCallback(
    (route: PaletteRoute) => {
      navigate({
        search: {
          ...(activeProjectId ? { projectId: activeProjectId } : {}),
          ...route.search,
        },
        to: route.to,
      });
      handleClose();
    },
    [navigate, activeProjectId, handleClose]
  );

  return (
    <Dialog
      onOpenChange={(isOpen) => {
        if (!isOpen) handleClose();
      }}
      open={open}
    >
      <DialogPortal>
        {/* Bespoke content, not `DialogContent`: cmdk needs a top-anchored shell
            with no header/footer/close chrome and its own width. */}
        <DialogOverlay />
        <DialogPrimitive.Content
          className={cn(
            "fixed top-[20%] left-1/2 z-50 w-full max-w-[560px] -translate-x-1/2",
            "overflow-hidden rounded-md border border-border bg-popover",
            "data-[state=open]:animate-in data-[state=closed]:animate-out",
            "data-[state=closed]:fade-out-0 data-[state=open]:fade-in-0",
            "data-[state=closed]:zoom-out-95 data-[state=open]:zoom-in-95",
            "duration-150"
          )}
        >
          <DialogTitle className="sr-only">Search</DialogTitle>
          <DialogDescription className="sr-only">
            Search for capabilities, projects, and navigation resources.
          </DialogDescription>

          <Command loop>
            <RemoveListener recents={recents} removeRecent={removeRecent} />

            <CommandInput
              onValueChange={setQuery}
              placeholder={
                activeProjectId
                  ? "Search pages, actions, and capabilities..."
                  : "Select a project to search..."
              }
              value={query}
            />

            <CommandList>
              <CommandEmpty>No results found.</CommandEmpty>

              {!activeProjectId && (
                <div className="px-3 py-2 text-xs text-muted-foreground">
                  Select a project below to search its pages, actions, and capabilities.
                </div>
              )}

              {isQueryEmpty && recents.length > 0 && (
                <CommandGroup heading="Recently viewed">
                  {recents.map((item) => (
                    <CommandItem
                      key={item.id}
                      keywords={[item.label, item.subtitle ?? ""]}
                      onSelect={() => handleSelect(item)}
                      value={`recent:${item.id}`}
                    >
                      <RecentIcon id={item.id} type={item.type} />
                      <span className="flex-1 truncate">{item.label}</span>
                      {item.subtitle && (
                        <span className="ml-2 shrink-0 text-xs text-muted-foreground">
                          {item.subtitle}
                        </span>
                      )}
                      <RemoveHint />
                    </CommandItem>
                  ))}
                </CommandGroup>
              )}

              {isQueryEmpty && recents.length > 0 && <CommandSeparator />}

              <CommandGroup heading="Pages">
                {!isQueryEmpty && activeProjectId && (
                  <CommandItem
                    keywords={[
                      "api key",
                      "api keys",
                      "key",
                      "keys",
                      "revoke key",
                      "delete key",
                      "rotate key",
                      "generate key",
                      "create key",
                      "token",
                    ]}
                    onSelect={() => handleRouteSelect({ to: `/projects/${activeProjectId}` })}
                    value="resource:api-keys"
                  >
                    <Icon.lock className="size-[15px] shrink-0 text-muted-foreground" />
                    <span className="flex-1">API keys</span>
                  </CommandItem>
                )}
                {!isQueryEmpty && activeProjectId && (
                  <CommandItem
                    keywords={[
                      "member",
                      "members",
                      "team",
                      "teammate",
                      "teammates",
                      "invite",
                      "invites",
                      "invitation",
                      "add",
                      "add member",
                      "add person",
                      "add user",
                      "add teammate",
                      "person",
                      "people",
                      "collaborator",
                      "collaborators",
                      "share",
                      "email",
                      "remove member",
                      "delete member",
                      "kick",
                      "manage team",
                      "manage members",
                    ]}
                    onSelect={() =>
                      handleRouteSelect({
                        search: { section: "members" },
                        to: `/projects/${activeProjectId}`,
                      })
                    }
                    value="resource:members"
                  >
                    <Icon.addUser className="size-[15px] shrink-0 text-muted-foreground" />
                    <span className="flex-1">Members & invites</span>
                  </CommandItem>
                )}
                {!isQueryEmpty && (
                  <CommandItem
                    keywords={[
                      "billing",
                      "plan",
                      "credits",
                      "subscription",
                      "top up",
                      "topup",
                      "ledger",
                      "upgrade",
                      "cancel plan",
                      "renew",
                      "buy credits",
                      "spend",
                      "invoice",
                    ]}
                    onSelect={() => handleRouteSelect({ to: "/settings" })}
                    value="resource:billing"
                  >
                    <Icon.credits className="size-[15px] shrink-0 text-muted-foreground" />
                    <span className="flex-1">Plan & billing</span>
                  </CommandItem>
                )}
                {resources.map((r) => {
                  const ResourceIcon = r.icon;
                  const item: RecentItem = {
                    id: r.id,
                    label: r.label,
                    to: r.to,
                    type: "resource",
                  };
                  return (
                    <CommandItem
                      key={r.id}
                      keywords={r.keywords ?? [r.label]}
                      onSelect={() => handleSelect(item)}
                      value={r.id}
                    >
                      <ResourceIcon className="size-[15px] shrink-0 text-muted-foreground" />
                      <span>{r.label}</span>
                    </CommandItem>
                  );
                })}
              </CommandGroup>

              {!isQueryEmpty && actions.some((a) => !a.projectScoped || activeProjectId) && (
                <>
                  <CommandSeparator />
                  <CommandGroup heading="Actions">
                    {actions
                      .filter((a) => !a.projectScoped || activeProjectId)
                      .map((action) => {
                        const ActionIcon = action.icon;
                        return (
                          <CommandItem
                            key={action.id}
                            keywords={action.keywords}
                            onSelect={() =>
                              handleRouteSelect({ search: action.search, to: action.to })
                            }
                            value={action.id}
                          >
                            <ActionIcon className="size-[15px] shrink-0 text-muted-foreground" />
                            <span className="flex-1 truncate">{action.label}</span>
                          </CommandItem>
                        );
                      })}
                  </CommandGroup>
                </>
              )}

              {(capabilitiesLoading || capabilities.length > 0) && (
                <>
                  <CommandSeparator />
                  <CommandGroup heading="Capabilities">
                    {capabilitiesLoading ? (
                      <div className="px-2 py-3 text-center text-xs text-muted-foreground">
                        Loading capabilities…
                      </div>
                    ) : null}
                    {capabilities.map((capability) => {
                      const item: RecentItem = {
                        id: `capability:${capability.id}`,
                        label: capability.name,
                        // Group only renders when a project is active (query is gated on it).
                        search: { projectId: activeProjectId! },
                        subtitle: subtitle,
                        to: `/capabilities/${capability.id}`,
                        type: "capability",
                      };
                      return (
                        <CommandItem
                          key={capability.id}
                          keywords={[capability.name, capability.slug]}
                          onSelect={() => handleSelect(item)}
                          value={`capability:${capability.id}`}
                        >
                          <Icon.capability className="size-[15px] shrink-0 text-muted-foreground" />
                          <span className="flex-1 truncate">{capability.name}</span>
                        </CommandItem>
                      );
                    })}
                  </CommandGroup>
                </>
              )}

              {projects.length > 0 && (
                <>
                  <CommandSeparator />
                  <CommandGroup heading="Switch project">
                    {projects.map((project) => {
                      const isActive = project.projectId === activeProjectId;
                      return (
                        <CommandItem
                          key={project.projectId}
                          keywords={[project.name]}
                          onSelect={() => handleSwitchProject(project.projectId)}
                          value={`project:${project.projectId}`}
                        >
                          <Icon.project className="size-[15px] shrink-0 text-muted-foreground" />
                          <span className="flex-1 truncate">{project.name}</span>
                          {isActive ? (
                            <Icon.success className="ml-auto size-[15px] shrink-0 text-muted-foreground" />
                          ) : null}
                        </CommandItem>
                      );
                    })}
                  </CommandGroup>
                </>
              )}
            </CommandList>

            <div className="flex items-center gap-3 border-t border-border/70 px-3 py-2 text-xs text-muted-foreground">
              <span>
                <Kbd>↑</Kbd>
                <Kbd>↓</Kbd> to navigate
              </span>
              <span>
                <Kbd>↵</Kbd> to select
              </span>
              <RemoveKeyHint />
              <span>
                <Kbd>Esc</Kbd> to close
              </span>
            </div>
          </Command>
        </DialogPrimitive.Content>
      </DialogPortal>
    </Dialog>
  );
}

// Must render inside <Command> so useCommandState can read cmdk's store.
interface RemoveListenerProps {
  recents: RecentItem[];
  removeRecent: (id: string) => void;
}

function RemoveListener({ recents, removeRecent }: RemoveListenerProps) {
  // cmdk types `state.value` as string but returns undefined when no item is
  // active, e.g. a query that matches nothing.
  const selectedValue = useCommandState((state) => state.value) as string | undefined;

  // A ref, so the keydown handler registers once instead of on every selection.
  const stateRef = useRef({ recents, removeRecent, selectedValue });
  useEffect(() => {
    stateRef.current = { recents, removeRecent, selectedValue };
  });

  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key !== "Enter" || !(e.metaKey || e.ctrlKey)) return;
      const { recents, removeRecent, selectedValue } = stateRef.current;
      if (!selectedValue?.startsWith("recent:")) return;
      const match = recents.find((r) => `recent:${r.id}` === selectedValue);
      if (match) {
        e.preventDefault();
        e.stopPropagation();
        removeRecent(match.id);
      }
    };
    document.addEventListener("keydown", handler, { capture: true });
    return () => document.removeEventListener("keydown", handler, { capture: true });
  }, []); // stable — reads latest values via stateRef

  return null;
}

// Renders inside <Command> so it reads cmdk's selected value directly, rather
// than lifting it into parent state.
function RemoveKeyHint() {
  const selectedValue = useCommandState((state) => state.value) as string | undefined;
  if (!selectedValue?.startsWith("recent:")) return null;
  return (
    <span>
      <Kbd>{isMac ? "⌘" : "Ctrl"}</Kbd>
      <Kbd>↵</Kbd> to remove
    </span>
  );
}

function Kbd({ children }: { children: React.ReactNode }) {
  return (
    <kbd className="mr-0.5 inline-flex h-4 min-w-4 items-center justify-center rounded-sm border border-border bg-muted px-0.5 font-mono text-xs">
      {children}
    </kbd>
  );
}

function RecentIcon({ id, type }: { id: string; type: RecentItem["type"] }) {
  const cls = "size-[15px] shrink-0 text-muted-foreground";
  if (type === "capability") return <Icon.capability className={cls} />;
  if (type === "project") return <Icon.chart className={cls} />;
  const resource = RESOURCES.find((r) => r.id === id);
  if (resource) {
    const ResourceIcon = resource.icon;
    return <ResourceIcon className={cls} />;
  }
  return <Icon.job className={cls} />;
}

function RemoveHint() {
  return (
    <span className="ml-2 hidden shrink-0 items-center gap-0.5 text-xs text-muted-foreground group-data-[selected=true]:flex">
      <kbd className="inline-flex h-4 min-w-4 items-center justify-center rounded-sm border border-border bg-muted px-0.5 font-mono">
        {isMac ? "⌘" : "Ctrl"}
      </kbd>
      <kbd className="inline-flex h-4 min-w-4 items-center justify-center rounded-sm border border-border bg-muted px-0.5 font-mono">
        ↵
      </kbd>
      <span className="ml-0.5">remove</span>
    </span>
  );
}
