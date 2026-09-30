// @vitest-environment jsdom

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

import { WorkshopFundingControl } from "@/components/datasets/workshop-funding";
import { useWorkshopFunding, workshopFundingKey } from "@/hooks/use-workshop-funding";
import { FundingSourceEnum, type WorkshopFunding } from "@/openapi";

const mocks = vi.hoisted(() => ({ models: vi.fn(), navigate: vi.fn(), save: vi.fn() }));
vi.mock("@tanstack/react-router", () => ({ useNavigate: () => mocks.navigate }));
vi.mock("@/client", () => ({
  default: {
    chatgpt: {
      chatgptModelsList: mocks.models,
      chatgptPartialUpdate: mocks.save,
    },
  },
}));

const initial: WorkshopFunding = {
  accountId: "account",
  accounts: [
    {
      connected: true,
      email: "test@example.invalid",
      id: "account",
      label: "Test account",
      planEnabled: true,
    },
  ],
  enabled: true,
  fundingSource: FundingSourceEnum.platform,
  model: "gpt-test-small",
  usageUrl: "https://chatgpt.com/#settings/Usage",
};

function Composer() {
  const { isChanging } = useWorkshopFunding();
  return (
    <form aria-label="Composer">
      <WorkshopFundingControl />
      <button disabled={isChanging} type="submit">
        Send request
      </button>
    </form>
  );
}

function setup(data: WorkshopFunding = initial) {
  const cache = new QueryClient({
    defaultOptions: { queries: { retry: false, staleTime: Infinity } },
  });
  cache.setQueryData(workshopFundingKey, data);
  mocks.save.mockImplementation(async ({ patchedWorkshopFundingRequestRequest: change }) => ({
    ...cache.getQueryData<WorkshopFunding>(workshopFundingKey),
    ...change,
  }));
  render(
    <QueryClientProvider client={cache}>
      <Composer />
    </QueryClientProvider>
  );
  return cache;
}

beforeEach(() => {
  Object.defineProperty(HTMLElement.prototype, "scrollIntoView", {
    configurable: true,
    value: vi.fn(),
  });
  vi.stubEnv("VITE_SELF_HOSTED", "true");
  mocks.models.mockResolvedValue([
    { id: "gpt-test-small", name: "Small model" },
    { id: "gpt-test-large", name: "Large model" },
  ]);
});
afterEach(() => {
  cleanup();
  vi.resetAllMocks();
  vi.unstubAllEnvs();
});

it("toggles funding, selects an account model, and switches back without disconnecting", async () => {
  const cache = setup();
  fireEvent.click(screen.getByRole("button", { name: "Use ChatGPT" }));
  await waitFor(() =>
    expect(screen.getByRole("button", { name: "Use ChatGPT" }).getAttribute("aria-pressed")).toBe(
      "true"
    )
  );
  fireEvent.keyDown(screen.getByRole("combobox", { name: "ChatGPT model" }), { key: "ArrowDown" });
  fireEvent.click(await screen.findByRole("option", { name: "Large model" }));
  await waitFor(() =>
    expect(cache.getQueryData<WorkshopFunding>(workshopFundingKey)?.model).toBe("gpt-test-large")
  );
  fireEvent.click(screen.getByRole("button", { name: "Use ChatGPT" }));
  await waitFor(() =>
    expect(cache.getQueryData<WorkshopFunding>(workshopFundingKey)?.fundingSource).toBe("platform")
  );
  expect(cache.getQueryData<WorkshopFunding>(workshopFundingKey)?.accounts[0].connected).toBe(true);
  expect(mocks.save).toHaveBeenLastCalledWith({
    patchedWorkshopFundingRequestRequest: { fundingSource: "platform" },
  });
});

it("asks for a model before enabling an account with no saved model", async () => {
  setup({ ...initial, accountId: null, model: "" });
  fireEvent.click(screen.getByRole("button", { name: "Use ChatGPT" }));
  expect(mocks.save).not.toHaveBeenCalled();
  fireEvent.click(await screen.findByRole("option", { name: "Small model" }));
  await waitFor(() =>
    expect(mocks.save).toHaveBeenCalledWith({
      patchedWorkshopFundingRequestRequest: {
        accountId: "account",
        fundingSource: "chatgpt",
        model: "gpt-test-small",
      },
    })
  );
});

it("blocks submission while funding is saving and preserves the old selection after failure", async () => {
  const cache = setup();
  let reject!: (error: Error) => void;
  mocks.save.mockImplementationOnce(
    () =>
      new Promise((_, fail) => {
        reject = fail;
      })
  );
  fireEvent.click(screen.getByRole("button", { name: "Use ChatGPT" }));
  await waitFor(() =>
    expect(
      (screen.getByRole("button", { name: "Send request" }) as HTMLButtonElement).disabled
    ).toBe(true)
  );
  await act(async () => reject(new Error("The connection expired.")));
  expect(await screen.findByRole("alert")).toBeTruthy();
  expect(cache.getQueryData<WorkshopFunding>(workshopFundingKey)?.fundingSource).toBe("platform");
  expect(screen.getByRole("button", { name: "Use ChatGPT" }).getAttribute("aria-pressed")).toBe(
    "false"
  );
});

it("routes an unconnected account to Settings and lets a disconnected selection be turned off", async () => {
  const cache = setup({ ...initial, accounts: [] });
  fireEvent.click(screen.getByRole("button", { name: "Use ChatGPT" }));
  expect(mocks.navigate).toHaveBeenCalledWith({ to: "/settings" });
  expect(mocks.save).not.toHaveBeenCalled();
  act(() =>
    cache.setQueryData(workshopFundingKey, {
      ...initial,
      accounts: [{ ...initial.accounts[0], connected: false, planEnabled: false }],
      fundingSource: "chatgpt",
    })
  );
  await screen.findByRole("button", { name: "Reconnect ChatGPT" });
  fireEvent.click(screen.getByRole("button", { name: "Use ChatGPT" }));
  await waitFor(() =>
    expect(cache.getQueryData<WorkshopFunding>(workshopFundingKey)?.fundingSource).toBe("platform")
  );
});
