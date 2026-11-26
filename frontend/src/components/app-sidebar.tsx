import { type ComponentType, type SVGProps, useEffect, useState } from "react";

import { Link, useRouterState, useSearch } from "@tanstack/react-router";

import { identifyUser, trackEvent } from "@/analytics";
import overmindEyeIcon from "@/assets/overmind-eye-copper.svg";
import { AccountMenu } from "@/components/account-menu";
import { FeedbackDialog } from "@/components/feedback-dialog";
import {
  JobNotificationBadge,
  type JobNotificationBadgeTone,
} from "@/components/job-notification-badge";
import { Icon } from "@/components/ui/icons";
import {
  Sidebar,
  SidebarContent,
  SidebarFooter,
  SidebarGroup,
  SidebarGroupContent,
  SidebarHeader,
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
  useSidebar,
} from "@/components/ui/sidebar";
import { useAuthContext } from "@/contexts/auth-context";
import { useAllEvalRunsQuery } from "@/hooks/use-evaluations";
import { useFinetuningJobsQuery } from "@/hooks/use-finetuning";
import { useGuestGate } from "@/hooks/use-guest-gate";
import { useOnboardingStatus } from "@/hooks/use-query";
import { useUnseenSignalCountQuery } from "@/hooks/use-signals";
import { featureFlags } from "@/lib/feature-flags";
import { PUBLIC_PRICING_URL } from "@/lib/marketing";
import { isMac } from "@/lib/platform";
import { buildRunningJobs, type RunningJobKind } from "@/lib/running-jobs";
import {
  clearUnseenCompletionsForKinds,
  kindsForPathname,
  useUnseenCompletions,
} from "@/lib/unseen-job-completions";

type IconType = ComponentType<SVGProps<SVGSVGElement>>;
type Flag = keyof typeof featureFlags;

interface NavLeaf {
  flag: Flag | null;
  icon: IconType;
  label: string;
  to: string;
}

const flagOn = (flag: Flag | null): boolean => flag === null || featureFlags[flag];

const platformNavTree: NavLeaf[] = [
  { flag: null, icon: Icon.agent, label: "Agent", to: "/" },
  { flag: null, icon: Icon.observability, label: "Observability", to: "/observability" },
  { flag: "datasets", icon: Icon.dataset, label: "Datasets", to: "/datasets" },
  { flag: "evaluations", icon: Icon.eval, label: "Evaluations", to: "/evaluations" },
  { flag: "finetuning", icon: Icon.training, label: "Training", to: "/training" },
  { flag: "inference", icon: Icon.inference, label: "Inference", to: "/inference" },
];

// Not rendered in the sidebar — Settings is the top-bar gear and Projects sits
// in the project switcher. Kept here so `navLinks` still reaches them.
const configNavTree: NavLeaf[] = [
  { flag: null, icon: Icon.settings, label: "Settings", to: "/settings" },
  { flag: null, icon: Icon.project, label: "Projects", to: "/projects" },
  { flag: null, icon: Icon.integrations, label: "Integrations", to: "/observability/integrations" },
];

const platformNav: NavLeaf[] = platformNavTree.filter((leaf) => flagOn(leaf.flag));
const configNav: NavLeaf[] = configNavTree.filter((leaf) => flagOn(leaf.flag));

export const navLinks: NavLeaf[] = [...platformNav, ...configNav];

const externalLinks = [
  { href: "https://docs.overmindlab.ai", icon: Icon.docs, label: "Docs" },
  { href: "https://discord.gg/TPF722ZKuj", icon: Icon.discord, label: "Discord" },
];

const isLeafActive = (pathname: string, to: string): boolean =>
  pathname === to || (to !== "/" && pathname.startsWith(to));

const JOB_KIND_ROUTE: Record<RunningJobKind, string> = {
  eval: "/evaluations",
  finetuning: "/training",
};

type NavJobBadge = { count: number; tone: JobNotificationBadgeTone };

/** Polls the same lists as the header bell; react-query dedupes the requests. */
function useNavJobBadges(): Record<string, NavJobBadge> {
  const { projectId } = useSearch({ from: "/_auth" });
  const ftQuery = useFinetuningJobsQuery(projectId);
  const evalQuery = useAllEvalRunsQuery(projectId);
  const unseen = useUnseenCompletions();
  const unseenSignals =
    useUnseenSignalCountQuery(featureFlags.signals ? projectId : undefined).data ?? 0;

  const running = buildRunningJobs({
    evalRuns: evalQuery.data?.results,
    finetuning: ftQuery.data?.results,
  });

  const runningByRoute: Record<string, number> = {};
  for (const job of running) {
    const route = JOB_KIND_ROUTE[job.kind];
    runningByRoute[route] = (runningByRoute[route] ?? 0) + 1;
  }

  const unseenByRoute: Record<string, number> = {};
  for (const job of unseen) {
    const route = JOB_KIND_ROUTE[job.kind];
    unseenByRoute[route] = (unseenByRoute[route] ?? 0) + 1;
  }

  const badges: Record<string, NavJobBadge> = {};
  for (const route of new Set([...Object.keys(runningByRoute), ...Object.keys(unseenByRoute)])) {
    const unseenCount = unseenByRoute[route] ?? 0;
    if (unseenCount > 0) {
      badges[route] = { count: unseenCount, tone: "success" };
      continue;
    }
    const runningCount = runningByRoute[route] ?? 0;
    if (runningCount > 0) badges[route] = { count: runningCount, tone: "running" };
  }
  if (unseenSignals > 0) badges["/observability"] = { count: unseenSignals, tone: "success" };
  return badges;
}

