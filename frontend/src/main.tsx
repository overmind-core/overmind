import { StrictMode } from "react";
import ReactDOM from "react-dom/client";

import { ClerkProvider } from "@clerk/clerk-react";
import { createRouter as createTanStackRouter, RouterProvider } from "@tanstack/react-router";

import { RouteErrorFallback, RouteNotFound } from "./components/route-error";
import { Toaster } from "./components/ui/sonner";

import "./styles.css";
import "./analytics";

import { config } from "./config";
import { AuthProvider } from "./contexts/auth-context";
import PostHogProvider from "./integrations/posthog-provider";
import { getContext } from "./integrations/tanstack-query";
import { installAutohideScrollbars } from "./lib/autohide-scrollbars";
import { routeTree } from "./routeTree.gen";

installAutohideScrollbars();

export function getRouter() {
  const router = createTanStackRouter({
    context: getContext(),
    defaultErrorComponent: RouteErrorFallback,
    defaultNotFoundComponent: RouteNotFound,
    defaultPreload: "intent",
    defaultPreloadStaleTime: 0,
    routeTree,
    scrollRestoration: true,
  });
  return router;
}

declare module "@tanstack/react-router" {
  interface Register {
    router: ReturnType<typeof getRouter>;
  }
}

const router = getRouter();

function App() {
  return (
    <AuthProvider>
      <PostHogProvider>
        <RouterProvider router={router} />
        <Toaster position="bottom-right" />
      </PostHogProvider>
    </AuthProvider>
  );
}

const rootElement = document.getElementById("app");
if (rootElement && !rootElement.innerHTML) {
  const root = ReactDOM.createRoot(rootElement);
  const tree = (
    <StrictMode>
      <App />
    </StrictMode>
  );
  root.render(
    config.clerkReady ? <ClerkProvider publishableKey={config.clerkPk}>{tree}</ClerkProvider> : tree
  );
}
