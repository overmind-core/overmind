import type { QueryClient } from "@tanstack/react-query";
import { createRootRouteWithContext, Outlet, Scripts } from "@tanstack/react-router";

import { RootQueryProvider } from "../integrations/tanstack-query";

interface MyRouterContext {
  queryClient: QueryClient;
  auth: { isLoaded: boolean; isSignedIn: boolean };
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
