import { createFileRoute, redirect } from "@tanstack/react-router";

// The capabilities index page does not exist yet — the product page at /agent
// covers it. Drop this redirect when /agent moves to /capabilities.
export const Route = createFileRoute("/_auth/capabilities/")({
  beforeLoad: ({ search }) => {
    throw redirect({ search, to: "/" });
  },
});
