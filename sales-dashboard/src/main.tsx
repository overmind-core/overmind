import { StrictMode } from "react";
import ReactDOM from "react-dom/client";

import { QueryCache, QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { createRouter, RouterProvider } from "@tanstack/react-router";

import { clearToken, Unauthorized } from "~/lib/api";
import { routeTree } from "~/routeTree.gen";

import "./styles.css";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      retry: (count, error) => !(error instanceof Unauthorized) && count < 2,
      staleTime: 60_000,
    },
  },
  queryCache: new QueryCache({
    onError: (error) => {
      if (!(error instanceof Unauthorized)) return;
      clearToken();
      queryClient.clear();
      void router.navigate({ search: { rejected: true }, to: "/login" });
    },
  }),
});

const router = createRouter({ defaultPreload: "intent", routeTree });

declare module "@tanstack/react-router" {
  interface Register {
    router: typeof router;
  }
}

const rootElement = document.getElementById("app");
if (rootElement) {
  ReactDOM.createRoot(rootElement).render(
    <StrictMode>
      <QueryClientProvider client={queryClient}>
        <RouterProvider router={router} />
      </QueryClientProvider>
    </StrictMode>
  );
}
