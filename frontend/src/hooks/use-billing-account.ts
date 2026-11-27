import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import api from "@/client";
import { useOnboardingStatus } from "@/hooks/use-query";

export const billingAccountQueryKey = ["billing-account"] as const;
export const billingLedgerQueryKey = ["billing-ledger"] as const;

export function useBillingAccount(options?: { enabled?: boolean; refetchInterval?: number }) {
  return useQuery({
    enabled: options?.enabled ?? true,
    queryFn: () => api.billing.billingAccountRetrieve(),
    queryKey: billingAccountQueryKey,
    refetchInterval: options?.refetchInterval,
  });
}

/** Remaining-credit billing is on only when the API injected Stripe. */
export function useCommercialBilling() {
  const { data: me, isSuccess } = useOnboardingStatus();
  return { enabled: me?.billingEnabled === true, ready: isSuccess };
}

export function useBillingLedgerQuery(page = 1, pageSize = 25, ordering?: string) {
  return useQuery({
    queryFn: () => api.billing.billingLedgerList({ ordering, page, pageSize }),
    queryKey: [...billingLedgerQueryKey, page, pageSize, ordering],
  });
}

/**
 * `hasCredits` mirrors the server gate exactly: `ensure_credits` rejects only a
 * non-positive balance, it does not price the job. An unresolved balance counts
 * as having credits — never block an action on a query that hasn't answered.
 */
export function useCredits() {
  const { enabled } = useCommercialBilling();
  const { data, isLoading } = useBillingAccount();
  const balanceUsd = data ? Math.max(0, Number(data.creditsUsd) || 0) : undefined;
  return {
    balanceUsd,
    hasCredits: !enabled || balanceUsd === undefined || balanceUsd > 0,
    isLoading,
  };
}

/** Resolves to a Stripe Checkout URL to redirect to. */
export function useTopUpCredits() {
  return useMutation({
    mutationFn: (amountUsd: number) =>
      api.billing.billingTopupCreate({ creditTopUpRequestRequest: { amountUsd } }),
  });
}

/** Grants a paid Checkout session on return, so the balance does not wait for the webhook. */
export function useConfirmTopUp() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (sessionId: string) =>
      api.billing.billingTopupConfirmCreate({
        creditTopUpConfirmRequestRequest: { sessionId },
      }),
    onSuccess: (data) => {
      queryClient.setQueryData(billingAccountQueryKey, data.account);
      void queryClient.invalidateQueries({ queryKey: billingLedgerQueryKey });
    },
  });
}
