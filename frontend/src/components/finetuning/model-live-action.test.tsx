// @vitest-environment jsdom
import type { ReactNode } from "react";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { Capability, DeployedModel, FinetuningJobList } from "@/openapi";

const CAPABILITY_ID = "00000000-0000-4000-8000-000000000010";
const PROJECT_ID = "00000000-0000-4000-8000-000000000001";
const JOB_ID = "job-a";

const mocks = vi.hoisted(() => ({
  capabilitiesPartialUpdate: vi.fn(),
  swapPromptProps: vi.fn(),
  useCapabilityDetailQuery: vi.fn(),
  useDeployedModelsQuery: vi.fn(),
}));

vi.mock("@/hooks/use-query", () => ({ useCapabilityDetailQuery: mocks.useCapabilityDetailQuery }));
vi.mock("@/hooks/use-inference", () => ({
  useDeployedModelsQuery: mocks.useDeployedModelsQuery,
}));
vi.mock("@/client", () => ({
  default: { capabilities: { capabilitiesPartialUpdate: mocks.capabilitiesPartialUpdate } },
}));

// The copy-prompt button has its own tests; here it only reports the props it is given.
vi.mock("@/components/finetuning/copy-model-swap-prompt-button", () => ({
  CopyModelSwapPromptButton: (props: Record<string, unknown>) => {
    mocks.swapPromptProps(props);
    return <button type="button">Copy prompt</button>;
  },
}));

// TooltipContent must render: a blocked control's reason is asserted from it.
vi.mock("@/components/ui/tooltip", () => ({
  Tooltip: ({ children }: { children: ReactNode }) => <>{children}</>,
  TooltipContent: ({ children }: { children: ReactNode }) => <div>{children}</div>,
  TooltipTrigger: ({ children }: { children: ReactNode }) => <>{children}</>,
}));

import { ModelLiveAction } from "./model-live-action";

const job = {
  capability: CAPABILITY_ID,
  id: JOB_ID,
  status: "succeeded",
} as unknown as FinetuningJobList;

const deployment = (id: string, maxModelLen = 4096): DeployedModel =>
  ({
    finetuningJobId: JOB_ID,
    id,
    maxModelLen,
    modelId: `ft:${id}`,
    status: "ready",
  }) as unknown as DeployedModel;

const capabilityState = (over: Partial<Capability> = {}): Capability =>
  ({
    activeModel: null,
    id: CAPABILITY_ID,
    name: "invoice-capability",
    ...over,
  }) as unknown as Capability;

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

const renderAction = ({
  capability,
  capabilityPending = !capability,
  deployedPending = false,
  models = [deployment("dm-1")],
  ...props
}: {
  capability: Capability | undefined;
  capabilityPending?: boolean;
  deployedPending?: boolean;
  models?: DeployedModel[];
  capabilityId?: string | null;
  allowPin?: boolean;
  model?: DeployedModel;
  promote?: boolean;
}) => {
  mocks.useCapabilityDetailQuery.mockReturnValue({
    data: capability,
    isPending: capabilityPending,
  });
  mocks.useDeployedModelsQuery.mockReturnValue({
    data: deployedPending ? undefined : { count: models.length, results: models },
    isPending: deployedPending,
  });
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={queryClient}>
      <ModelLiveAction
        capabilityId={CAPABILITY_ID}
        jobs={[job]}
        projectId={PROJECT_ID}
        {...props}
      />
    </QueryClientProvider>
  );
};

const skeleton = () => document.querySelector("[data-slot='skeleton']");

describe("ModelLiveAction — the swap-prompt state", () => {
  it("offers the copy prompt on both mounts while the capability is unmigrated", () => {
    renderAction({ capability: capabilityState() });
    expect(screen.getByText("Copy prompt")).toBeTruthy();

    cleanup();
    renderAction({ capability: capabilityState(), model: deployment("dm-1") });
    expect(screen.getByText("Copy prompt")).toBeTruthy();
  });

  it("falls back to the copy prompt for a job with no capability to name", () => {
    renderAction({ capability: undefined, capabilityId: null });

    expect(screen.getByText("Copy prompt")).toBeTruthy();
  });

  it("waits on a skeleton instead of offering a prompt while the capability loads", () => {
    renderAction({ capability: undefined });

    expect(screen.queryByText("Copy prompt")).toBeNull();
    expect(skeleton()).not.toBeNull();
  });
});

