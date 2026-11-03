import { SignIn, SignUp } from "@clerk/clerk-react";
import { createFileRoute, Link, Navigate } from "@tanstack/react-router";
import { z } from "zod";

import { ConnectionScene } from "@/components/connection-scene";
import { LocalLoginForm } from "@/components/local-login-form";
import { AuthenticationLoadingState } from "@/components/route-error";
import { config } from "@/config";
import { useAuthContext } from "@/contexts/auth-context";
import { useLoginGate } from "@/hooks/use-login-gate";
import { safeNextPath } from "@/lib/auth-redirect";
import { PUBLIC_PRICING_URL } from "@/lib/marketing";
import { isMobileBrowser } from "@/lib/platform";

export const Route = createFileRoute("/login")({
  component: RouteComponent,
  validateSearch: z.object({
    mode: z.enum(["signup"]).optional().catch(undefined),
    next: z.string().optional(),
  }),
});

const CLERK_APPEARANCE = {
  elements: {
    card: "bg-transparent shadow-none",
    cardBox: "w-full rounded-md border border-auth-border bg-auth-panel/90 shadow-none",
    rootBox: "w-full max-w-sm",
  },
};

function RouteComponent() {
  const { refreshAuth } = useAuthContext();
  const { mode, next } = Route.useSearch();
  // The matched login search survives while the next route's code is loading.
  const safeNext = safeNextPath(next);
  const gate = useLoginGate(safeNext);
  // Clerk ignores our `next` and lands on "/" unless told where to go, and the
  // sign-in/sign-up toggle would drop it.
  const nextQuery = safeNext ? `next=${encodeURIComponent(safeNext)}` : "";
  const signInUrl = `/login${nextQuery ? `?${nextQuery}` : ""}`;
  const signUpUrl = `/login?mode=signup${nextQuery ? `&${nextQuery}` : ""}`;
  const forceRedirectUrl = safeNext ?? undefined;
  const oauthFlow = isMobileBrowser() ? "redirect" : "popup";

  if (gate.kind === "loading") {
    return (
      <ConnectionScene>
        <AuthenticationLoadingState />
      </ConnectionScene>
    );
  }

  if (gate.kind === "leaving")
    return (
      <ConnectionScene>
        <AuthenticationLoadingState label="Opening your workspace" />
      </ConnectionScene>
    );

  if (gate.kind === "home") {
    return (
      <ConnectionScene>
        <AuthenticationLoadingState label="Opening your workspace" />
        {gate.dropProject ? (
          <Navigate search={{ projectId: undefined }} to="/" />
        ) : (
          <Navigate to="/" />
        )}
      </ConnectionScene>
    );
  }

  if (!config.clerkReady) {
    return (
      <ConnectionScene>
        <div className="flex w-full max-w-sm flex-col items-center justify-center gap-5">
          <LocalLoginForm onSignedIn={refreshAuth} />
        </div>
      </ConnectionScene>
    );
  }

  return (
    <ConnectionScene>
      <div className="flex w-full max-w-xl flex-col items-center justify-center gap-5">
        {mode === "signup" ? (
          <SignUp
            appearance={CLERK_APPEARANCE}
            fallback={<AuthenticationLoadingState />}
            forceRedirectUrl={forceRedirectUrl}
            oauthFlow={oauthFlow}
            routing="hash"
            signInUrl={signInUrl}
          />
        ) : (
          <SignIn
            appearance={CLERK_APPEARANCE}
            fallback={<AuthenticationLoadingState />}
            forceRedirectUrl={forceRedirectUrl}
            oauthFlow={oauthFlow}
            routing="hash"
            signUpUrl={signUpUrl}
          />
        )}
        <div className="flex flex-col items-center gap-2">
          {gate.offerDemo ? (
            <Link
              className="text-sm font-semibold text-auth-text/85 underline-offset-4 hover:text-auth-text hover:underline"
              to="/demo"
            >
              Set up locally
            </Link>
          ) : null}
          <a
            className="text-sm font-semibold text-auth-text/85 underline-offset-4 hover:text-auth-text hover:underline"
            href={PUBLIC_PRICING_URL}
            rel="noreferrer"
            target="_blank"
          >
            View pricing
          </a>
        </div>
      </div>
    </ConnectionScene>
  );
}
