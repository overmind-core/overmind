import { StrictMode } from "react";
import ReactDOM from "react-dom/client";

import { RouterProvider } from "@tanstack/react-router";

import "./styles.css";

import { createRouter as createTanStackRouter } from "@tanstack/react-router";
import { Toaster } from "sonner";

import PostHogProvider from "./integrations/posthog-provider";
import { getContext } from "./integrations/tanstack-query";
import { routeTree } from "./routeTree.gen";

export function getRouter() {
  const router = createTanStackRouter({
    context: getContext(),
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

// Render the app
const rootElement = document.getElementById("app");
if (rootElement && !rootElement.innerHTML) {
  const root = ReactDOM.createRoot(rootElement);
  root.render(
    <StrictMode>
      <PostHogProvider>
        <RouterProvider router={router} />
        <Toaster position="bottom-right" />
      </PostHogProvider>
    </StrictMode>
  );
}
