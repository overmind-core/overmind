// @vitest-environment jsdom

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

import { DatasetNotebook } from "@/components/datasets/notebook/notebook-page";
import { DatasetFromJSON } from "@/openapi";

const state = vi.hoisted(() => ({
  dataset: {} as ReturnType<typeof DatasetFromJSON>,
  guest: false,
  retry: vi.fn(),
  upgrade: vi.fn(),
}));

vi.mock("@/client", () => ({
  default: {
    capabilities: { capabilitiesList: vi.fn(() => Promise.resolve({ results: [] })) },
    datasets: {
      datasetsRetrieve: vi.fn(() => Promise.resolve(state.dataset)),
      datasetsRunCreate: state.retry,
    },
  },
  fetchWithAuth: vi.fn(() => Promise.resolve(new Response(""))),
}));
vi.mock("@tanstack/react-router", () => ({ Link: () => null, useNavigate: () => vi.fn() }));
vi.mock("@/contexts/auth-context", () => ({
  useAuthContext: () => ({ isGuest: state.guest, requestUpgrade: state.upgrade }),
}));

let queryClient: QueryClient;

beforeEach(() => {
  vi.clearAllMocks();
  state.guest = false;
  state.dataset = DatasetFromJSON({
    cells: [],
    chat: [],
    error: "The import worker stopped.",
    id: "dataset",
    name: "titanic",
    project: "project",
    source_spec: {},
    state: "error",
  });
  state.retry.mockImplementation(() => new Promise(() => {}));
  queryClient = new QueryClient({
    defaultOptions: { mutations: { retry: false }, queries: { retry: false, staleTime: Infinity } },
  });
  vi.stubGlobal(
    "ResizeObserver",
    class {
      observe() {}
      unobserve() {}
      disconnect() {}
    }
  );
});

afterEach(() => {
  cleanup();
  queryClient.clear();
  vi.unstubAllGlobals();
});

function showNotebook() {
  queryClient.setQueryData(["datasets", "detail", "dataset"], state.dataset);
  queryClient.setQueryData(["eval-capabilities", "project"], { results: [] });
  return render(
    <QueryClientProvider client={queryClient}>
      <DatasetNotebook datasetId="dataset" projectId="project" />
    </QueryClientProvider>
  );
}

it("retries a failed import once and prevents duplicate clicks while the request is pending", async () => {
  showNotebook();
  const button = screen.getByRole("button", { name: "Retry import" }) as HTMLButtonElement;
  expect(button.disabled).toBe(false);
  expect(screen.getByText("Import failed")).toBeTruthy();
  fireEvent.click(button);
  await waitFor(() => expect(state.retry).toHaveBeenCalledWith({ id: "dataset" }));
  await waitFor(() => expect(button.disabled).toBe(true));
  fireEvent.click(button);
  expect(state.retry).toHaveBeenCalledTimes(1);
  expect(
    (screen.getByRole("textbox", { name: "Message the agent" }) as HTMLTextAreaElement).disabled
  ).toBe(true);
});

it("gates retry for guests without submitting a mutation", () => {
  state.guest = true;
  showNotebook();
  fireEvent.click(screen.getByRole("button", { name: "Retry import" }));
  expect(state.upgrade).toHaveBeenCalledOnce();
  expect(state.retry).not.toHaveBeenCalled();
});

it("keeps import retry unavailable while the source is landing", () => {
  state.dataset = { ...state.dataset, state: "landing" };
  showNotebook();
  expect(screen.queryByRole("button", { name: "Retry import" })).toBeNull();
  expect(screen.getByText("Landing the source…")).toBeTruthy();
  expect(
    (screen.getByRole("textbox", { name: "Message the agent" }) as HTMLTextAreaElement).disabled
  ).toBe(true);
});
