import { useQuery } from "@tanstack/react-query";

export function useOrganisationsList() {
  return useQuery({
    queryFn: () => Promise.resolve({ organisations: [] }),
    queryKey: ["organisations"],
  });
}
