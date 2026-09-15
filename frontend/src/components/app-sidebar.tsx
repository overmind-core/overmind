import { type ComponentType, type CSSProperties, type SVGProps, useEffect, useState } from "react";

import { useUser } from "@clerk/clerk-react";
import { Link, useRouterState, useSearch } from "@tanstack/react-router";

import { identifyUser, trackEvent } from "@/analytics";
import overmindEyeIcon from "@/assets/overmind-eye-copper.svg";
import { AccountMenu } from "@/components/account-menu";
import { FeedbackDialog } from "@/components/feedback-dialog";
import {
  JobNotificationBadge,
  type JobNotificationBadgeTone,
} from "@/components/job-notification-badge";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
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
  SidebarMenuSub,
  SidebarMenuSubButton,
  SidebarMenuSubItem,
  useSidebar,
} from "@/components/ui/sidebar";
import { useAuthContext } from "@/contexts/auth-context";
import { useAllEvalRunsQuery } from "@/hooks/use-evaluations";
import { useFinetuningJobsQuery } from "@/hooks/use-finetuning";
import { useGuestGate } from "@/hooks/use-guest-gate";
import { useOptimizerExperimentsQuery } from "@/hooks/use-optimizer";
import { useOnboardingStatus } from "@/hooks/use-query";
import { featureFlags } from "@/lib/feature-flags";
import { PUBLIC_PRICING_URL } from "@/lib/marketing";
import { isMac } from "@/lib/platform";
import { buildRunningJobs, type RunningJobKind } from "@/lib/running-jobs";
import {
  clearUnseenCompletionsForKinds,
  kindsForPathname,
  useUnseenCompletions,
} from "@/lib/unseen-job-completions";
import { cn } from "@/lib/utils";

type IconType = ComponentType<SVGProps<SVGSVGElement>>;
type Flag = keyof typeof featureFlags;

interface NavLeaf {
  flag: Flag | null;
  icon: IconType;
  label: string;
  to: string;
}

interface NavGroup {
  icon: IconType;
  label: string;
  items: NavLeaf[];
}

type NavEntry = NavLeaf | NavGroup;

const isGroup = (entry: NavEntry): entry is NavGroup => "items" in entry;
const flagOn = (flag: Flag | null): boolean => flag === null || featureFlags[flag];

const platformNavTree: NavEntry[] = [
  { flag: null, icon: Icon.agent, label: "Agent", to: "/" },
  { flag: null, icon: Icon.observability, label: "Observability", to: "/observability" },
  { flag: "datasets", icon: Icon.dataset, label: "Datasets", to: "/datasets" },
  {
    icon: Icon.agentTesting,
    items: [
      { flag: "evaluations", icon: Icon.eval, label: "Evaluations", to: "/evaluations" },
      { flag: "evaluations", icon: Icon.optimiser, label: "Optimiser", to: "/optimiser" },
    ],
    label: "Agent Testing",
  },
  {
    icon: Icon.models,
    items: [
      { flag: "finetuning", icon: Icon.training, label: "Training", to: "/training" },
      { flag: "inference", icon: Icon.inference, label: "Inference", to: "/inference" },
    ],
    label: "Models",
  },
];

// Not rendered in the sidebar — Settings is the top-bar gear and Projects sits
// in the project switcher. Kept here so `navLinks` still reaches them.
const configNavTree: NavLeaf[] = [
  { flag: null, icon: Icon.settings, label: "Settings", to: "/settings" },
  { flag: null, icon: Icon.project, label: "Projects", to: "/projects" },
  { flag: null, icon: Icon.integrations, label: "Integrations", to: "/observability/integrations" },
];

const filterNav = (tree: NavEntry[]): NavEntry[] =>
  tree
    .map((entry) =>
      isGroup(entry) ? { ...entry, items: entry.items.filter((i) => flagOn(i.flag)) } : entry
    )
    .filter((entry) => (isGroup(entry) ? entry.items.length > 0 : flagOn(entry.flag)));

const platformNav: NavEntry[] = filterNav(platformNavTree);
const configNav: NavLeaf[] = configNavTree.filter((leaf) => flagOn(leaf.flag));

export const navLinks: NavLeaf[] = [
  ...platformNav.flatMap((entry) => (isGroup(entry) ? entry.items : [entry])),
  ...configNav,
];

const externalLinks = [
  { href: "https://docs.overmindlab.ai", icon: Icon.docs, label: "Docs" },
  { href: "https://discord.gg/TPF722ZKuj", icon: DiscordIcon, label: "Discord" },
];

function DiscordIcon({ className }: { className?: string }) {
  return (
    <svg
      aria-hidden="true"
      className={className}
      fill="none"
      viewBox="0 0 16 16"
      xmlns="http://www.w3.org/2000/svg"
    >
      <path
        d="M14.6667 7.33335V5.33335H14V4.00002H13.3333V3.33335H12V2.66669H10V3.33335H6V2.66669H4V3.33335H2.66666V4.00002H2V5.33335H1.33333V7.33335H0.666664V12H2V12.6667H3.33333V13.3334H4.66666V12H4V11.3334H5.33333V12H6V12.6667H10V12H10.6667V11.3334H12V12H11.3333V13.3334H12.6667V12.6667H14V12H15.3333V7.33335H14.6667ZM6 10H4.66666V9.33335H4V8.00002H4.66666V7.33335H6V8.00002H6.66666V9.33335H6V10ZM12 9.33335H11.3333V10H10V9.33335H9.33333V8.00002H10V7.33335H11.3333V8.00002H12V9.33335Z"
        fill="currentColor"
      />
    </svg>
  );
}

