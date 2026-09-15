import { useQuery } from "@tanstack/react-query";

import apiClient from "@/client";
import { isExperimentLive } from "@/components/optimiser/experiment-status";

export const useOptimizerExperimentsQuery = (projectId: string | undefined) =>
  useQuery({
    enabled: !!projectId,
    queryFn: () =>
      apiClient.optimizerExperiments.optimizerExperimentsList({
        pageSize: 100,
      }),
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
