import { SignIn, SignUp } from "@clerk/clerk-react";
import { createFileRoute, Link, Navigate } from "@tanstack/react-router";
import { z } from "zod";

import { LocalLoginForm } from "@/components/local-login-form";
import { AuthenticationLoadingState } from "@/components/route-error";
import { SplashBackground } from "@/components/splash-background";
import { config } from "@/config";
import { useAuthContext } from "@/contexts/auth-context";
import { useLoginGate } from "@/hooks/use-login-gate";
import { safeNextPath } from "@/lib/auth-redirect";
import { PUBLIC_PRICING_URL } from "@/lib/marketing";

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
    cardBox: "rounded-md border border-auth-border bg-auth-panel/90 shadow-none",
  },
};

function RouteComponent() {
  const { refreshAuth } = useAuthContext();
  const { mode } = Route.useSearch();
  // `?next=` may carry its own query string, which TanStack's parsed search would mangle.
  const safeNext = safeNextPath(new URLSearchParams(window.location.search).get("next"));
  const gate = useLoginGate(safeNext);
  // Clerk ignores our `next` and lands on "/" unless told where to go, and the
  // sign-in/sign-up toggle would drop it.
  const nextQuery = safeNext ? `next=${encodeURIComponent(safeNext)}` : "";
  const signInUrl = `/login${nextQuery ? `?${nextQuery}` : ""}`;
  const signUpUrl = `/login?mode=signup${nextQuery ? `&${nextQuery}` : ""}`;
  const forceRedirectUrl = safeNext ?? undefined;

  if (gate.kind === "loading") {
    return (
      <SplashBackground>
        <AuthenticationLoadingState />
      </SplashBackground>
    );
  }

  if (gate.kind === "leaving") return null;

  if (gate.kind === "home") {
    return gate.dropProject ? (
      <Navigate search={{ projectId: undefined }} to="/" />
    ) : (
      <Navigate to="/" />
    );
  }

  if (!config.clerkReady) {
    return (
      <SplashBackground>
        <div className="mx-4 flex w-full max-w-sm flex-col items-center justify-center gap-5">
          <LocalLoginForm onSignedIn={refreshAuth} />
        </div>
      </SplashBackground>
    );
  }

  return (
    <SplashBackground>
      <div className="flex flex-col items-center justify-center gap-5">
        {mode === "signup" ? (
          <SignUp
            appearance={CLERK_APPEARANCE}
            fallback={<AuthenticationLoadingState />}
            forceRedirectUrl={forceRedirectUrl}
            oauthFlow="popup"
            routing="hash"
            signInUrl={signInUrl}
          />
        ) : (
          <SignIn
            appearance={CLERK_APPEARANCE}
            fallback={<AuthenticationLoadingState />}
            forceRedirectUrl={forceRedirectUrl}
            oauthFlow="popup"
            routing="hash"
            signUpUrl={signUpUrl}
          />
        )}
        <div className="flex flex-col items-center gap-2">
          {gate.offerDemo ? (
            <Link
              className="animate-fade-in-up text-sm font-semibold text-auth-text/85 underline-offset-4 hover:text-auth-text hover:underline [animation-delay:500ms]"
              to="/demo"
            >
              Set up locally
            </Link>
          ) : null}
          <a
            className="animate-fade-in-up text-sm font-semibold text-auth-text/85 underline-offset-4 hover:text-auth-text hover:underline [animation-delay:550ms]"
            href={PUBLIC_PRICING_URL}
            rel="noreferrer"
            target="_blank"
          >
            View pricing
          </a>
        </div>
      </div>
    </SplashBackground>
  );
}