/** `sidebar-label` hides the chip with the text in the collapsed icon rail. */
function NavJobCountBadge({ badge }: { badge: NavJobBadge }) {
  return (
    <JobNotificationBadge
      className="sidebar-label ml-auto shrink-0"
      count={badge.count}
      tone={badge.tone}
    />
  );
}

function NavLink({
  leaf,
  pathname,
  badge,
}: {
  leaf: NavLeaf;
  pathname: string;
  badge?: NavJobBadge;
}) {
  const { icon: Icon, label, to } = leaf;
  const guard = useGuestGate();
  const handleClick = to === "/" ? undefined : guard();
  return (
    <SidebarMenuItem>
      <SidebarMenuButton asChild isActive={isLeafActive(pathname, to)} tooltip={label}>
        <Link
          className="!py-2.5"
          onClick={handleClick}
          search={(e) => ({
            projectId: e.projectId,
            ...(to === "/observability" ? { view: "sessions" } : {}),
          })}
          to={to}
        >
          <Icon className="!size-[17px]" />
          <span className="sidebar-label">{label}</span>
          {badge ? <NavJobCountBadge badge={badge} /> : null}
        </Link>
      </SidebarMenuButton>
    </SidebarMenuItem>
  );
}

function ExternalNavLink({ href, icon: Icon, label }: (typeof externalLinks)[number]) {
  return (
    <SidebarMenuItem>
      <SidebarMenuButton asChild tooltip={label}>
        <a className="!py-2.5" href={href} rel="noreferrer" target="_blank">
          <Icon className="!size-[17px]" />
          <span className="sidebar-label">{label}</span>
        </a>
      </SidebarMenuButton>
    </SidebarMenuItem>
  );
}

interface AppSidebarProps {
  onSearchOpen?: () => void;
  /** `"icon"` collapses to a 3 rem rail; focus layouts (the workshop console)
   *  pass `"offcanvas"` to hide the sidebar entirely. */
  collapsible?: "icon" | "offcanvas" | "none";
}

