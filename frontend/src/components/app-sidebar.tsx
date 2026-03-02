import { useState } from "react";

import { useAuth, UserButton, useUser } from "@clerk/clerk-react";
import { Link, useNavigate, useRouteContext, useRouterState } from "@tanstack/react-router";
import {
  Briefcase,
  BuildingCommunity,
  Chart,
  ChevronUp,
  Home,
  Login,
  Logout,
  Robot,
  User,
  Zap,
} from "pixelarticons/react";

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
} from "@/components/ui/sidebar";

const navLinks = [
  { icon: Home, label: "Home", to: "/" },
  { icon: Robot, label: "Agents", to: "/agents" },
  { icon: Briefcase, label: "Jobs", to: "/jobs" },
  { icon: Chart, label: "Traces", to: "/projects" },
];

export function AppSidebar() {
  const { location } = useRouterState();
  const { config } = useRouteContext({ from: "/_auth" });
  const isSignedIn = useIsSignedIn();

  return (
    <Sidebar collapsible="icon">
      <SidebarHeader className="flex shrink-0 flex-row items-center px-3 pt-5">
        <SidebarMenu>
          <SidebarMenuItem>
            <SidebarMenuButton asChild size="default" tooltip="Overmind">
              <Link className="flex items-center gap-2" to="/">
                <img
                  alt="Overmind"
                  className="!size-[20px] !min-h-[20px] !min-w-[20px] shrink-0 object-contain"
                  src="/overmind_logo.png"
                />
                <span className="font-display text-lg font-medium uppercase leading-none translate-y-px">Overmind</span>
              </Link>
            </SidebarMenuButton>
          </SidebarMenuItem>
        </SidebarMenu>
      </SidebarHeader>

      <SidebarContent className="px-1 pt-3">
        <SidebarGroup>
          <SidebarGroupContent>
            <SidebarMenu className="gap-1.5">
              {navLinks.map(({ to, icon: Icon, label }) => {
                const isActive =
                  location.pathname === to || (to !== "/" && location.pathname.startsWith(to));
                return (
                  <SidebarMenuItem key={to}>
                    <SidebarMenuButton asChild isActive={isActive} tooltip={label}>
                      <Link className="!py-2.5" to={to}>
                        <Icon className="!size-[17px]" />
                        <span className="sidebar-label">{label}</span>
                      </Link>
                    </SidebarMenuButton>
                  </SidebarMenuItem>
                );
              })}
            </SidebarMenu>
          </SidebarGroupContent>
        </SidebarGroup>
      </SidebarContent>

      <SidebarFooter className="px-1 pt-2">
        {isSignedIn ? (
          <SidebarMenu>{config.isSelfHosted ? <OSSUserButton /> : <EEUserButton />}</SidebarMenu>
        ) : (
          <SidebarMenu>
            <SidebarMenuItem>
              <SidebarMenuButton asChild tooltip="Sign in">
                <Link className="!py-2.5" to="/login">
                  <Login className="!size-[17px]" />
                  <span className="sidebar-label">Sign in</span>
                </Link>
              </SidebarMenuButton>
            </SidebarMenuItem>
          </SidebarMenu>
        )}
      </SidebarFooter>
    </Sidebar>
  );
}

const accountLinks = [
  { icon: Zap, label: "Getting Started", to: "/get-started" },
  { icon: User, label: "Account", to: "/account" },
  { icon: BuildingCommunity, label: "Organisations", to: "/organisations" },
];

function useIsSignedIn() {
  const { config } = useRouteContext({ from: "/_auth" });
  if (typeof window === "undefined") return false;
  if (config.clerkReady) return useAuth().isSignedIn;
  return !!(localStorage.getItem("token") ?? localStorage.getItem("auth_token"));
}

