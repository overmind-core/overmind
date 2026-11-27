import { useEffect, useState } from "react";

import { CreditsAmount } from "@/components/ui/credits";
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip";
import { useBillingAccount, useCommercialBilling } from "@/hooks/use-billing-account";
import { onCreditsDelta } from "@/lib/credits-delta";
import { cn } from "@/lib/utils";

const LOW_BALANCE_USD = 5;
/* Must match the credits-delta-bubble animation duration in styles.css. */
const DELTA_MS = 2500;

export function HeaderCredits() {
  const { enabled: commercial, ready } = useCommercialBilling();
  // Always mounted, so focus-refetch alone goes stale.
  const { data } = useBillingAccount({ refetchInterval: 60_000 });
  // seq keys the bubble so a reward landing mid-animation restarts it.
  const [delta, setDelta] = useState<{ credits: number; seq: number } | null>(null);

  useEffect(() => {
    let clearTimer: number | undefined;
    const unsubscribe = onCreditsDelta((credits) => {
      window.clearTimeout(clearTimer);
      setDelta((prev) => ({ credits, seq: (prev?.seq ?? 0) + 1 }));
      clearTimer = window.setTimeout(() => setDelta(null), DELTA_MS);
    });
    return () => {
      unsubscribe();
      window.clearTimeout(clearTimer);
    };
  }, []);

  if (!ready || !data) return null;

  if (!commercial) {
    const spent = Math.max(0, Number(data.creditsSpentUsd) || 0);
    return (
      <TooltipProvider delayDuration={150}>
        <Tooltip>
          <TooltipTrigger asChild>
            <span
              aria-label="Usage accrued on this account"
              className="relative inline-flex h-8 cursor-help select-none items-center px-1 text-sm leading-none text-foreground [&_svg]:text-muted-foreground"
              role="status"
            >
              <CreditsAmount usd={spent} />
            </span>
          </TooltipTrigger>
          <TooltipContent className="max-w-xs text-pretty" side="bottom" sideOffset={6}>
            Usage accrued on this account.
          </TooltipContent>
        </Tooltip>
      </TooltipProvider>
    );
  }

  const balance = Math.max(0, Number(data.creditsUsd) || 0);
  const out = balance <= 0;
  const low = !out && balance < LOW_BALANCE_USD;

  const detail = out
    ? "You're out of credits. Add credits in Settings."
    : low
      ? "Credits are low. Add credits in Settings."
      : "Credits pay for training, inference and analysis.";

  return (
    <TooltipProvider delayDuration={150}>
      <Tooltip>
        <TooltipTrigger asChild>
          {/* The state sentence rides the aria-label: colour and the hover-only tooltip
              are invisible to assistive tech. */}
          <span
            aria-label={`Credits balance: ${detail}`}
            className={cn(
              "relative inline-flex h-8 cursor-help select-none items-center px-1 text-sm leading-none",
              "[&_svg]:text-muted-foreground",
              out ? "text-destructive" : low ? "text-warning" : "text-foreground"
            )}
            role="status"
          >
            <CreditsAmount usd={balance} />
            {delta != null ? (
              /* Deliberately not aria-hidden — the role="status" parent announces
                 the addition. */
              <span
                className="animate-credits-delta pointer-events-none absolute right-0 top-full z-50 mt-2 w-max rounded-md bg-foreground px-3 py-1.5 text-xs font-medium tabular-nums text-background"
                key={delta.seq}
              >
                <span className="absolute -top-1 right-3 size-2.5 rotate-45 rounded-sm bg-foreground" />
                +{delta.credits.toLocaleString("en-US")} credits
              </span>
            ) : null}
          </span>
        </TooltipTrigger>
        <TooltipContent className="max-w-xs text-pretty" side="bottom" sideOffset={6}>
          {detail}
        </TooltipContent>
      </Tooltip>
    </TooltipProvider>
  );
}
