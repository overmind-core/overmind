// @vitest-environment jsdom
import type { ReactNode } from "react";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { EvaluatorsTab } from "@/components/capability-detail/EvaluatorsTab";
import type { Capability } from "@/openapi";

const mocks = vi.hoisted(() => ({
  useCapabilityEvalPreload: vi.fn(),
  useEvalSetsQuery: vi.fn(),
}));

vi.mock("@/hooks/use-capability-eval-preload", () => ({
  useCapabilityEvalPreload: mocks.useCapabilityEvalPreload,
}));

vi.mock("@/components/evaluations/evaluator-detail-dialog", () => ({
  EvaluatorDetailDialog: () => null,
}));

vi.mock("@/components/optimiser/run-locally-dialog", () => ({
  RunLocallyDialog: () => null,
}));

vi.mock("@/components/ui/lazy-chart", () => ({
  lazyChart: () => () => null,
}));

vi.mock("@tanstack/react-router", () => ({
  Link: ({ children }: { children: ReactNode }) => <a href="/">{children}</a>,
  useNavigate: () => vi.fn(),
}));

vi.mock("@/hooks/use-evaluations", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/hooks/use-evaluations")>();
  return {
    ...actual,
    useActivateEvalSetMutation: vi.fn(() => ({ isPending: false, mutateAsync: vi.fn() })),
    useAddEvalSetMembersMutation: vi.fn(() => ({ isPending: false, mutateAsync: vi.fn() })),
    useCreateEvalSetMutation: vi.fn(() => ({ isPending: false, mutateAsync: vi.fn() })),
    useEvalSetsQuery: mocks.useEvalSetsQuery,
    useEvaluatorCatalogQuery: vi.fn(() => ({ data: [], isLoading: false })),
    useEvaluatorScoreHistoryQuery: vi.fn(() => ({ data: [] })),
    useRemoveEvalSetMemberMutation: vi.fn(() => ({ isPending: false, mutateAsync: vi.fn() })),
    useUpdateEvalSetMemberMutation: vi.fn(() => ({ isPending: false, mutateAsync: vi.fn() })),
  };
});

const CAPABILITY_ID = "00000000-0000-4000-8000-000000000010";
const PROJECT_ID = "00000000-0000-4000-8000-000000000001";

const capability = {
  id: CAPABILITY_ID,
  improvementMetadata: {},
  name: "invoice-capability",
  project: PROJECT_ID,
  slug: "invoice-capability",
} as Capability;

describe("EvaluatorsTab preload states", () => {
  let queryClient: QueryClient;

  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  );

  beforeEach(() => {
    queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    mocks.useEvalSetsQuery.mockReturnValue({
      data: { results: [] },
      error: null,
      isLoading: false,
    });
    mocks.useCapabilityEvalPreload.mockReturnValue({
      data: { error: null, status: "running" },
      isActive: true,
      isLoading: false,
    });
  });

  afterEach(() => {
    cleanup();
    vi.clearAllMocks();
  });

  it("shows in-progress UI instead of empty-state copy while preload is running", () => {
    render(
      <EvaluatorsTab capability={capability} capabilityId={CAPABILITY_ID} projectId={PROJECT_ID} />,
      { wrapper }
    );

    expect(screen.getByText("Authoring Default eval set")).toBeTruthy();
    expect(document.querySelector('[data-slot="spinner"]')).toBeTruthy();
    expect(screen.queryByText(/No eval sets for this capability yet/i)).toBeNull();
  });

  it("shows failed UI when preload fails with no eval sets", () => {
    mocks.useCapabilityEvalPreload.mockReturnValue({
      data: { error: "tier1 timeout", status: "failed" },
      isActive: false,
      isLoading: false,
    });

    render(
      <EvaluatorsTab capability={capability} capabilityId={CAPABILITY_ID} projectId={PROJECT_ID} />,
      { wrapper }
    );

    expect(screen.getByText(/tier1 timeout/i)).toBeTruthy();
    expect(screen.getByRole("button", { name: /New eval set/i })).toBeTruthy();
  });
});