describe("ModelLiveAction — the retarget state", () => {
  it("retargets from the run card while still offering the copy prompt", async () => {
    mocks.capabilitiesPartialUpdate.mockResolvedValue({});
    renderAction({
      capability: capabilityState({ activeModel: "dm-other" }),
    });

    expect(screen.getByText("Copy prompt")).toBeTruthy();
    fireEvent.click(screen.getByLabelText("Make live — ft:dm-1"));

    await waitFor(() =>
      expect(mocks.capabilitiesPartialUpdate).toHaveBeenCalledWith({
        id: CAPABILITY_ID,
        patchedCapabilityRequest: { activeModel: "dm-1" },
      })
    );
  });

  it("retargets the model the detail page hands it directly", async () => {
    mocks.capabilitiesPartialUpdate.mockResolvedValue({});
    renderAction({
      capability: capabilityState({ activeModel: "dm-other" }),
      // A model whose job is not in `jobs` at all: the page hands it over directly.
      model: { ...deployment("dm-page"), finetuningJobId: "job-z" } as DeployedModel,
    });

    fireEvent.click(screen.getByLabelText("Make live — ft:dm-page"));

    await waitFor(() =>
      expect(mocks.capabilitiesPartialUpdate).toHaveBeenCalledWith({
        id: CAPABILITY_ID,
        patchedCapabilityRequest: { activeModel: "dm-page" },
      })
    );
  });

  // Inherited from the Models tab through the shared mutation.
  it("asks before narrowing the context window", async () => {
    const incumbent = { ...deployment("dm-wide", 8192), finetuningJobId: "job-w" };
    renderAction({
      capability: capabilityState({ activeModel: "dm-wide" }),
      models: [incumbent as DeployedModel, deployment("dm-1", 4096)],
    });

    fireEvent.click(screen.getByLabelText("Make live — ft:dm-1"));

    expect(
      await screen.findByText(/accepts 4,096 tokens of context, down from 8,192/)
    ).toBeTruthy();
    expect(mocks.capabilitiesPartialUpdate).not.toHaveBeenCalled();
  });

  // A real `disabled` cancels the element's own pointer events, so the tooltip
  // carrying the reason could never open.
  it("blocks a deployment that cannot serve the alias without silencing the reason", () => {
    renderAction({
      capability: capabilityState({ activeModel: "dm-other" }),
      model: { ...deployment("dm-1"), status: "deploying" } as DeployedModel,
    });

    const button = screen.getByLabelText("Make live — ft:dm-1");
    expect(button).toHaveProperty("disabled", false);
    expect(button.getAttribute("aria-disabled")).toBe("true");
    expect(
      screen.getByText("Still deploying — it can serve the alias once it is ready.")
    ).toBeTruthy();

    fireEvent.click(button);
    expect(mocks.capabilitiesPartialUpdate).not.toHaveBeenCalled();
  });
});

describe("ModelLiveAction — no candidate", () => {
  it("holds the control's place while the deployments load", () => {
    renderAction({
      capability: capabilityState({ activeModel: "dm-other" }),
      deployedPending: true,
    });

    expect(skeleton()).not.toBeNull();
    expect(screen.queryByText(/no longer available/)).toBeNull();
  });

  it("says the run's deployment is gone once the query has settled without it", () => {
    renderAction({
      capability: capabilityState({ activeModel: "dm-other" }),
      models: [],
    });

    expect(skeleton()).toBeNull();
    const button = screen.getByRole("button", { name: /Make live/ });
    expect(button.getAttribute("aria-disabled")).toBe("true");
    expect(screen.getByText(/no longer available/)).toBeTruthy();
  });
});

describe("ModelLiveAction — the live state", () => {
  // A permanently disabled button composited under the contrast floor, and
  // `check:contrast` cannot see an opacity utility.
  it("states the live model as a badge rather than a disabled button", () => {
    renderAction({ capability: capabilityState({ activeModel: "dm-1" }) });

    const badge = screen.getByText("Live model");
    expect(badge.getAttribute("data-slot")).toBe("badge");
    // Keyboard-reachable, so its tooltip is too.
    expect(badge.getAttribute("tabindex")).toBe("0");
    expect(screen.getByText("Copy prompt")).toBeTruthy();
    expect(document.querySelector("[disabled]")).toBeNull();
  });

  it("keeps the copy prompt next to the live badge", () => {
    renderAction({ capability: capabilityState({ activeModel: "dm-1" }) });
    expect(screen.getByText("Live model")).toBeTruthy();
    expect(screen.getByText("Copy prompt")).toBeTruthy();

    cleanup();
    renderAction({
      capability: capabilityState({ activeModel: "dm-page" }),
      model: deployment("dm-page"),
    });
    expect(screen.getByText("Live model")).toBeTruthy();
    expect(screen.getByText("Copy prompt")).toBeTruthy();
  });
});
