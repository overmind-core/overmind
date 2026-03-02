import { useQuery } from "@tanstack/react-query";

import apiClient from "@/client";
import { useOrganisationId } from "./use-query";

export function useProjectsList() {
  const organisationId = useOrganisationId();
  return useQuery({
    queryFn: () =>
      apiClient.projects.listUserProjectsApiV1IamProjectsGet(
        organisationId ? { organisationId } : {}
      ),
    queryKey: ["projects", organisationId],
  });
}
