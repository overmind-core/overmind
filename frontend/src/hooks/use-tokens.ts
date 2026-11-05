import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import apiClient from "@/client";
import { notify } from "@/lib/notify";

export function useTokensList(projectId?: string) {
  return useQuery({
    enabled: !!projectId,
    queryFn: async ({ signal }) => {
      const pid = projectId ?? "";
      const tokens = [];
      for (let page = 1; ; page++) {
        const res = await apiClient.auth.authApiKeysList({ page }, { signal });
        tokens.push(...res.results);
        if (!res.next) break;
      }
      return {
        tokens: tokens
          .filter(
            (k) =>
              k.project === pid ||
              (k.scope.scope === "project" && k.scope.resourceIds?.includes(pid))
          )
          .map((k) => ({ ...k, projectId: k.project ?? pid, tokenId: k.id })),
      };
    },
    queryKey: ["tokens", projectId],
  });
}

export function useDeleteToken() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (tokenId: string) => apiClient.auth.authApiKeysDestroy({ id: tokenId }),
    onError: (e) => notify.error(e, "Couldn't delete API key"),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["tokens"] });
      notify.success("API key deleted");
    },
  });
}
