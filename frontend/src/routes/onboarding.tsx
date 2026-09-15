import { useEffect, useState } from "react";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { createFileRoute, Navigate, useNavigate } from "@tanstack/react-router";

import overmindEye from "@/assets/overmind-eye-copper.svg";
import apiClient from "@/client";
import { clearDraft } from "@/components/onboarding/draft";
import { OnboardingShell } from "@/components/onboarding/shell";
import { OnboardWithAiPanel } from "@/components/quickstart/onboard-with-ai-panel";
import { Button } from "@/components/ui/button";
import { Icon } from "@/components/ui/icons";
import { Spinner } from "@/components/ui/spinner";
import { useAuthContext } from "@/contexts/auth-context";
import { useOnboardingStatus } from "@/hooks/use-query";
import { onboardingSearchSchema } from "@/lib/schemas";
import { TITLE } from "@/lib/typography";
import { cn } from "@/lib/utils";
import type { UserMe } from "@/openapi";

const DONE_DWELL_MS = 1400;

export const Route = createFileRoute("/onboarding")({
  component: OnboardingPage,
  validateSearch: onboardingSearchSchema,
});

function ErrorBanner({ message }: { message: string }) {
  return (
    <p className="flex shrink-0 items-start gap-1.5 rounded-md border border-destructive/40 bg-destructive/10 px-3 py-2 text-sm text-destructive">
      <Icon.warning className="mt-0.5 size-4 shrink-0" />
      {message}
    </p>
  );
}

function CenteredSpinner() {
  return (
    <div className="flex h-full items-center justify-center">
      <Spinner size="lg" />
    </div>
  );
}

function OnboardingPage() {
  const { isSignedIn, isLoaded, isGuest } = useAuthContext();
  const meQuery = useOnboardingStatus(isSignedIn && isLoaded && !isGuest);
  const meId = meQuery.data?.id == null ? "" : String(meQuery.data.id);

  if (!isLoaded) {
    return (
      <div className="fixed inset-0 flex items-center justify-center bg-auth-splash">
        <Spinner size="lg" />
      </div>
    );
  }

  if (!isSignedIn) return <Navigate to="/login" />;
  if (isGuest) return <Navigate search={{ projectId: undefined }} to="/" />;

  return (
    <OnboardingShell>
      {meQuery.isLoading || !meId ? (
        <CenteredSpinner />
      ) : (
        <SetupForm hasCompleted={!!meQuery.data?.hasCompletedOnboarding} key={meId} meId={meId} />
      )}
    </OnboardingShell>
  );
}

function SetupForm({ hasCompleted }: { meId: string; hasCompleted: boolean }) {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [done, setDone] = useState(false);

  useEffect(() => {
    if (hasCompleted) return;
    apiClient.auth
      .authOnboardingPartialUpdate({ patchedUserOnboardingRequest: { status: "in_progress" } })
      .catch(() => {});
  }, [hasCompleted]);

  const finishMutation = useMutation({
    mutationFn: async () => {
      setSubmitError(null);
      await apiClient.auth.authOnboardingPartialUpdate({
        patchedUserOnboardingRequest: { status: "completed" },
      });
    },
    onError: (err: Error) => setSubmitError(err.message),
    onSuccess: () => {
      queryClient.setQueryData(["auth-me"], (prev: UserMe | undefined) =>
        prev ? { ...prev, hasCompletedOnboarding: true } : prev
      );
      void queryClient.invalidateQueries({ queryKey: ["auth-me"] });
      clearDraft();
      setDone(true);
    },
  });

  useEffect(() => {
    if (!done) return;
    const t = setTimeout(() => {
      void navigate({ search: { projectId: undefined }, to: "/" });
    }, DONE_DWELL_MS);
    return () => clearTimeout(t);
  }, [done, navigate]);

  const pending = finishMutation.isPending;

  if (done) {
    return (
      <div className="flex h-full flex-col items-center justify-center gap-4 px-8 py-10 text-center">
        <img
          alt=""
          aria-hidden="true"
          className="size-16 animate-fade-in object-contain"
          src={overmindEye}
        />
        <h2 className={cn(TITLE.card, "animate-fade-in-up text-auth-text")}>You&apos;re all set</h2>
      </div>
    );
  }

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="flex min-h-0 flex-1 flex-col gap-4 overflow-y-auto px-7 pb-5 pt-6">
        <div className="shrink-0">
          <h2 className={cn(TITLE.card, "text-auth-text")}>Set up from your repository</h2>
          <p className="mt-1 text-sm text-auth-text-label">
            Copy the prompt into your coding agent. The console project is created when you run{" "}
            <span className="font-mono text-auth-text">overmind sync</span>.
          </p>
        </div>

        {submitError && <ErrorBanner message={submitError} />}

        <OnboardWithAiPanel showManualSetup={false} />
      </div>

      <div className="flex shrink-0 items-center justify-end border-t border-auth-border px-7 py-4">
        <Button
          className="gap-2"
          disabled={pending}
          onClick={() => finishMutation.mutate()}
          type="button"
        >
          {pending ? <Spinner className="text-current" size="sm" /> : null}
          {finishMutation.isError ? "Retry" : "Go to console"}
          {pending || finishMutation.isError ? null : <Icon.forward />}
        </Button>
      </div>
    </div>
  );
}
