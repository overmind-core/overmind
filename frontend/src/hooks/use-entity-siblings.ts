import { useQuery } from "@tanstack/react-query";

import apiClient from "@/client";

export interface EntitySibling {
  id: string;
  label: string;
}

// Traces and jobs are excluded on purpose: high-cardinality, paginated,
// filter-driven lists that a flat dropdown suits poorly.
export const SWITCHABLE_KINDS = new Set(["capabilities", "projects", "datasets"]);

export function useEntitySiblings(kind: string | undefined, projectId: string | undefined) {
  const scopedToProject = kind === "capabilities" || kind === "datasets";
  return useQuery<EntitySibling[]>({
    enabled: !!kind && SWITCHABLE_KINDS.has(kind) && (!scopedToProject || !!projectId),
    queryFn: async (): Promise<EntitySibling[]> => {
      switch (kind) {
        case "capabilities": {
          const res = await apiClient.capabilities.capabilitiesList({ project: projectId });
          return (res.results ?? []).map((a) => ({ id: a.id, label: a.name }));
        }
        case "projects": {
          const res = await apiClient.projects.projectsList({});
          return (res.results ?? []).map((p) => ({ id: p.id, label: p.name }));
        }
        case "datasets": {
          const res = await apiClient.datasets.datasetsList({
            pageSize: 200,
            project: projectId,
          });
          return (res.results ?? []).map((d) => ({
            id: d.id,
            label: d.name?.trim() || `Dataset ${d.id.slice(0, 8)}`,
          }));
        }
        default:
          return [];
      }
    },
    queryKey: ["entity-siblings", kind, projectId],
    staleTime: 30_000,
  });
}
