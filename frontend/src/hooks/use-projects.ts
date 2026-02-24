import { useQuery } from "@tanstack/react-query";

import apiClient from "@/client";

export function useProjectsList(organisationId?: string) {
  return useQuery({
    queryFn: () =>
      apiClient.projects.listUserProjectsApiV1IamProjectsGet(
        organisationId ? { organisationId } : {}
      ),
    queryKey: ["projects", organisationId],
  });
}
