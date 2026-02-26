import { OrganizationSwitcher, RedirectToSignUp, SignedIn, SignedOut } from "@clerk/clerk-react";
import { useQueryClient } from "@tanstack/react-query";
import { createFileRoute, Link, Outlet, useRouterState } from "@tanstack/react-router";
import { ChevronRight, PanelLeftClose, PanelLeftOpen } from "lucide-react";

import { AppSidebar } from "../components/app-sidebar";
import { ThemeToggle } from "../components/theme-toggle";
import { Button } from "../components/ui/button";
import { SidebarInset, SidebarProvider, useSidebar } from "../components/ui/sidebar";

export const Route = createFileRoute("/_auth")({
  component: RouteComponent,
});

// ─── Breadcrumb helpers ──────────────────────────────────────────────────────

type Crumb = { label: string; path: string };

const DYNAMIC_PARENTS = new Set(["agents", "projects", "jobs"]);

function prettifySegment(seg: string): string {
  return decodeURIComponent(seg).replace(/[-_]/g, " ");
}

function useCachedName(parent: string | undefined, slug: string | undefined): string | undefined {
  const queryClient = useQueryClient();
  if (!parent || !slug) return undefined;

  const keyMap: Record<string, string[]> = {
    agents: ["agent-detail", slug],
    projects: ["project", slug],
  };

  const prefix = keyMap[parent];
  if (!prefix) return undefined;

  const queries = queryClient.getQueriesData<{ name?: string }>({ queryKey: prefix });
  for (const [, data] of queries) {
    if (data?.name) return data.name;
  }
  return undefined;
}

function useBreadcrumbs(): Crumb[] {
  const { location } = useRouterState();
  const { pathname } = location;
  const segments = pathname.split("/").filter(Boolean);

  const dynamicParent =
    segments.length >= 2 && DYNAMIC_PARENTS.has(segments[0]) ? segments[0] : undefined;
  const dynamicSlug = dynamicParent ? segments[1] : undefined;
  const cachedName = useCachedName(dynamicParent, dynamicSlug);

  if (pathname === "/") return [{ label: "Home", path: "/" }];

  const crumbs: Crumb[] = [{ label: "Home", path: "/" }];
  let builtPath = "";

  for (let i = 0; i < segments.length; i++) {
    const seg = segments[i];
    builtPath += `/${seg}`;

    const isDynamicSlug = i === 1 && dynamicParent;
    const label = isDynamicSlug && cachedName ? cachedName : prettifySegment(seg);

    crumbs.push({ label, path: builtPath });
  }

  return crumbs;
}

// ─── Custom sidebar toggle using Lucide icons ────────────────────────────────

function SidebarToggle() {
  const { toggleSidebar, state } = useSidebar();
  const isCollapsed = state === "collapsed";

  return (
    <Button
      aria-label={isCollapsed ? "Expand sidebar" : "Collapse sidebar"}
      className="size-7"
      onClick={toggleSidebar}
      size="icon"
      variant="ghost"
    >
      {isCollapsed ? (
        <PanelLeftOpen className="size-4" strokeWidth={1.5} />
      ) : (
        <PanelLeftClose className="size-4" strokeWidth={1.5} />
      )}
    </Button>
  );
}

// ─── Breadcrumb ──────────────────────────────────────────────────────────────

function Breadcrumb() {
  const crumbs = useBreadcrumbs();

  return (
    <nav aria-label="Breadcrumb" className="flex items-center gap-1.5 text-sm">
      {crumbs.map((crumb, i) => {
        const isLast = i === crumbs.length - 1;
        return (
          <span className="flex items-center gap-1.5" key={crumb.path}>
            {i > 0 && <ChevronRight className="size-3.5 text-muted-foreground" strokeWidth={1.5} />}
            {isLast ? (
              <span className="font-medium capitalize text-foreground">{crumb.label}</span>
            ) : (
              <Link
                className="capitalize text-muted-foreground transition-colors hover:text-foreground"
                to={crumb.path}
              >
                {crumb.label}
              </Link>
            )}
          </span>
        );
      })}
    </nav>
  );
}

// ─── Layout ──────────────────────────────────────────────────────────────────

function RootLayout() {
  const { config } = Route.useRouteContext();
  return (
    <SidebarProvider>
      <AppSidebar />
      <SidebarInset className="w-md bg-background">
        <div className="flex h-screen flex-col p-2">
          <div className="flex flex-1 flex-col overflow-hidden rounded-lg border border-border bg-card">
            <header className="flex h-14 shrink-0 items-center gap-3 border-b border-border px-4">
              <SidebarToggle />
              <Breadcrumb />
              <span className="flex-1" />
              <ThemeToggle />
              {(config.clerkReady && !config.isSelfHosted) && (
                <OrganizationSwitcher
                  createOrganizationMode={"modal"}
                  organizationProfileMode={"modal"}
                />
              )}
            </header>
            <div className="flex min-h-0 flex-1 flex-col overflow-y-auto p-4 md:p-6">
              <Outlet />
            </div>
          </div>
        </div>
      </SidebarInset>
    </SidebarProvider>
  );
}

function RouteComponent() {
  const { config } = Route.useRouteContext();
  if (!config.clerkReady || config.isSelfHosted) return <RootLayout />;
  return (
    <>
      <SignedIn>
        <RootLayout />
      </SignedIn>
      <SignedOut>
        <RedirectToSignUp />
      </SignedOut>
    </>
  );
}
