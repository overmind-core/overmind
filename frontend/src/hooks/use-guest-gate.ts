import { useCallback } from "react";

import { useAuthContext } from "@/contexts/auth-context";

/**
 * `guard(handler)` runs the handler for members and swaps it for the
 * create-account dialog for guests. `guard()` with no handler gates Radix
 * `asChild` triggers and router Links: both honor `defaultPrevented`, so the
 * guarded click leaves them inert.
 */
export function useGuestGate() {
  const { isGuest, requestUpgrade } = useAuthContext();
  return useCallback(
    <A extends unknown[]>(handler?: (...args: A) => void) =>
      (...args: A) => {
        if (!isGuest) {
          handler?.(...args);
          return;
        }
        (args[0] as { preventDefault?: () => void } | undefined)?.preventDefault?.();
        requestUpgrade();
      },
    [isGuest, requestUpgrade]
  );
}
