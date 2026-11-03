import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { ApiError } from "@/lib/api-error";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      retry: (count, error) =>
        count < 1 &&
        error.name !== "AbortError" &&
        !(error instanceof ApiError && error.status === 401),
      staleTime: 60 * 1000,
    },
  },
});

export function getContext() {
  return {
    auth: { isLoaded: false, isSignedIn: false },
    queryClient,
  };
}

export function RootQueryProvider({ children }: { children: React.ReactNode }) {
  return <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>;
}
