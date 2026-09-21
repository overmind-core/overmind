// @vitest-environment jsdom
import type { ReactNode } from "react";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { Capability, DeployedModel } from "@/openapi";

const mocks = vi.hoisted(() => ({
  capabilitiesPartialUpdate: vi.fn(),
  useDeployedModelQuery: vi.fn(),
  useDeployedModelsQuery: vi.fn(),
}));

vi.mock("@/hooks/use-inference", () => ({
  useDeployedModelQuery: mocks.useDeployedModelQuery,
  useDeployedModelsQuery: mocks.useDeployedModelsQuery,
}));

vi.mock("@/client", () => ({
  default: { capabilities: { capabilitiesPartialUpdate: mocks.capabilitiesPartialUpdate } },
}));

vi.mock("@tanstack/react-router", () => ({
  Link: ({ children }: { children: ReactNode }) => <a href="/">{children}</a>,
  useNavigate: () => vi.fn(),
}));

// The @lobehub icon ESM subpaths behind the provider logo don't resolve under
// vitest's node resolver; the row's provider chrome isn't the assertion.
vi.mock("@/components/model-provider-chip", () => ({
  getModelProviderInfo: (id: string) => ({ id, modelLabel: id, providerLabel: id }),
  getProviderIcon: () => () => null,
  ProviderLogo: () => null,
}));

import { ModelsTab } from "./ModelsTab";

const CAPABILITY_ID = "00000000-0000-4000-8000-000000000010";
const PROJECT_ID = "00000000-0000-4000-8000-000000000001";

const capability = {
  id: CAPABILITY_ID,
  model: "gpt-4o-mini",
  name: "invoice-capability",
} as unknown as Capability;
/** No base model, so the fine-tune rows are the only rows. */
const bareCapability = {
  id: CAPABILITY_ID,
  name: "invoice-capability",
} as unknown as Capability;

const model = (
  id: string,
  status = "ready",
  capabilityId: string | null = CAPABILITY_ID,
  maxModelLen = 4096
) =>
  ({
    baseModelId: "gpt-4o-mini",
    capabilityId,
    capabilityName: "invoice-capability",
    createdAt: new Date("2026-01-01T00:00:00Z"),
    finetuningJobId: "job-1",
    finetuningJobName: "nightly",
    id,
    maxModelLen,
    modelId: `ft:${id}`,
    status,
  }) as unknown as DeployedModel;

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

const renderTab = (
  data: { count: number; results: DeployedModel[] } | undefined,
  withBaseModel = capability
) => {
  mocks.useDeployedModelsQuery.mockReturnValue({ data, error: null, isLoading: false });
  mocks.useDeployedModelQuery.mockReturnValue({ data: undefined, isLoading: false });
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={queryClient}>
      <ModelsTab capability={withBaseModel} capabilityId={CAPABILITY_ID} projectId={PROJECT_ID} />
    </QueryClientProvider>
  );
};

describe("ModelsTab scope", () => {
  it("scopes the request to the capability and counts the server's total", () => {
    // One row of a bigger set: a count taken from the page would say 2.
    renderTab({ count: 7, results: [model("m-1")] });

    expect(mocks.useDeployedModelsQuery).toHaveBeenCalledWith({
      capability: CAPABILITY_ID,
      pageSize: 100,
      projectId: PROJECT_ID,
    });
    // 7 fine-tunes + the synthetic base row.
    expect(screen.getByText("8")).toBeTruthy();
  });

  // An undeployed model is still a model the capability was trained for, and the server
  // counts it.
  it("renders what the server returned instead of re-narrowing it", () => {
    renderTab({ count: 1, results: [model("m-1", "deleted")] }, bareCapability);

    expect(screen.queryByText(/No models yet/)).toBeNull();
    expect(screen.getByText("1")).toBeTruthy();
  });
});

