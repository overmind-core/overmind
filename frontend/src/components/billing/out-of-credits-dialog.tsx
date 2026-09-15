import { useEffect, useState } from "react";

import { useQueryClient } from "@tanstack/react-query";

import { TopUpDialog } from "@/components/billing/top-up-dialog";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { CreditsAmount } from "@/components/ui/credits";
import { useAuthContext } from "@/contexts/auth-context";
import { subscriptionQueryKey, useSubscriptionQuery } from "@/hooks/use-subscription";
import { hasCreditsSurface, onPaymentRequired, type PaymentRequiredContext } from "@/lib/credits";

/**
 * The single surface for a 402: mounted once in the `_auth` layout, opened by
 * any credit-gated request failing. `client.ts` publishes the event.
 */
export function OutOfCreditsDialog() {
  const [context, setContext] = useState<PaymentRequiredContext | null>(null);
  const [topUpOpen, setTopUpOpen] = useState(false);
  const { isSignedIn } = useAuthContext();
  const { data: sub } = useSubscriptionQuery({ enabled: isSignedIn });
  const queryClient = useQueryClient();

  useEffect(
    () =>
      onPaymentRequired((next) => {
        // A surface already explaining the empty balance in place owns this.
        if (hasCreditsSurface()) return;
        // Concurrent gated requests emit once each; keep the first one's action
        // so the copy doesn't swap under the user mid-read.
        setContext((current) => current ?? next);
        // The cached balance predates the spend that just failed.
        queryClient.invalidateQueries({ queryKey: subscriptionQueryKey });
      }),
    [queryClient]
  );

  const action = context?.action;
  const balanceUsd = Math.max(0, Number(sub?.creditsUsd) || 0);

  return (
    <>
      <ConfirmDialog
        cancelLabel="Close"
        confirmLabel="Add credits"
        description={
          <>
            {action ? `You need credits to ${action}.` : "You need credits to run this."} Your
            balance is <CreditsAmount className="text-foreground" usd={balanceUsd} />.
          </>
        }
        onConfirm={() => {
          setContext(null);
          setTopUpOpen(true);
        }}
        onOpenChange={(next) => !next && setContext(null)}
        open={context !== null}
        title="Out of credits"
      />
      <TopUpDialog onOpenChange={setTopUpOpen} open={topUpOpen} />
    </>
  );
}