const isLeafActive = (pathname: string, to: string): boolean =>
  pathname === to || (to !== "/" && pathname.startsWith(to));

const JOB_KIND_ROUTE: Record<RunningJobKind, string> = {
  eval: "/evaluations",
  finetuning: "/training",
  optimizer: "/optimiser",
};

type NavJobBadge = { count: number; tone: JobNotificationBadgeTone };

/** Polls the same lists as the header bell; react-query dedupes the requests. */
function useNavJobBadges(): Record<string, NavJobBadge> {
  const { projectId } = useSearch({ from: "/_auth" });
  const ftQuery = useFinetuningJobsQuery(projectId);
  const evalQuery = useAllEvalRunsQuery(projectId);
  const optimizerQuery = useOptimizerExperimentsQuery(projectId);
  const unseen = useUnseenCompletions();

  const running = buildRunningJobs({
    evalRuns: evalQuery.data?.results,
    finetuning: ftQuery.data?.results,
    optimizerExperiments: optimizerQuery.data?.results,
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
          search={(e) => ({ projectId: e.projectId })}
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

// The trigger deliberately takes no `tooltip` prop: `CollapsibleTrigger asChild`
// and the tooltip wrapper clash over the ref and swallow the toggle click.
function NavGroupItem({
  group,
  pathname,
  badges,
}: {
  group: NavGroup;
  pathname: string;
  badges: Record<string, NavJobBadge>;
}) {
  const { icon: IconCmp, items, label } = group;
  const guard = useGuestGate();
  const hasActiveChild = items.some((item) => isLeafActive(pathname, item.to));
  return (
    <Collapsible asChild className="group/collapsible" defaultOpen>
      <SidebarMenuItem>
        <CollapsibleTrigger asChild>
          <SidebarMenuButton className="!py-2.5" isActive={hasActiveChild}>
            <IconCmp className="!size-[17px]" />
            <span className="sidebar-label flex-1">{label}</span>
            <Icon.chevronRight className="ml-auto !size-4 shrink-0 text-muted-foreground transition-transform duration-200 group-data-[state=open]/collapsible:rotate-90" />
          </SidebarMenuButton>
        </CollapsibleTrigger>
        <CollapsibleContent>
          {/* border-l-0 drops the primitive's 1px trunk; each item draws its own. */}
          <SidebarMenuSub className="mr-0 border-l-0 pr-0">
            {items.map((item, index) => {
              const ItemIcon = item.icon;
              const active = isLeafActive(pathname, item.to);
              return (
                <SidebarMenuSubItem
                  // `.sidebar-elbow::before` (styles.css) draws each item's own
                  // connector. Every trunk starts at one shared point 0.75rem
                  // above the FIRST row's centre and ends at its own row's
                  // centre — hence the row pitch (2rem) times index. The active
                  // line is raised above the muted ones, and its colour must
                  // stay a sidebar token: a hard `#fff` vanishes in light theme.
                  className={cn("sidebar-elbow", active && "z-10")}
                  key={item.to}
                  style={
                    {
                      "--elbow-h": `calc(${index} * 2rem + 0.75rem)`,
                      ...(active && { "--elbow-color": "var(--sidebar-foreground)" }),
                    } as CSSProperties
                  }
                >
                  <SidebarMenuSubButton asChild isActive={active}>
                    <Link
                      onClick={item.to === "/" ? undefined : guard()}
                      search={(e) => ({ projectId: e.projectId })}
                      to={item.to}
                    >
                      <ItemIcon className="!size-[15px]" />
                      <span className="sidebar-label">{item.label}</span>
                      {badges[item.to] ? <NavJobCountBadge badge={badges[item.to]} /> : null}
                    </Link>
                  </SidebarMenuSubButton>
                </SidebarMenuSubItem>
              );
            })}
          </SidebarMenuSub>
        </CollapsibleContent>
      </SidebarMenuItem>
    </Collapsible>
  );
}

function NavSection({
  entries,
  iconOnly,
  pathname,
  badges = {},
}: {
  entries: NavEntry[];
  iconOnly: boolean;
  pathname: string;
  badges?: Record<string, NavJobBadge>;
}) {
  return (
    <>
      {entries.map((entry) => {
        if (!isGroup(entry)) {
          return (
            <NavLink badge={badges[entry.to]} key={entry.to} leaf={entry} pathname={pathname} />
          );
        }
        return iconOnly ? (
          entry.items.map((leaf) => (
            <NavLink badge={badges[leaf.to]} key={leaf.to} leaf={leaf} pathname={pathname} />
          ))
        ) : (
          <NavGroupItem badges={badges} group={entry} key={entry.label} pathname={pathname} />
        );
      })}
    </>
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
  const { isSignedIn, isGuest, requestUpgrade } = useAuthContext();
  const { user } = useUser();
  const navJobBadges = useNavJobBadges();
  const { data: me } = useOnboardingStatus(isSignedIn);
  const planLabel = me?.plan === "pro" ? "Pro" : "Free";
  const showPlan = me?.billingEnabled === true;
  const [feedbackOpen, setFeedbackOpen] = useState(false);
  const clerkUserId = user?.id ?? "";
  useEffect(() => {
    trackEvent("login_changed", { isSignedIn });
    identifyUser({ clerkUserId, userId: clerkUserId });
  }, [isSignedIn, clerkUserId]);
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
              <NavSection
                badges={navJobBadges}
                entries={platformNav}
                iconOnly={iconOnly}
                pathname={location.pathname}
              />
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
