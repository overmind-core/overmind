// @vitest-environment jsdom
import type { ReactNode } from "react";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { FinetuningJobList } from "@/openapi";

const mocks = vi.hoisted(() => ({
  modelSwapPrompt: vi.fn(),
  notifyError: vi.fn(),
  notifySuccess: vi.fn(),
  writeText: vi.fn(),
}));

vi.mock("@/client", () => ({ modelSwapPrompt: mocks.modelSwapPrompt }));
vi.mock("@/lib/notify", () => ({
  notify: { error: mocks.notifyError, success: mocks.notifySuccess },
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

function renderButton() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={queryClient}>
      <CopyModelSwapPromptButton jobs={[job]} />
    </QueryClientProvider>
  );
  return screen.getByRole("button", { name: "Copy a prompt switching to invoice-v3" });
}

beforeEach(() => {
  Object.defineProperty(navigator, "clipboard", {
    configurable: true,
    value: { writeText: mocks.writeText },
  });
  mocks.modelSwapPrompt.mockResolvedValue({ prompt: "Use overmind/capability-1" });
  mocks.writeText.mockResolvedValue(undefined);
});
afterEach(() => {
  cleanup();
  vi.resetAllMocks();
});

describe("CopyModelSwapPromptButton", () => {
  it("copies the alias prompt directly and confirms only after the clipboard write", async () => {
    let finishCopy!: () => void;
    mocks.writeText.mockImplementation(
      () =>
        new Promise<void>((resolve) => {
          finishCopy = resolve;
        })
    );
    const button = renderButton();
    fireEvent.click(button);
    await waitFor(() => expect(mocks.writeText).toHaveBeenCalledWith("Use overmind/capability-1"));
    expect(mocks.modelSwapPrompt).toHaveBeenCalledWith({ id: "job-a", pin: false });
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(button.hasAttribute("disabled")).toBe(true);
    expect(mocks.notifySuccess).not.toHaveBeenCalled();
    fireEvent.click(button);
    expect(mocks.modelSwapPrompt).toHaveBeenCalledTimes(1);
    finishCopy();
    await waitFor(() => expect(mocks.notifySuccess).toHaveBeenCalledWith("Prompt copied"));
    await waitFor(() => expect(button.hasAttribute("disabled")).toBe(false));
  });

  it("reports an API failure without writing to the clipboard", async () => {
    const error = new Error("The model is unavailable");
    mocks.modelSwapPrompt.mockRejectedValue(error);
    const button = renderButton();
    fireEvent.click(button);
    await waitFor(() =>
      expect(mocks.notifyError).toHaveBeenCalledWith(error, "Could not copy prompt")
    );
    expect(mocks.writeText).not.toHaveBeenCalled();
    expect(mocks.notifySuccess).not.toHaveBeenCalled();
    await waitFor(() => expect(button.hasAttribute("disabled")).toBe(false));
  });

  it("reports clipboard denial and permits another attempt", async () => {
    const error = new Error("Clipboard permission denied");
    mocks.writeText.mockRejectedValueOnce(error);
    const button = renderButton();
    fireEvent.click(button);
    await waitFor(() =>
      expect(mocks.notifyError).toHaveBeenCalledWith(error, "Could not copy prompt")
    );
    expect(mocks.notifySuccess).not.toHaveBeenCalled();
    await waitFor(() => expect(button.hasAttribute("disabled")).toBe(false));
    fireEvent.click(button);
    await waitFor(() => expect(mocks.notifySuccess).toHaveBeenCalledWith("Prompt copied"));
    expect(mocks.writeText).toHaveBeenCalledTimes(2);
  });
});
