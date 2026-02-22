import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import apiClient from "@/client";

export function useTokensList(projectId?: string) {
  return useQuery({
    enabled: !!projectId,
    queryFn: async () => {
      const res = await apiClient.tokens.listTokensApiV1IamTokensGet({
        projectId: projectId!,
      });
      return res;
    },
    queryKey: ["tokens", projectId],
  });
}

export function useDeleteToken() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (tokenId: string) =>
      apiClient.tokens.deleteTokenApiV1IamTokensTokenIdDelete({ tokenId }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["tokens"] });
    },
  });
}
