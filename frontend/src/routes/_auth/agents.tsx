import { createFileRoute, Outlet } from "@tanstack/react-router";

export const Route = createFileRoute("/_auth/agents")({
  component: AgentsLayout,
});

function AgentsLayout() {
  return <Outlet />;
}