describe("ModelsTab alias routing", () => {
  // The alias has no memorable second form — this is the only place to obtain it.
  it("shows the alias even when the capability has no models at all", () => {
    renderTab({ count: 0, results: [] }, bareCapability);

    expect(screen.getByTitle(`Copy overmind/${CAPABILITY_ID}`)).toBeTruthy();
  });

  it("offers the switch only for ready deployments", () => {
    renderTab({
      count: 3,
      results: [model("m-1"), model("m-2", "failed"), model("m-3", "deploying")],
    });

    expect(screen.getByLabelText("Make ft:m-1 live")).not.toHaveProperty("disabled", true);

    // Blocked rows render no radio: the reason lives on a focusable trigger, which
    // must not be reachable as a "make live" control.
    expect(screen.queryByLabelText("Make ft:m-2 live")).toBeNull();
    expect(screen.queryByLabelText("Make ft:m-3 live")).toBeNull();
  });

  /** The reason has to be a focusable control's accessible name: `disabled` drops a
   *  control out of the tab order and `title` never fires on focus. */
  it("exposes every blocked row's reason as a focusable control's name", () => {
    renderTab({
      count: 2,
      results: [model("m-2", "failed"), model("m-3", "deploying")],
    });

    for (const reason of [
      "This deployment failed, so it can't serve the alias.",
      "Still deploying — it can serve the alias once it is ready.",
      "The alias only routes to deployed fine-tunes.",
    ]) {
      const trigger = screen.getByRole("button", { name: reason });
      expect(trigger).not.toHaveProperty("disabled", true);
      // Not a switch: clicking it must retarget nothing.
      fireEvent.click(trigger);
    }
    expect(mocks.capabilitiesPartialUpdate).not.toHaveBeenCalled();
  });

  it("patches activeModel when a ready deployment is picked", async () => {
    mocks.capabilitiesPartialUpdate.mockResolvedValue({});
    renderTab({ count: 1, results: [model("m-1")] });

    fireEvent.click(screen.getByLabelText("Make ft:m-1 live"));

    await waitFor(() =>
      expect(mocks.capabilitiesPartialUpdate).toHaveBeenCalledWith({
        id: CAPABILITY_ID,
        patchedCapabilityRequest: { activeModel: "m-1" },
      })
    );
  });

  // A shorter context window breaks long-prompt callers as an opaque 502, so it asks
  // first — but it must not block, and it must not ask when widening.
  it("asks before narrowing the context window", async () => {
    const routed = { ...capability, activeModel: "m-live" } as unknown as Capability;
    renderTab(
      { count: 2, results: [model("m-live", "ready", CAPABILITY_ID, 8192), model("m-1")] },
      routed
    );

    fireEvent.click(screen.getByLabelText("Make ft:m-1 live"));

    // Grouped digits: `tabular-nums` is inert in this typeface.
    expect(
      await screen.findByText(/accepts 4,096 tokens of context, down from 8,192/)
    ).toBeTruthy();
    expect(mocks.capabilitiesPartialUpdate).not.toHaveBeenCalled();
  });

  it("switches straight away when the candidate is not narrower", async () => {
    mocks.capabilitiesPartialUpdate.mockResolvedValue({});
    const routed = { ...capability, activeModel: "m-live" } as unknown as Capability;
    renderTab(
      { count: 2, results: [model("m-live"), model("m-1", "ready", CAPABILITY_ID, 8192)] },
      routed
    );

    fireEvent.click(screen.getByLabelText("Make ft:m-1 live"));

    await waitFor(() => expect(mocks.capabilitiesPartialUpdate).toHaveBeenCalledTimes(1));
  });
});

describe("ModelsTab benchmarks", () => {
  it("keeps benchmark selection in training setup, separate from live routing", () => {
    renderTab({ count: 1, results: [model("m-live")] }, { ...capability, activeModel: "m-live" });
    expect(screen.queryByRole("columnheader", { name: "Benchmark" })).toBeNull();
    expect(screen.queryByLabelText(/as benchmark/)).toBeNull();
    expect(screen.getByLabelText("ft:m-live is live")).toBeTruthy();
    expect(mocks.capabilitiesPartialUpdate).not.toHaveBeenCalled();
  });
});
