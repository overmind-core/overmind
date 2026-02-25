import { useState } from "react";

import { UserButton, useUser } from "@clerk/clerk-react";
import { Link, useNavigate, useRouteContext, useRouterState } from "@tanstack/react-router";
import {
  BarChart3,
  Bot,
  Briefcase,
  Building2,
  ChevronUp,
  Home,
  LogIn,
  LogOut,
  User,
} from "lucide-react";

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
  { icon: Bot, label: "Agents", to: "/agents" },
  { icon: Briefcase, label: "Jobs", to: "/jobs" },
  { icon: BarChart3, label: "Traces", to: "/projects" },
];

export function AppSidebar() {
  const { location } = useRouterState();
  const { config } = useRouteContext({ from: "/_auth" });

  return (
    <Sidebar collapsible="icon">
      <SidebarHeader className="flex shrink-0 flex-row items-center pt-5">
        <SidebarMenu>
          <SidebarMenuItem>
            <SidebarMenuButton asChild size="default" tooltip="Overmind">
              <Link className="flex items-center gap-2" to="/">
                <img
                  alt="Overmind"
                  className="size-4 shrink-0 object-contain"
                  src="/overmind_logo.png"
                />
                <span className="font-bold" style={{ fontFamily: "var(--font-sidebar)" }}>
                  Overmind
                </span>
              </Link>
            </SidebarMenuButton>
          </SidebarMenuItem>
        </SidebarMenu>
      </SidebarHeader>

      <SidebarContent>
        <SidebarGroup>
          <SidebarGroupContent>
            <SidebarMenu>
              {navLinks.map(({ to, icon: Icon, label }) => {
                const isActive =
                  location.pathname === to || (to !== "/" && location.pathname.startsWith(to));
                return (
                  <SidebarMenuItem key={to}>
                    <SidebarMenuButton asChild isActive={isActive} tooltip={label}>
                      <Link to={to}>
                        <Icon className="size-4" />
                        <span style={{ fontFamily: "var(--font-sidebar)" }}>{label}</span>
                      </Link>
                    </SidebarMenuButton>
                  </SidebarMenuItem>
                );
              })}
            </SidebarMenu>
          </SidebarGroupContent>
        </SidebarGroup>
      </SidebarContent>

      <SidebarFooter>{config.isSelfHosted ? <OSSUserButton /> : <EEUserButton />}</SidebarFooter>
    </Sidebar>
  );
}
const accountLinks = [
  { icon: User, label: "Account", to: "/account" },
  { icon: Building2, label: "Organisations", to: "/organisations" },
];

function useIsSignedIn() {
  if (typeof window === "undefined") return false;
  return !!(localStorage.getItem("token") ?? localStorage.getItem("auth_token"));
}

const OSSUserButton = () => {
  const isSignedIn = useIsSignedIn();
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
    <SidebarMenu>
      {isSignedIn ? (
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
                      <Icon className="size-4" />
                      <span style={{ fontFamily: "var(--font-sidebar)" }}> {label} </span>
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
                <LogOut className="size-4" />
                <span style={{ fontFamily: "var(--font-sidebar)" }}> Logout </span>
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
              <User className="size-4" />
              <span className="flex-1" style={{ fontFamily: "var(--font-sidebar)" }}>
                Account
              </span>
              <ChevronUp
                className="size-3.5 shrink-0 transition-transform duration-200"
                style={{ transform: accountOpen ? "rotate(180deg)" : "rotate(0deg)" }}
              />
            </SidebarMenuButton>
          </SidebarMenuItem>
        </>
      ) : (
        <SidebarMenuItem>
          <SidebarMenuButton asChild tooltip="Sign in">
            <Link to="/login">
              <LogIn className="size-4" />
              <span style={{ fontFamily: "var(--font-sidebar)" }}> Sign in </span>
            </Link>
          </SidebarMenuButton>
        </SidebarMenuItem>
      )}
    </SidebarMenu>
  );
};
const EEUserButton = () => {
  const { isSignedIn, isLoaded } = useUser();
  return (
    <SidebarMenu>
      <SidebarMenuItem>
        {isLoaded && isSignedIn ? (
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
        ) : (
          <SidebarMenuButton asChild tooltip="Sign in">
            <Link to="/login">
              <LogIn className="size-4" />
              <span style={{ fontFamily: "var(--font-sidebar)" }}>Sign in</span>
            </Link>
          </SidebarMenuButton>
        )}
      </SidebarMenuItem>
    </SidebarMenu>
  );
};
