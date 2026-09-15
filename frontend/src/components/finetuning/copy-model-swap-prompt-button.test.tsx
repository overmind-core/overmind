// @vitest-environment jsdom
import type { ReactNode } from "react";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { FinetuningJobList } from "@/openapi";

const mocks = vi.hoisted(() => ({
  modelSwapPrompt: vi.fn(),
}));

vi.mock("@/client", () => ({
  modelSwapPrompt: mocks.modelSwapPrompt,
}));

vi.mock("@/components/ui/tooltip", () => ({
  Tooltip: ({ children }: { children: ReactNode }) => <>{children}</>,
  TooltipContent: ({ children }: { children: ReactNode }) => <div>{children}</div>,
  TooltipTrigger: ({ children }: { children: ReactNode }) => <>{children}</>,
}));

vi.mock("@/components/model-provider-chip", () => ({
  getModelProviderInfo: (id: string) => ({ id, modelLabel: id, providerLabel: id }),
  getProviderIcon: () => null,
  ProviderLogo: () => null,
}));

import { CopyModelSwapPromptButton } from "./copy-model-swap-prompt-button";

const job = {
  baseModel: "Qwen/Qwen3-1.7B",
  id: "job-a",
  outputModelName: "invoice-v3",
  status: "succeeded",
} as unknown as FinetuningJobList;

const renderButton = ({ allowPin }: { allowPin?: boolean } = {}) => {
  mocks.modelSwapPrompt.mockResolvedValue({
    capabilityId: "capability-1",
    capabilityName: "invoice",
    newModel: "invoice-v3",
    oldModel: "Qwen/Qwen3-1.7B",
    pin: false,
    prompt: "swap the model to invoice-v3",
  });
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={queryClient}>
      <CopyModelSwapPromptButton allowPin={allowPin} jobs={[job]} />
    </QueryClientProvider>
  );
};

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("CopyModelSwapPromptButton", () => {
  it("fetches the alias prompt by default", async () => {
    renderButton({});
    fireEvent.click(await screen.findByLabelText("Copy a prompt switching to invoice-v3"));

    await waitFor(() =>
      expect(mocks.modelSwapPrompt).toHaveBeenCalledWith({ id: "job-a", pin: false })
    );
    expect(await screen.findByText("swap the model to invoice-v3")).toBeTruthy();
    expect(screen.getByText("Paste this in your coding agent.")).toBeTruthy();
  });

  it("keeps the hard pin behind a secondary action", async () => {
    renderButton({ allowPin: true });
    fireEvent.click(await screen.findByLabelText("Copy a prompt switching to invoice-v3"));
    fireEvent.click(await screen.findByText("Pin this exact model id"));

    await waitFor(() =>
      expect(mocks.modelSwapPrompt).toHaveBeenCalledWith({ id: "job-a", pin: true })
    );
    expect(await screen.findByText("Pins the exact model id.")).toBeTruthy();
  });

  it("offers no pin when the mount does not allow it", async () => {
    renderButton({});
    fireEvent.click(await screen.findByLabelText("Copy a prompt switching to invoice-v3"));
    await screen.findByText("Paste this in your coding agent.");
    expect(screen.queryByText("Pin this exact model id")).toBeNull();
  });
});
