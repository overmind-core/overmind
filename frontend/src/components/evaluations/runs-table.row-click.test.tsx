// @vitest-environment jsdom
// Renders the real `DataTable` on purpose: navigation happens on the row's
// `onClick`, and the sibling file's stub calls `onRowClick` directly, so it
// would pass either way.
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

// Radix Select needs a layout engine jsdom lacks.
vi.mock("@/components/ui/select", () => ({
  Select: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  SelectContent: () => null,
  SelectItem: () => null,
  SelectTrigger: () => null,
  SelectValue: () => null,
}));

const run = (id: string, name: string) => ({
  createdAt: new Date("2026-01-01T00:00:00Z"),
  evaluatorCount: 2,
  id,
  name,
  status: "completed",
  variantCount: 1,
});

const mocks = vi.hoisted(() => ({
  deleteRun: vi.fn(),
  navigate: vi.fn(),
  rows: [] as ReturnType<typeof run>[],
  undoable: vi.fn(),
}));
vi.mock("@tanstack/react-router", () => ({ Link: () => null, useNavigate: () => mocks.navigate }));

vi.mock("@/lib/notify", () => ({
  errorMessage: (_e: unknown, fallback: string) => fallback,
  notify: { error: vi.fn(), undoable: mocks.undoable },
}));

// The real `useMutation`: react-query v5 fires per-call `mutate` callbacks only
// for the latest call, which is what a bulk delete must not rely on.
vi.mock("@/hooks/use-evaluations", async () => {
  const { useMutation } = await import("@tanstack/react-query");
  return {
    useDeleteEvalRunMutation: () => useMutation({ mutationFn: mocks.deleteRun }),
    useEvalRunDatasetsQuery: () => ({ data: [] }),
    useEvalRunsQuery: () => ({
      data: { count: mocks.rows.length, results: mocks.rows },
      isLoading: false,
    }),
    useProjectCapabilitiesQuery: () => ({ data: { results: [] } }),
  };
});

vi.mock("@/hooks/use-persisted-state", () => ({
  usePersistedState: (_k: string, initial: string) => [initial, vi.fn()],
}));

import { RunsTable } from "./runs-table";

beforeAll(() => {
  // jsdom has no ResizeObserver, which DataTable uses to measure column widths.
  vi.stubGlobal(
    "ResizeObserver",
    class {
      observe() {}
      unobserve() {}
      disconnect() {}
    }
  );
});

beforeEach(() => {
  mocks.rows = [run("run-1", "nightly")];
});

afterEach(() => {
  cleanup();
  mocks.deleteRun.mockReset();
  mocks.navigate.mockClear();
  mocks.undoable.mockClear();
});

const renderTable = () =>
  render(
    <QueryClientProvider client={new QueryClient()}>
      <RunsTable
        filters={{ run_capability: "all", run_dataset: "all", run_search: "", run_status: "all" }}
        onSearchChange={vi.fn()}
        page={1}
        pageSize={25}
        projectId="proj-1"
      />
    </QueryClientProvider>
  );

describe("RunsTable row click", () => {
  it("navigates from a plain cell", () => {
    renderTable();

    fireEvent.click(screen.getByText("nightly"));

    expect(mocks.navigate).toHaveBeenCalledTimes(1);
  });

  it("selects the run without navigating when the checkbox is clicked", () => {
    renderTable();

    fireEvent.click(screen.getByRole("checkbox", { name: "Select run nightly" }));

    expect(screen.getByText("1 selected")).toBeTruthy();
    expect(mocks.navigate).not.toHaveBeenCalled();
  });

  it("deletes without navigating when the delete icon is clicked", () => {
    renderTable();

    fireEvent.click(screen.getByRole("button", { name: "Delete run" }));

    expect(mocks.undoable).toHaveBeenCalledTimes(1);
    expect(mocks.navigate).not.toHaveBeenCalled();
  });

  it("settles a bulk delete and keeps only the runs that failed selected", async () => {
    mocks.rows = [run("run-1", "nightly"), run("run-2", "weekly"), run("run-3", "monthly")];
    mocks.deleteRun.mockImplementation(async (id: string) => {
      if (id === "run-2") throw new Error("gone");
    });
    renderTable();

    fireEvent.click(screen.getByRole("checkbox", { name: "Select all runs" }));
    fireEvent.click(screen.getByRole("button", { name: /Delete 3/ }));
    fireEvent.click(screen.getByRole("button", { name: "Delete 3 runs" }));

    await waitFor(() => expect(screen.getByText("1 selected")).toBeTruthy());
    expect(mocks.deleteRun).toHaveBeenCalledTimes(3);
  });
});
