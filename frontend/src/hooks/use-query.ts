import { useQuery } from "@tanstack/react-query";

import apiClient from "@/client";

export const useOnboardingQuery = () => {
  return useQuery({
    queryFn: () => apiClient.onboarding.getUserOnboardingApiV1OnboardingGet(),
    queryKey: ["onboarding"],
  });
};

export const useAgentDetailQuery = (slug: string, projectId?: string) => {
  return useQuery({
    queryFn: () =>
      apiClient.agents.getAgentDetailApiV1AgentsPromptSlugDetailGet({
        promptSlug: slug,
        projectId,
      }),
    queryKey: ["agent-detail", slug, projectId],
    refetchInterval: 15_000,
  });
};

export const useProjectQuery = (projectId: string) => {
  return useQuery({
    queryFn: () => apiClient.projects.getProjectApiV1IamProjectsProjectIdGet({ projectId }),
    queryKey: ["project", projectId],
  });
};
