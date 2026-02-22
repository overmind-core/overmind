import { createFileRoute, Link, Navigate } from "@tanstack/react-router";
import { KeyRound, Sparkles } from "lucide-react";

import { APIKeySection } from "@/components/api-keys";
import { MoreAboutUser } from "@/components/more-about-user";
import { Button } from "@/components/ui/button";
import { useOnboardingQuery } from "@/hooks/use-query";
import { onboardingSearchSchema } from "@/lib/schemas";
import { cn } from "@/lib/utils";

export const Route = createFileRoute("/_auth/onboarding")({
  component: OnboardingPage,
  validateSearch: onboardingSearchSchema,
});

function OnboardingPage() {
  const { isLoading, data } = useOnboardingQuery();
  const { step } = Route.useSearch();

  if (isLoading) {
    return (
      <div className="page-wrapper">
        <div className="text-center text-muted-foreground">Loading...</div>
      </div>
    );
  }
  if (!isLoading && data) {
    return <Navigate replace to="/" />;
  }

  return (
    <div className="page-wrapper">
      <div className="mx-auto w-full  space-y-6">
        {step === "2" ? (
          <>
            <OnboardingStep2 />
            <StepIndicator currentStep={2} />
          </>
        ) : (
          <>
            <OnboardingStep1 />
            <StepIndicator currentStep={1} />
          </>
        )}
      </div>
    </div>
  );
}

function OnboardingStep1() {
  return (
    <div className="space-y-6">
      <div className="text-center">
        <div className="mb-3 inline-flex items-center justify-center rounded-full bg-primary/10 p-3">
          <KeyRound className="size-6 text-primary" />
        </div>
        <h1 className="text-2xl font-bold tracking-tight">Set up your API key</h1>
        <p className="mt-2 text-muted-foreground">
          Generate an API key to start tracing your AI agent with Overmind.
        </p>
      </div>
      <APIKeySection />
      <div className="mt-6 flex justify-end">
        <Button asChild size="lg">
          <Link search={{ step: "2" }} to="/onboarding">
            Continue
          </Link>
        </Button>
      </div>
    </div>
  );
}

function OnboardingStep2() {
  return (
    <div className="space-y-6">
      <div className="text-center">
        <div className="mb-3 inline-flex items-center justify-center rounded-full bg-primary/10 p-3">
          <Sparkles className="size-6 text-primary" />
        </div>
        <h1 className="text-2xl font-bold tracking-tight">Almost there</h1>
        <p className="mt-2 text-muted-foreground">
          Tell us about your AI agent so we can tailor Overmind for you.
        </p>
      </div>

      <div className="flex flex-col gap-4">
        <div className="flex justify-start">
          <Button asChild size="sm" variant="ghost">
            <Link search={{ step: "1" }} to="/onboarding">
              ← Back
            </Link>
          </Button>
        </div>
        <MoreAboutUser />
      </div>
    </div>
  );
}
function StepIndicator({ currentStep }: { currentStep: 1 | 2 }) {
  return (
    <div className="mb-8  flex items-center justify-center gap-2">
      <div
        className={cn(
          "flex h-9 w-9 items-center justify-center rounded-full text-sm font-semibold transition-colors",
          currentStep === 1 ? "bg-primary text-primary-foreground" : "bg-primary/20 text-primary"
        )}
      >
        1
      </div>
      <div className="h-0.5 w-8 bg-border" />
      <div
        className={cn(
          "flex h-9 w-9 items-center justify-center rounded-full text-sm font-semibold transition-colors",
          currentStep === 2
            ? "bg-primary text-primary-foreground"
            : "bg-muted text-muted-foreground"
        )}
      >
        2
      </div>
    </div>
  );
}
