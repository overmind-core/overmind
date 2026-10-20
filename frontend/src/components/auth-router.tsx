import { useEffect, useRef } from "react";

import { type AnyRouter, RouterProvider } from "@tanstack/react-router";

import { useAuthContext } from "@/contexts/auth-context";

// Auth lives in React state, so the router sees it through context and re-runs
// every `beforeLoad` when it changes.
export function AuthRouter({ router }: { router: AnyRouter }) {
  const { isLoaded, isSignedIn } = useAuthContext();
  const previous = useRef(`${isLoaded}:${isSignedIn}`);

  useEffect(() => {
    const key = `${isLoaded}:${isSignedIn}`;
    if (previous.current === key) return;
    previous.current = key;
    void router.invalidate();
  }, [isLoaded, isSignedIn, router]);

  return <RouterProvider context={{ auth: { isLoaded, isSignedIn } }} router={router} />;
}
