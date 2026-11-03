import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import apiClient from "@/client";
import { notify } from "@/lib/notify";
import type { ActionEnum, IssuesRankedRetrieveRequest } from "@/openapi";

type IssuesRankedRetrieveCauseEnum = IssuesRankedRetrieveRequest["cause"];

export type IssueScope = {
  projectId: string;
  range: string;
  capability?: string;
};

const params = ({ projectId, range, capability }: IssueScope) => ({
  capability,
  project: projectId,
  range,
});

export const useObservabilityOverview = (scope: IssueScope) =>
  useQuery({
    placeholderData: keepPreviousData,
    queryFn: () => apiClient.issues.issuesOverviewRetrieve(params(scope)),
    queryKey: ["issues", "overview", scope],
  });

export const useObservabilityTrends = (scope: IssueScope) =>
  useQuery({
    placeholderData: keepPreviousData,
    queryFn: () => apiClient.issues.issuesTrendsRetrieve(params(scope)),
    queryKey: ["issues", "trends", scope],
  });

export const useIssuePage = (
  scope: IssueScope,
  page: { cause?: IssuesRankedRetrieveCauseEnum; page: number; pageSize: number },
  enabled: boolean
) =>
  useQuery({
    enabled,
    placeholderData: keepPreviousData,
    queryFn: () => apiClient.issues.issuesRankedRetrieve({ ...params(scope), ...page }),
    queryKey: ["issues", "rows", scope, page],
  });

export const useTrainingPlan = (projectId: string, capability: string | undefined, range: string) =>
  useQuery({
    enabled: !!capability,
    queryFn: () =>
      apiClient.issues.issuesTrainingPlanRetrieve({
        capability: capability as string,
        project: projectId,
        range,
      }),
    queryKey: ["issues", "training-plan", projectId, capability, range],
  });

export const useTraceIssues = (projectId: string, trace: string) =>
  useQuery({
    queryFn: () => apiClient.issues.issuesForTraceRetrieve({ project: projectId, trace }),
    queryKey: ["issues", "for-trace", projectId, trace],
  });

export const useCapabilityActivity = (scope: IssueScope, enabled: boolean) =>
  useQuery({
    enabled,
    queryFn: () => apiClient.issues.issuesCapabilitiesRetrieve(params(scope)),
    queryKey: ["issues", "capabilities", scope],
  });

export const useIssueDetail = (id: string | undefined, range: string) =>
  useQuery({
    enabled: !!id,
    queryFn: () => apiClient.issues.issuesRetrieve({ id: id as string, range }),
    queryKey: ["issues", "detail", id, range],
  });

export const useIssueState = (id: string) => {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (action: ActionEnum) =>
      apiClient.issues.issuesStateCreate({ id, issueStateRequest: { action } }),
    onError: (error) => notify.error(error, "Couldn't update the issue"),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["issues"] }),
  });
};
