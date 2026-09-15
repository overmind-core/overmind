import { useEffect } from "react";

import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { CreditsAmount } from "@/components/ui/credits";
import { Icon } from "@/components/ui/icons";
import { useCredits } from "@/hooks/use-subscription";
import { claimCreditsSurface } from "@/lib/credits";
import { PUBLIC_PRICING_URL } from "@/lib/marketing";
import { cn } from "@/lib/utils";

/** While one of these is on screen, the global dialog stays quiet. */
function useCreditsSurfaceClaim(active: boolean) {
  useEffect(() => (active ? claimCreditsSurface() : undefined), [active]);
}

function ViewPlansButton({ size = "sm" }: { size?: "sm" | "default" }) {
  return (
    <Button asChild className="shrink-0" size={size} variant="secondary">
      <a href={PUBLIC_PRICING_URL} rel="noreferrer" target="_blank">
        <Icon.zap />
        View plans
      </a>
    </Button>
  );
}

export function CreditsRequiredAlert({
  action,
  className,
}: {
  /** Verb phrase for the blocked work, e.g. "start a training run". */
  action: string;
  className?: string;
}) {
  const { balanceUsd, hasCredits } = useCredits();
  useCreditsSurfaceClaim(!hasCredits);
  if (hasCredits) return null;

  return (
    // Stacked, not a row: the finetuning summary rail is ~320px wide.
    // `bg-warning/10` completes the tint triplet (DESIGN.md); the Alert variant
    // ships border + text only.
    <Alert
      className={cn("flex flex-col items-start gap-2 bg-warning/10", className)}
      variant="warning"
    >
      {/* Wrapped: bare `>svg` children get absolutely positioned by alertVariants. */}
      <span className="flex items-center gap-2 font-medium">
        <Icon.credits className="size-4 shrink-0" />
        Out of credits
      </span>
      <p className="text-muted-foreground">
        You need credits to {action}. Your balance is{" "}
        <CreditsAmount className="text-foreground" usd={balanceUsd ?? 0} />.
      </p>
      <ViewPlansButton />
    </Alert>
  );
}
