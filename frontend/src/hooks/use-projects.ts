import { useQuery } from "@tanstack/react-query";

import apiClient from "@/client";

export function useProjectsList(options?: { pollWhileEmpty?: boolean }) {
  return useQuery({
    queryFn: async () => {
      const pageSize = 100;
      const first = await apiClient.projects.projectsList({
        ordering: "name",
        page: 1,
        pageSize,
      });
      const results = [...(first.results ?? [])];
      const total = first.count ?? results.length;
      for (let page = 2; results.length < total; page += 1) {
        const next = await apiClient.projects.projectsList({
          ordering: "name",
          page,
          pageSize,
        });
        const batch = next.results ?? [];
        if (batch.length === 0) break;
        results.push(...batch);
      }
      return {
        projects: results.map((proj) => ({
          ...proj,
          projectId: proj.id,
        })),
      };
    },
    queryKey: ["projects"],
    refetchInterval: options?.pollWhileEmpty
      ? (query) => ((query.state.data?.projects ?? []).length === 0 ? 5000 : false)
      : false,
  });
}
