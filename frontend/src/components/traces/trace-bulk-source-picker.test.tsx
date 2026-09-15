// @vitest-environment jsdom
/**
 * The selection is resolved server-side against EVERY project the user belongs to,
 * so it must carry its own project scope — the list request passes the project as
 * its own argument, not as a filter.
 */
import type { ReactNode } from "react";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  TraceBulkSourcePicker,
  type TraceSelectionState,
} from "@/components/traces/trace-bulk-source-picker";

const mocks = vi.hoisted(() => ({ capabilitiesList: vi.fn(), tracesList: vi.fn() }));

vi.mock("@/client", () => ({
  default: {
    capabilities: { capabilitiesList: mocks.capabilitiesList },
    traces: { tracesList: mocks.tracesList },
  },
}));

const PROJECT_ID = "00000000-0000-4000-8000-000000000001";

describe("TraceBulkSourcePicker", () => {
  let queryClient: QueryClient;
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  );

  beforeEach(() => {
    queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    mocks.capabilitiesList.mockReset().mockResolvedValue({ results: [] });
    mocks.tracesList.mockReset().mockResolvedValue({ count: 100, results: [] });
  });

  afterEach(() => {
    cleanup();
    vi.clearAllMocks();
  });

  it("reports a ready selection scoped to its own project once the count lands", async () => {
    const onChange = vi.fn();

    render(<TraceBulkSourcePicker onChange={onChange} projectId={PROJECT_ID} />, { wrapper });

    expect(await screen.findByText("100")).toBeTruthy();
    const states = onChange.mock.calls.map((c) => c[0] as TraceSelectionState);
    expect(states[0]?.status).toBe("counting");
    const last = states.at(-1);
    expect(last?.status).toBe("ready");
    if (last?.status !== "ready") return;
    expect(last.count).toBe(100);
    expect(last.selection.filters?.project).toBe(PROJECT_ID);
  });
});
