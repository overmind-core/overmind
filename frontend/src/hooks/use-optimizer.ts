import { useQuery } from "@tanstack/react-query";

import apiClient from "@/client";
import { isExperimentLive } from "@/components/optimiser/experiment-status";

export const useOptimizerExperimentsQuery = (projectId: string | undefined) =>
  useQuery({
    enabled: !!projectId,
    queryFn: async ({ signal }) => {
      const results = [];
      for (let page = 1; ; page++) {
        const data = await apiClient.optimizerExperiments.optimizerExperimentsList(
          { page, pageSize: 100, project: projectId },
          { signal }
        );
        results.push(...data.results);
        if (!data.next) return { ...data, next: null, previous: null, results };
      }
    },
    queryKey: ["optimizer-experiments", projectId],
    refetchInterval: (query) => {
      const rows = query.state.data?.results ?? [];
      return rows.some((e) => e.project === projectId && isExperimentLive(e.status))
        ? 5_000
        : false;
    },
    select: (data) => ({
      ...data,
      results: (data.results ?? []).filter((e) => e.project === projectId),
    }),
  });
