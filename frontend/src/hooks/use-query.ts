import { useOrganization } from "@clerk/clerk-react";
import { useQuery } from "@tanstack/react-query";

import apiClient from "@/client";
import { config } from "@/config";

export const useOrganisationId = () => {
  // biome-ignore lint/correctness/useHookAtTopLevel: useOrganization only valid when Clerk is enabled
  return config.clerkReady ? (useOrganization().organization?.id ?? "") : "";
};

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