const EEUserButton = () => {
  const { config } = useRouteContext({ from: "/_auth" });
  const { location } = useRouterState();
  const navigate = useNavigate();
  const [accountOpen, setAccountOpen] = useState(false);

  const handleLogout = () => {
    localStorage.removeItem("token");
    localStorage.removeItem("auth_token");
    localStorage.removeItem("auth_user");
    navigate({ to: "/login" });
  };

  const handleToggleAccount = () => {
    setAccountOpen((prev) => !prev);
  };
  if (config.clerkReady) return <EEClerkUserButton />;
  return (
    <>
      {/* Expandable account items — slide up from footer */}
      <div
        className="flex flex-col gap-1 overflow-hidden transition-all duration-200 ease-in-out"
        style={{
          maxHeight: accountOpen ? `${(accountLinks.length + 1) * 40}px` : "0px",
          opacity: accountOpen ? 1 : 0,
        }}
      >
        {accountLinks.map(({ to, icon: Icon, label }) => {
          const isActive = location.pathname === to;
          return (
            <SidebarMenuItem key={to}>
              <SidebarMenuButton asChild isActive={isActive} tooltip={label}>
                <Link to={to}>
                  <Icon className="!size-[17px]" />
                  <span className="sidebar-label">{label}</span>
                </Link>
              </SidebarMenuButton>
            </SidebarMenuItem>
          );
        })}
        <SidebarMenuItem>
          <SidebarMenuButton
            className="text-destructive hover:text-destructive"
            onClick={handleLogout}
            tooltip="Logout"
          >
            <Logout className="!size-[17px]" />
            <span className="sidebar-label">Logout</span>
          </SidebarMenuButton>
        </SidebarMenuItem>
      </div>
      {/* Account trigger — toggles expansion */}
      <SidebarMenuItem>
        <SidebarMenuButton
          isActive={location.pathname === "/account"}
          onClick={handleToggleAccount}
          tooltip="Account"
        >
          <User className="!size-[17px]" />
          <span className="sidebar-label flex-1">Account</span>
          <ChevronUp
            className="!size-[15px] shrink-0 transition-transform duration-200"
            style={{ transform: accountOpen ? "rotate(180deg)" : "rotate(0deg)" }}
          />
        </SidebarMenuButton>
      </SidebarMenuItem>
    </>
  );
};

const EEClerkUserButton = () => {
  const { isSignedIn, isLoaded } = useUser();
  if (!isLoaded || !isSignedIn) return null;
  return (
    <SidebarMenuItem>
      <div
        className="peer/menu-button flex h-8 w-full items-center gap-2 overflow-hidden rounded-md p-2 text-left text-sm outline-none ring-sidebar-ring transition-[width,height,padding] hover:bg-sidebar-accent hover:text-sidebar-accent-foreground group-data-[collapsible=icon]:!size-8 group-data-[collapsible=icon]:!p-0 group-data-[collapsible=icon]:justify-center"
        data-sidebar="menu-button"
      >
        <UserButton
          appearance={{
            elements: {
              avatarBox: "size-5 shrink-0",
              userButtonBox: "flex items-center gap-2 w-full overflow-hidden flex-row-reverse",
              userButtonOuterIdentifier:
                "truncate text-sm text-sidebar-foreground group-data-[collapsible=icon]:hidden",
              userButtonTrigger: "focus:shadow-none w-full flex items-center gap-2 px-0",
            },
          }}
          showName
        />
      </div>
    </SidebarMenuItem>
  );
};

const OSSUserButton = () => {
  const [accountOpen, setAccountOpen] = useState(false);
  const navigate = useNavigate();
  const handleLogout = () => {
    localStorage.removeItem("token");
    localStorage.removeItem("auth_token");
    localStorage.removeItem("auth_user");
    navigate({ to: "/login" });
  };

  const handleToggleAccount = () => {
    setAccountOpen((prev) => !prev);
  };
  return (
    <>
      {/* Expandable account items — slide up from footer */}
      <div
        className="flex flex-col gap-1 overflow-hidden transition-all duration-200 ease-in-out"
        style={{
          maxHeight: accountOpen ? `${(accountLinks.length + 1) * 40}px` : "0px",
          opacity: accountOpen ? 1 : 0,
        }}
      >
        {accountLinks.map(({ to, icon: Icon, label }) => {
          const isActive = location.pathname === to;
          return (
            <SidebarMenuItem key={to}>
              <SidebarMenuButton asChild isActive={isActive} tooltip={label}>
                <Link to={to}>
                  <Icon className="!size-[17px]" />
                  <span className="sidebar-label">{label}</span>
                </Link>
              </SidebarMenuButton>
            </SidebarMenuItem>
          );
        })}
        <SidebarMenuItem>
          <SidebarMenuButton
            className="text-destructive hover:text-destructive"
            onClick={handleLogout}
            tooltip="Logout"
          >
            <Logout className="!size-[17px]" />
            <span className="sidebar-label">Logout</span>
          </SidebarMenuButton>
        </SidebarMenuItem>
      </div>

      {/* Account trigger — toggles expansion */}
      <SidebarMenuItem>
        <SidebarMenuButton
          isActive={location.pathname === "/account"}
          onClick={handleToggleAccount}
          tooltip="Account"
        >
          <User className="!size-[17px]" />
          <span className="sidebar-label flex-1">Account</span>
          <ChevronUp
            className="!size-[15px] shrink-0 transition-transform duration-200"
            style={{ transform: accountOpen ? "rotate(180deg)" : "rotate(0deg)" }}
          />
        </SidebarMenuButton>
      </SidebarMenuItem>
    </>
  );
};
