import { StrictMode } from "react";
import ReactDOM from "react-dom/client";

import { ClerkFailed, ClerkLoaded, ClerkLoading, ClerkProvider } from "@clerk/clerk-react";
import { createRouter as createTanStackRouter } from "@tanstack/react-router";

import { AuthRouter } from "./components/auth-router";
import {
  AuthenticationLoadError,
  AuthenticationLoadingState,
  RouteErrorFallback,
  RouteNotFound,
} from "./components/route-error";
import { Toaster } from "./components/ui/sonner";

import "./styles.css";
import "./analytics";

import { config } from "./config";
import { AuthProvider } from "./contexts/auth-context";
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
      {config.clerkReady ? (
        <>
          <ClerkLoading>
            <div className="flex min-h-screen flex-col items-center justify-center gap-4">
              <AuthenticationLoadingState />
            </div>
          </ClerkLoading>
          <ClerkFailed>
            <AuthenticationLoadError />
          </ClerkFailed>
          <ClerkLoaded>
            <AuthRouter router={router} />
          </ClerkLoaded>
        </>
      ) : (
        <AuthRouter router={router} />
      )}
      <Toaster position="bottom-right" />
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
