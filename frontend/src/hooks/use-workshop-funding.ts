import { useIsMutating, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import api from "@/client";
import type { PatchedWorkshopFundingRequestRequest } from "@/openapi";

export const workshopFundingKey = ["workshop-funding"] as const;

export function useWorkshopFunding() {
  const isChanging = useIsMutating({ mutationKey: workshopFundingKey }) > 0;
  const query = useQuery({
    enabled: import.meta.env.VITE_SELF_HOSTED === "true",
    queryFn: () => api.chatgpt.chatgptRetrieve(),
    queryKey: workshopFundingKey,
    staleTime: 15_000,
  });
  return { ...query, isChanging };
}

export function useChatGPTModels(accountId: string | undefined, enabled: boolean) {
  return useQuery({
    enabled: enabled && !!accountId,
    queryFn: () => api.chatgpt.chatgptModelsList({ accountId: accountId! }),
    queryKey: ["chatgpt-models", accountId],
    retry: false,
    staleTime: 60_000,
  });
}

export function useSaveWorkshopFunding() {
  const cache = useQueryClient();
  return useMutation({
    mutationFn: (value: PatchedWorkshopFundingRequestRequest) =>
      api.chatgpt.chatgptPartialUpdate({ patchedWorkshopFundingRequestRequest: value }),
    mutationKey: workshopFundingKey,
    onSuccess: (value) => cache.setQueryData(workshopFundingKey, value),
  });
}
