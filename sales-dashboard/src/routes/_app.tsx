import { useEffect } from "react";

import { useQueryClient } from "@tanstack/react-query";
import { createFileRoute, Link, Outlet, redirect, useNavigate } from "@tanstack/react-router";

import overwatchLogo from "~/assets/overwatch-logo.svg";
import { clearToken, getToken } from "~/lib/api";

import { Button } from "@/components/ui/button";
import { Icon } from "@/components/ui/icons";
import { LABEL } from "@/lib/typography";
import { cn } from "@/lib/utils";

const WINDOWS = [7, 30, 90, 365] as const;

const NAV = [
  { label: "Overview", to: "/" },
  { label: "Activation", to: "/funnel" },
  { label: "Users", to: "/accounts" },
] as const;

export const Route = createFileRoute("/_app")({
  beforeLoad: () => {
    if (!getToken()) throw redirect({ to: "/login" });
  },
  component: Shell,
  validateSearch: (search: Record<string, unknown>): { days: number } => {
    const days = Number(search.days);
    return { days: (WINDOWS as readonly number[]).includes(days) ? days : 30 };
  },
});

function Shell() {
  const { days } = Route.useSearch();
  const navigate = useNavigate();
  const queryClient = useQueryClient();

  useEffect(() => {
    const checkSession = () => {
      if (!getToken()) {
        queryClient.clear();
        void navigate({ to: "/login" });
      }
    };
    const interval = window.setInterval(checkSession, 5000);
    document.addEventListener("visibilitychange", checkSession);
    return () => {
      window.clearInterval(interval);
      document.removeEventListener("visibilitychange", checkSession);
    };
  }, [navigate, queryClient]);

  function signOut() {
    clearToken();
    queryClient.clear();
    void navigate({ to: "/login" });
  }

  return (
    <div className="flex h-screen flex-col">
      <header className="flex flex-wrap items-center gap-x-6 gap-y-3 border-b border-border/70 bg-card px-6 py-3">
        <div className="flex items-center gap-2">
          <img alt="" className="size-6 shrink-0" height={24} src={overwatchLogo} width={24} />
          <span className={LABEL.sidebar}>Overwatch</span>
        </div>
        <nav aria-label="Sections" className="flex gap-1">
          {NAV.map((item) => (
            <Link
              activeOptions={{ exact: true, includeSearch: false }}
              activeProps={{ "data-active": "true" }}
              className={cn(
                LABEL.sidebar,
                "rounded-sm px-2 py-1 text-muted-foreground transition-colors hover:text-foreground data-[active=true]:bg-foreground/8 data-[active=true]:text-foreground"
              )}
              key={item.to}
              search={{ days }}
              to={item.to}
            >
              {item.label}
            </Link>
          ))}
        </nav>
        <div className="ml-auto flex items-center gap-2">
          <fieldset aria-label="Window" className="flex gap-1">
            {WINDOWS.map((value) => (
              <Button
                aria-pressed={value === days}
                key={value}
                onClick={() =>
                  void navigate({ search: (prev) => ({ ...prev, days: value }), to: "." })
                }
                size="sm"
                variant={value === days ? "default" : "secondary"}
              >
                {value}d
              </Button>
            ))}
          </fieldset>
          <Button onClick={signOut} size="sm" variant="secondary">
            <Icon.logout />
            Sign out
          </Button>
        </div>
      </header>
      <main className="flex-1 overflow-y-auto">
        <div className="mx-auto max-w-screen-2xl space-y-6 p-6">
          <Outlet />
        </div>
      </main>
    </div>
  );
}
