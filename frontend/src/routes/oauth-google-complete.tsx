import { createFileRoute, Navigate } from "@tanstack/react-router";

export const Route = createFileRoute("/oauth-google-complete")({
  component: () => <Navigate replace to="/login" />,
  validateSearch: (search: Record<string, unknown>) => ({
    code: (search.code as string) ?? "",
    state: (search.state as string) ?? "",
  }),
});
