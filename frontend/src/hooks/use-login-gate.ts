import { useEffect, useState } from "react";

import { useRouter } from "@tanstack/react-router";

import { useAuthContext } from "@/contexts/auth-context";
import { hasGuestSession } from "@/lib/guest";

export type LoginGate =
  | { kind: "loading" }
  | { kind: "leaving" }
  | { kind: "home"; dropProject: boolean }
  | { kind: "form"; offerDemo: boolean };

// A demo guest counts as signed in but must still reach the form to claim an account.
export function useLoginGate(next: string | null): LoginGate {
  const router = useRouter();
  const { isSignedIn, isLoaded, isGuest } = useAuthContext();
  // Read once: the claim clears the flag before the redirect below runs.
  const [wasGuest] = useState(hasGuestSession);
  const member = isLoaded && isSignedIn && !isGuest;

  useEffect(() => {
    if (!member || !next) return;
    // `href` parses the whole destination, including nested search and its fragment.
    void router.navigate({ href: next, replace: true });
  }, [member, next, router]);

  if (!isLoaded) return { kind: "loading" };
  if (member) return next ? { kind: "leaving" } : { dropProject: wasGuest, kind: "home" };
  return { kind: "form", offerDemo: !isGuest };
}
