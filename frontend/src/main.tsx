import { StrictMode } from "react";
import ReactDOM from "react-dom/client";

import { shadcn } from "@clerk/themes";
import { RouterProvider } from "@tanstack/react-router";

import "./styles.css";

import { ClerkProvider } from "@clerk/clerk-react";
import { createRouter as createTanStackRouter } from "@tanstack/react-router";
import { Toaster } from "sonner";

import { config } from "./config";
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

const AuthProvider = ({ children }: { children: React.ReactNode }) => {
  if (!config.clerkReady || config.isSelfHosted) {
    return children;
  }
  return (
    <ClerkProvider
      afterSignOutUrl={"/login"}
      appearance={{ theme: shadcn }}
      publishableKey={config.clerkPublishableKey!}
      signInForceRedirectUrl={"/"}
      signInUrl="/login"
      signUpForceRedirectUrl={"/"}
      signUpUrl="/login"
    >
      {children}
    </ClerkProvider>
  );
};

// Render the app
const rootElement = document.getElementById("app");
if (rootElement && !rootElement.innerHTML) {
  const root = ReactDOM.createRoot(rootElement);
  root.render(
    <StrictMode>
      <AuthProvider>
        <PostHogProvider>
          <RouterProvider router={router} />
          <Toaster position="bottom-right" />
        </PostHogProvider>
      </AuthProvider>
    </StrictMode>
  );
}
