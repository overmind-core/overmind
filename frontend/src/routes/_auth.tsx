import { createFileRoute, Outlet } from "@tanstack/react-router";

import { AppSidebar } from "../components/app-sidebar";
import { ThemeToggle } from "../components/theme-toggle";
import { SidebarInset, SidebarProvider, SidebarTrigger } from "../components/ui/sidebar";

export const Route = createFileRoute("/_auth")({
  component: RouteComponent,
});

function RouteComponent() {
  return (
    <SidebarProvider>
      <AppSidebar />
      <SidebarInset className="w-md">
        <header className="flex h-14 shrink-0 items-center gap-2 border-b border-border px-4 sticky top-0 bg-background z-10">
          <SidebarTrigger className="-ml-1" />
          <span className="flex-1 text-sm font-medium text-muted-foreground"></span>
          <ThemeToggle />
        </header>
        <div className="flex-1 p-4 md:p-6">
          <Outlet />
        </div>
      </SidebarInset>
    </SidebarProvider>
  );
}
