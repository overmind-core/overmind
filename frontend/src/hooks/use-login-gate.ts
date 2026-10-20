import { useEffect, useState } from "react";

import { useAuthContext } from "@/contexts/auth-context";
import { hasGuestSession } from "@/lib/guest";

export type LoginGate =
  | { kind: "loading" }
  | { kind: "leaving" }
  | { kind: "home"; dropProject: boolean }
  | { kind: "form"; offerDemo: boolean };

// A demo guest counts as signed in but must still reach the form to claim an account.
export function useLoginGate(next: string | null): LoginGate {
  const { isSignedIn, isLoaded, isGuest } = useAuthContext();
  // Read once: the claim clears the flag before the redirect below runs.
  const [wasGuest] = useState(hasGuestSession);
  const member = isLoaded && isSignedIn && !isGuest;

  useEffect(() => {
    if (!member || !next) return;
    // Full assign so nested search params survive (TanStack `<Navigate to>` drops them).
    window.location.replace(next);
  }, [member, next]);

  if (wasGuest && !isLoaded) return { kind: "loading" };
  if (member) return next ? { kind: "leaving" } : { dropProject: wasGuest, kind: "home" };
  return { kind: "form", offerDemo: !isGuest };
}
