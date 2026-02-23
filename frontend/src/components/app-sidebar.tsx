import { Link, useNavigate, useRouterState } from "@tanstack/react-router";
import { BarChart3, Bot, Briefcase, Home, LogIn, LogOut, User } from "lucide-react";

import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
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
  SidebarRail,
} from "@/components/ui/sidebar";

const navLinks = [
  { icon: Home, label: "Home", to: "/" },
  { icon: Bot, label: "Agents", to: "/agents" },
  { icon: Briefcase, label: "Jobs", to: "/jobs" },
  { icon: BarChart3, label: "Traces", to: "/projects" },
];

function useIsSignedIn() {
  if (typeof window === "undefined") return false;
  return !!(localStorage.getItem("token") ?? localStorage.getItem("auth_token"));
}

export function AppSidebar() {
  const { location } = useRouterState();
  const navigate = useNavigate();
  const isSignedIn = useIsSignedIn();

  const handleLogout = () => {
    localStorage.removeItem("token");
    localStorage.removeItem("auth_token");
    localStorage.removeItem("auth_user");
    navigate({ to: "/login" });
  };

  return (
    <Sidebar collapsible="icon">
      <SidebarHeader className="flex h-14 shrink-0 flex-row items-center border-b">
        <SidebarMenu>
          <SidebarMenuItem>
            <SidebarMenuButton asChild className="h-10 shrink-0" size="default">
              <Link className="flex items-center gap-2 py-4" to="/">
                <img
                  alt="Overmind"
                  className="size-8 shrink-0 object-contain"
                  src="/overmind_logo.png"
                />
                <span className="font-bold">Overmind</span>
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
                        <span>{label}</span>
                      </Link>
                    </SidebarMenuButton>
                  </SidebarMenuItem>
                );
              })}
            </SidebarMenu>
          </SidebarGroupContent>
        </SidebarGroup>
      </SidebarContent>

      <SidebarFooter className="border-t border-sidebar-border">
        <SidebarMenu>
          <SidebarMenuItem>
            {isSignedIn ? (
              <DropdownMenu>
                <DropdownMenuTrigger asChild>
                  <SidebarMenuButton isActive={location.pathname === "/account"} tooltip="Account">
                    <User className="size-4" />
                    <span>Account</span>
                  </SidebarMenuButton>
                </DropdownMenuTrigger>
                <DropdownMenuContent align="end" className="min-w-40" side="top" sideOffset={8}>
                  <DropdownMenuItem asChild>
                    <Link to="/account">
                      <User className="size-4" />
                      Account
                    </Link>
                  </DropdownMenuItem>
                  <DropdownMenuSeparator />
                  <DropdownMenuItem onClick={handleLogout} variant="destructive">
                    <LogOut className="size-4" />
                    Logout
                  </DropdownMenuItem>
                </DropdownMenuContent>
              </DropdownMenu>
            ) : (
              <SidebarMenuButton asChild tooltip="Sign in">
                <Link to="/login">
                  <LogIn className="size-4" />
                  <span>Sign in</span>
                </Link>
              </SidebarMenuButton>
            )}
          </SidebarMenuItem>
        </SidebarMenu>
      </SidebarFooter>
      <SidebarRail />
    </Sidebar>
  );
}
