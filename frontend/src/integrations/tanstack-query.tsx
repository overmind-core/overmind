import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { config } from "../config";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      retry: 1,
      staleTime: 60 * 1000,
    },
  },
});

export function getContext() {
  return {
    authUser: undefined,
    config: config,
    queryClient,
  };
}

export function RootQueryProvider({ children }: { children: React.ReactNode }) {
  return <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>;
}
