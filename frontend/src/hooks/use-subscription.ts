import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import api from "@/client";
import { useOnboardingStatus } from "@/hooks/use-query";
import type { Subscription } from "@/openapi";

export const subscriptionQueryKey = ["billing-subscription"] as const;
export const billingLedgerQueryKey = ["billing-ledger"] as const;

export function useSubscriptionQuery(options?: { enabled?: boolean; refetchInterval?: number }) {
  return useQuery({
    enabled: options?.enabled ?? true,
    queryFn: () => api.billing.billingSubscriptionRetrieve(),
    queryKey: subscriptionQueryKey,
    refetchInterval: options?.refetchInterval,
  });
}

/** Remaining-credit billing is on only when the API injected Stripe. */
export function useCommercialBilling() {
  const { data: me, isSuccess } = useOnboardingStatus();
  return { enabled: me?.billingEnabled === true, ready: isSuccess };
}

export function useBillingLedgerQuery(page = 1, pageSize = 25) {
  return useQuery({
    queryFn: () => api.billing.billingLedgerList({ page, pageSize }),
    queryKey: [...billingLedgerQueryKey, page, pageSize],
  });
}

export function useCancelSubscription() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () => api.billing.billingCancelCreate(),
    onSuccess: (data: Subscription) => {
      queryClient.setQueryData(subscriptionQueryKey, data);
    },
  });
}

export function useRenewSubscription() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () => api.billing.billingRenewCreate(),
    onSuccess: (data: Subscription) => {
      queryClient.setQueryData(subscriptionQueryKey, data);
    },
  });
}

/**
 * `hasCredits` mirrors the server gate exactly: `ensure_credits` rejects only a
 * non-positive balance, it does not price the job. An unresolved balance counts
 * as having credits — never block an action on a query that hasn't answered.
 */
export function useCredits() {
  const { enabled } = useCommercialBilling();
  const { data, isLoading } = useSubscriptionQuery();
  const balanceUsd = data ? Math.max(0, Number(data.creditsUsd) || 0) : undefined;
  return {
    balanceUsd,
    hasCredits: !enabled || balanceUsd === undefined || balanceUsd > 0,
    isLoading,
  };
}

export function useBuyPlan() {
  return useMutation({
    mutationFn: () => api.billing.billingCheckoutCreate(),
  });
}

/** Resolves to a Stripe Checkout URL to redirect to. */
export function useTopUpCredits() {
  return useMutation({
    mutationFn: (amountUsd: number) =>
      api.billing.billingTopupCreate({ creditTopUpRequestRequest: { amountUsd } }),
  });
}
