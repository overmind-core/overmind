import { useQuery } from "@tanstack/react-query";

import apiClient from "@/client";

export function usePromptsList(projectId: string | undefined) {
  return useQuery({
    enabled: !!projectId,
    queryFn: () => apiClient.prompts.listPromptsApiV1PromptsGet({ projectId: projectId! }),
    queryKey: ["prompts", projectId],
  });
}
