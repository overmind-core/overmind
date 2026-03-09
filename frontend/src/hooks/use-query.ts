import { useQuery } from "@tanstack/react-query";

import apiClient from "@/client";
import { useAuthContext } from "@/contexts/auth-context";

export const useOrganisationId = () => useAuthContext().organisationId;

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
        projectId,
        promptSlug: slug,
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
