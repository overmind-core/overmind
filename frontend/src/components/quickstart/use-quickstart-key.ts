import { useEffect, useMemo } from "react";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import apiClient from "@/client";
import { config } from "@/config";
import { useProjectsList } from "@/hooks/use-projects";

const STORAGE_PREFIX = "telemetry_api_key_";

export function useQuickstartKey(projectId?: string) {
  const queryClient = useQueryClient();
  const { data: projectsData, isLoading: projectsLoading } = useProjectsList();

  const currentProject = useMemo(() => {
    if (projectId) {
      return projectsData?.projects?.find((p) => p.projectId === projectId);
    }
    return projectsData?.projects?.[0];
  }, [projectsData, projectId]);

  const storageKey = currentProject ? `${STORAGE_PREFIX}${currentProject.projectId}` : null;

  const {
    data: apiKey,
    isLoading: keyLoading,
    refetch: refetchKey,
  } = useQuery({
    enabled: !!storageKey,
    queryFn: () => {
      if (!storageKey) return null;
      return localStorage.getItem(storageKey) ?? null;
    },
    queryKey: ["quickstart_api_key", currentProject?.projectId],
    staleTime: Number.POSITIVE_INFINITY,
  });

  const generateMutation = useMutation({
    mutationFn: async () => {
      if (!currentProject) throw new Error("No project available");

      const tokenResponse = await apiClient.tokens.createTokenApiV1IamTokensPost({
        createTokenRequest: {
          description: "API key for telemetry",
          name: `Telemetry API Key - ${new Date().toISOString()}`,
          organisationId: currentProject.organisationId,
          projectId: currentProject.projectId,
        },
      });

      if (!config.isSelfHosted) {
        const rolesRes = await apiClient.roles.listCoreRolesApiV1IamRolesGet({
          scope: "project",
        });
        const adminRole = rolesRes.roles?.find((r) => r.name === "project_admin");
        if (!adminRole) throw new Error("Admin role not found");

        await apiClient.tokenRoles.assignTokenRoleApiV1IamTokensTokenIdRolesPost({
          assignTokenRoleRequest: {
            expiresAt: new Date(Date.now() + 1000 * 60 * 60 * 24 * 300),
            roleId: adminRole.roleId,
            scopeId: currentProject.projectId,
            scopeType: "project",
          },
          tokenId: tokenResponse.tokenId,
        });
      }

      localStorage.setItem(`${STORAGE_PREFIX}${currentProject.projectId}`, tokenResponse.token);

      return tokenResponse.token;
    },
    onSuccess: () => {
      refetchKey();
      queryClient.invalidateQueries({ queryKey: ["tokens"] });
    },
  });

  useEffect(() => {
    if (
      !projectsLoading &&
      !keyLoading &&
      currentProject &&
      apiKey === null &&
      !generateMutation.isPending &&
      !generateMutation.isError
    ) {
      generateMutation.mutate();
    }
  }, [projectsLoading, keyLoading, currentProject, apiKey, generateMutation]);

  return {
    apiKey: apiKey ?? null,
    isLoading: projectsLoading || keyLoading || generateMutation.isPending,
    isError: generateMutation.isError,
    error: generateMutation.error,
    retry: () => generateMutation.mutate(),
  };
}
