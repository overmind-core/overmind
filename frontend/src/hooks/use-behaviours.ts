import { useQueries, useQuery } from "@tanstack/react-query";

import apiClient from "@/client";
import type { Behaviour, PaginatedBehaviourList } from "@/openapi";

export type { BehaviourCoverageEntry } from "@/openapi";

// The server clamps pageSize to 100 and the list spans every project of the
// membership, so one page silently truncates; walk the pages.
const BEHAVIOURS_API_PAGE_SIZE = 100;
const BEHAVIOURS_MAX_PAGES = 20;

const fetchAllBehaviours = async (): Promise<PaginatedBehaviourList> => {
  const results: Behaviour[] = [];
  let page = 1;
  let count = 0;
  while (page <= BEHAVIOURS_MAX_PAGES) {
    const data = await apiClient.behaviours.behavioursList({
      page,
      pageSize: BEHAVIOURS_API_PAGE_SIZE,
    });
    count = data.count;
    results.push(...data.results);
    if (!data.next || results.length >= count || data.results.length === 0) break;
    page += 1;
  }
  return { count, next: null, previous: null, results };
};

/** List is membership-scoped server-side with no project param; consumers
 *  narrow to the project's capabilities themselves. */
export const useBehavioursQuery = (enabled: boolean) =>
  useQuery({
    enabled,
    queryFn: fetchAllBehaviours,
    queryKey: ["behaviours"],
  });

export const useBehaviourCoverageQueries = (capabilityIds: string[]) =>
  useQueries({
    queries: capabilityIds.map((capabilityId) => ({
      queryFn: () => apiClient.behaviours.behavioursCoverageRetrieve({ capability: capabilityId }),
      queryKey: ["behaviour-coverage", capabilityId],
    })),
  });

export const useBehaviourEvaluatorsQuery = (behaviourId: string, enabled: boolean) =>
  useQuery({
    enabled,
    queryFn: () => apiClient.behaviours.behavioursEvaluatorsList({ id: behaviourId }),
    queryKey: ["behaviour-evaluators", behaviourId],
  });

/** Preloaded context for task-scoped eval authoring: the behaviour's latest
 *  contract, so the picker can list the steps a judge may target. */
export const useBehaviourAuthoringContextQuery = (behaviourId: string, enabled: boolean) =>
  useQuery({
    enabled,
    queryFn: () => apiClient.behaviours.behavioursAuthoringContextRetrieve({ id: behaviourId }),
    queryKey: ["behaviour-authoring-context", behaviourId],
  });
