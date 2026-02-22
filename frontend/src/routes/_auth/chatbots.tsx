import { createFileRoute, Outlet } from "@tanstack/react-router";

export const Route = createFileRoute("/_auth/chatbots")({
  component: ChatbotsLayout,
});

function ChatbotsLayout() {
  return <Outlet />;
}
