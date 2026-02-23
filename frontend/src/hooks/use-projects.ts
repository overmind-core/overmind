import { useQuery } from "@tanstack/react-query";

import apiClient from "@/client";

export function useProjectsList() {
  return useQuery({
    queryFn: () => apiClient.projects.listUserProjectsApiV1IamProjectsGet({}),
    queryKey: ["projects"],
  });
}
