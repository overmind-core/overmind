import { createFileRoute, Navigate } from "@tanstack/react-router";

import { GuestRepoPicker } from "@/components/guest/repo-picker";
import { SplashBackground } from "@/components/splash-background";
import { LoadingState } from "@/components/ui/spinner";
import { useAuthContext } from "@/contexts/auth-context";

export const Route = createFileRoute("/demo")({
  component: DemoPage,
});

function DemoPage() {
  const { isSignedIn, isLoaded, isGuest } = useAuthContext();

  if (!isLoaded) {
    return (
      <SplashBackground>
        <LoadingState className="max-w-sm" label="Loading demo" />
      </SplashBackground>
    );
  }

  // Signed-in members skip the local-setup tip and go straight to the agent.
  if (isSignedIn && !isGuest) {
    return <Navigate search={{ projectId: undefined }} to="/" />;
  }

  return <GuestRepoPicker />;
}
