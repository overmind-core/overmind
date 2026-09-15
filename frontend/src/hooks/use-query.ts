import { useQuery } from "@tanstack/react-query";

import apiClient from "@/client";
export const useOnboardingStatus = (enabled = true) => {
  return useQuery({
    enabled,
    queryFn: () => apiClient.auth.authMeRetrieve(),
    queryKey: ["auth-me"],
    staleTime: 60_000,
  });
};

export const useCapabilityDetailQuery = (capabilityId: string) => {
  return useQuery({
    enabled: !!capabilityId,
    queryFn: () => apiClient.capabilities.capabilitiesRetrieve({ id: capabilityId }),
    queryKey: ["capability-detail", capabilityId],
    refetchInterval: 15_000,
  });
};

// No remapping: several callers fill ["project", id] with the raw API shape,
// and whichever shape lands first is what every consumer reads.
export const useProjectQuery = (projectId: string) => {
  return useQuery({
    queryFn: () => apiClient.projects.projectsRetrieve({ id: projectId }),
    queryKey: ["project", projectId],
  });
};
