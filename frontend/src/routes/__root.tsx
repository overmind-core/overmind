import type { QueryClient } from "@tanstack/react-query";
import { createRootRouteWithContext, Outlet, Scripts } from "@tanstack/react-router";

import { RootQueryProvider } from "../integrations/tanstack-query";

interface MyRouterContext {
  queryClient: QueryClient;
  authUser: { id: string; email: string; name: string; picture: string | null } | undefined;
}

export const Route = createRootRouteWithContext<MyRouterContext>()({
  shellComponent: RootDocument,
});

function RootDocument() {
  return (
    <>
      <RootQueryProvider>
        <Outlet />
      </RootQueryProvider>
      <Scripts />
    </>
  );
}
