import { useEffect, useState } from "react";

import { SignIn, SignUp } from "@clerk/clerk-react";
import { createFileRoute, Link, Navigate } from "@tanstack/react-router";
import { z } from "zod";

import { SplashBackground } from "@/components/splash-background";
import { Spinner } from "@/components/ui/spinner";
import { config } from "@/config";
import { useAuthContext } from "@/contexts/auth-context";
import { hasGuestSession } from "@/lib/guest";
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
    rootBox: "animate-fade-in-up [animation-delay:400ms]",
  },
};

function RouteComponent() {
  const { isSignedIn, isLoaded, isGuest } = useAuthContext();
  const { mode } = Route.useSearch();
  // Read once: the claim clears the flag before the redirect below runs.
  const [wasGuest] = useState(hasGuestSession);
  // `?next=` may carry its own query string; `//` is rejected as an open redirect.
  const next = new URLSearchParams(window.location.search).get("next");
  const safeNext = next?.startsWith("/") && !next.startsWith("//") ? next : null;

  useEffect(() => {
    if (!config.clerkReady || !isLoaded || !isSignedIn || isGuest || !safeNext) return;
    // Full assign so nested search params survive (TanStack `<Navigate to>` drops them).
    window.location.replace(safeNext);
  }, [isGuest, isLoaded, isSignedIn, safeNext]);

  if (wasGuest && !isLoaded) {
    return (
      <SplashBackground>
        <Spinner size="lg" />
      </SplashBackground>
    );
  }

  if (config.clerkReady && isLoaded && isSignedIn && !isGuest) {
    if (safeNext) return null;
    return wasGuest ? <Navigate search={{ projectId: undefined }} to="/" /> : <Navigate to="/" />;
  }

  return (
    <SplashBackground>
      <div className="flex flex-col items-center justify-center gap-5">
        {mode === "signup" ? (
          <SignUp
            appearance={CLERK_APPEARANCE}
            oauthFlow="popup"
            routing="hash"
            signInUrl="/login"
          />
        ) : (
          <SignIn
            appearance={CLERK_APPEARANCE}
            oauthFlow="popup"
            routing="hash"
            signUpUrl="/login?mode=signup"
          />
        )}
        <div className="flex flex-col items-center gap-2">
          {isGuest ? null : (
            <Link
              className="animate-fade-in-up text-sm font-semibold text-auth-text/85 underline-offset-4 hover:text-auth-text hover:underline [animation-delay:500ms]"
              to="/demo"
            >
              Set up locally
            </Link>
          )}
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
