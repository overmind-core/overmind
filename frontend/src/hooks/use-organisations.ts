import { useQuery } from "@tanstack/react-query";

import apiClient from "@/client";

export function useOrganisationsList() {
  return useQuery({
    queryFn: () => apiClient.organisations.listUserOrganisationsApiV1IamOrganisationsGet(),
    queryKey: ["organisations"],
  });
}
