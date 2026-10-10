// @vitest-environment jsdom

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

import { DatasetNotebook } from "@/components/datasets/notebook/notebook-page";
import { TooltipProvider } from "@/components/ui/tooltip";
import { WorkshopSidebarContext } from "@/contexts/workshop-sidebar-context";
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
      datasetsResumeImportCreate: state.retry,
      datasetsRetrieve: vi.fn(() => Promise.resolve(state.dataset)),
      datasetsWorkbenchRetrieve: vi.fn(() => Promise.resolve({ runPage: {}, runs: [] })),
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
    error: "The import worker stopped.",
    id: "dataset",
    name: "titanic",
    operation: { source_import: { can_resume: true } },
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
      <TooltipProvider>
        <WorkshopSidebarContext.Provider value={{ close: vi.fn(), open: false, toggle: vi.fn() }}>
          <DatasetNotebook datasetId="dataset" projectId="project" />
        </WorkshopSidebarContext.Provider>
      </TooltipProvider>
    </QueryClientProvider>
  );
}

it("retries a failed import once and prevents duplicate clicks while the request is pending", async () => {
  showNotebook();
  const button = screen.getByRole("button", { name: "Resume import" }) as HTMLButtonElement;
  expect(button.disabled).toBe(false);
  expect(screen.getByText("The import worker stopped.")).toBeTruthy();
  fireEvent.click(button);
  await waitFor(() => expect(state.retry).toHaveBeenCalledWith({ id: "dataset" }));
  await waitFor(() => expect(button.disabled).toBe(true));
  fireEvent.click(button);
  expect(state.retry).toHaveBeenCalledTimes(1);
});

it("gates retry for guests without submitting a mutation", () => {
  state.guest = true;
  showNotebook();
  fireEvent.click(screen.getByRole("button", { name: "Resume import" }));
  expect(state.upgrade).toHaveBeenCalledOnce();
  expect(state.retry).not.toHaveBeenCalled();
});

it("keeps import retry unavailable while the source is landing", () => {
  state.dataset = {
    ...state.dataset,
    operation: { source_import: { can_resume: false } },
    state: "landing",
  };
  showNotebook();
  expect(screen.queryByRole("button", { name: "Resume import" })).toBeNull();
  expect(screen.getByText("Workshop operation running")).toBeTruthy();
});