export function AppSidebar({ collapsible = "icon", onSearchOpen }: AppSidebarProps) {
  const { location } = useRouterState();
  const { state, isMobile } = useSidebar();
  // The 3 rem rail has no room for dropdown groups, so it flattens to leaves.
  const iconOnly = state === "collapsed" && !isMobile;
  const { isSignedIn, isGuest, requestUpgrade, user } = useAuthContext();
  const navJobBadges = useNavJobBadges();
  const { data: me } = useOnboardingStatus(isSignedIn);
  const planLabel = me?.plan === "pro" ? "Pro" : "Free";
  const showPlan = me?.billingEnabled === true;
  const [feedbackOpen, setFeedbackOpen] = useState(false);
  const clerkUserId = user?.id ?? "";
  const userEmail = user?.email ?? "";
  const userName = user?.name ?? "";
  useEffect(() => {
    trackEvent("login_changed", { isSignedIn });
  }, [isSignedIn]);
  useEffect(() => {
    identifyUser({
      clerkUserId,
      email: userEmail || undefined,
      name: userName || undefined,
      userId: clerkUserId,
    });
  }, [clerkUserId, userEmail, userName]);
  useEffect(() => {
    const kinds = kindsForPathname(location.pathname);
    if (kinds) clearUnseenCompletionsForKinds(kinds);
  }, [location.pathname]);
  return (
    <Sidebar collapsible={collapsible}>
      {/* px-3 plus a size-8 slot matches the nav column's 12px inset
          (SidebarContent px-1 + SidebarGroup p-2), so the collapsed rail's logo
          lands on the same axis as the icons below it. */}
      <SidebarHeader className="mt-2 flex h-14 shrink-0 flex-row items-center gap-2 px-3 py-0">
        <span className="flex size-8 shrink-0 items-center justify-center">
          <img alt="Overmind" className="size-6 object-contain" src={overmindEyeIcon} />
        </span>
        <button
          className="flex h-8 min-w-0 flex-1 items-center gap-2 rounded-md border border-border bg-wash-subtle px-2 text-left text-sm text-muted-foreground transition-colors hover:bg-sidebar-accent hover:text-sidebar-accent-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/60 group-data-[collapsible=icon]:hidden"
          onClick={onSearchOpen}
          type="button"
        >
          <Icon.search className="!size-[17px] shrink-0" />
          <span className="sidebar-label flex-1 truncate">Search</span>
          <span className="sidebar-label ml-auto hidden items-center gap-1 sm:flex">
            <kbd className="inline-flex h-5 items-center justify-center rounded-sm border border-border bg-muted px-1.5 font-mono text-xs font-medium text-foreground">
              {isMac ? "⌘" : "Ctrl"}
            </kbd>
            <kbd className="inline-flex h-5 items-center justify-center rounded-sm border border-border bg-muted px-1.5 font-mono text-xs font-medium text-foreground">
              K
            </kbd>
          </span>
        </button>
      </SidebarHeader>

      <SidebarContent className="px-1 py-2">
        <SidebarGroup className="py-0">
          <SidebarGroupContent>
            <SidebarMenu className="gap-1.5">
              {/* The collapsed rail has no header search field, so it exposes a
                  Search icon here instead. */}
              {iconOnly && (
                <SidebarMenuItem>
                  <SidebarMenuButton
                    className="!py-2.5 text-muted-foreground hover:text-foreground"
                    onClick={onSearchOpen}
                    tooltip="Search"
                  >
                    <Icon.search className="!size-[17px] shrink-0" />
                    <span className="sidebar-label">Search</span>
                  </SidebarMenuButton>
                </SidebarMenuItem>
              )}
              {platformNav.map((leaf) => (
                <NavLink
                  badge={navJobBadges[leaf.to]}
                  key={leaf.to}
                  leaf={leaf}
                  pathname={location.pathname}
                />
              ))}
            </SidebarMenu>
          </SidebarGroupContent>
        </SidebarGroup>

        {/* No divider above Resources: the gap is the separator. */}
        <SidebarGroup className="mt-auto py-0">
          <SidebarGroupContent>
            <SidebarMenu className="gap-1.5">
              {externalLinks.map((link) => (
                <ExternalNavLink key={link.label} {...link} />
              ))}
              <SidebarMenuItem>
                <SidebarMenuButton
                  className="!py-2.5"
                  onClick={() => setFeedbackOpen(true)}
                  tooltip="Feedback"
                >
                  <Icon.megaphone className="!size-[17px]" />
                  <span className="sidebar-label">Feedback</span>
                </SidebarMenuButton>
              </SidebarMenuItem>
            </SidebarMenu>
          </SidebarGroupContent>
        </SidebarGroup>
      </SidebarContent>

      {/* Collapsed rail: pr-1 spans the account row across the same 32px band as
          the size-8 nav buttons, so the avatar lands on the nav icons' axis. */}
      <SidebarFooter className="mb-2 px-3 pb-0 pt-2 group-data-[collapsible=icon]:pr-1">
        <SidebarMenu>
          {isGuest ? (
            <SidebarMenuItem>
              <SidebarMenuButton
                className="!py-2.5"
                onClick={requestUpgrade}
                tooltip="Create account"
              >
                <Icon.login className="!size-[17px]" />
                <span className="sidebar-label">Create account</span>
              </SidebarMenuButton>
            </SidebarMenuItem>
          ) : isSignedIn ? (
            <SidebarMenuItem>
              {/* Mirrors the header: a size-6 avatar in a 32px slot puts its
                  centre on the logo's, and h-10 + pb-0 sit the row on the main
                  pane's bottom-bar band. */}
              <div className="flex items-center gap-1 rounded-md transition-colors hover:bg-sidebar-accent focus-within:bg-sidebar-accent">
                <AccountMenu />
                {showPlan ? (
                  <a
                    className="chip-label mr-1 shrink-0 rounded-sm border border-border px-1.5 py-0.5 text-xs text-muted-foreground transition-colors hover:border-sidebar-accent-foreground/30 hover:text-sidebar-accent-foreground group-data-[collapsible=icon]:hidden"
                    href={PUBLIC_PRICING_URL}
                    rel="noreferrer"
                    target="_blank"
                    title="View plans"
                  >
                    {planLabel}
                  </a>
                ) : null}
              </div>
            </SidebarMenuItem>
          ) : (
            <SidebarMenuItem>
              <SidebarMenuButton asChild tooltip="Sign in">
                <Link className="!py-2.5" to="/login">
                  <Icon.login className="!size-[17px]" />
                  <span className="sidebar-label">Sign in</span>
                </Link>
              </SidebarMenuButton>
            </SidebarMenuItem>
          )}
        </SidebarMenu>
      </SidebarFooter>

      <FeedbackDialog onOpenChange={setFeedbackOpen} open={feedbackOpen} />
    </Sidebar>
  );
}
