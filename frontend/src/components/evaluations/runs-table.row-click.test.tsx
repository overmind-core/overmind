// @vitest-environment jsdom
// Renders the real `DataTable` on purpose: navigation happens on the row's
// `onClick`, and the sibling file's stub calls `onRowClick` directly, so it
// would pass either way.
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";

// Radix Select needs a layout engine jsdom lacks.
vi.mock("@/components/ui/select", () => ({
  Select: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  SelectContent: () => null,
  SelectItem: () => null,
  SelectTrigger: () => null,
  SelectValue: () => null,
}));

const mocks = vi.hoisted(() => ({ navigate: vi.fn(), undoable: vi.fn() }));
vi.mock("@tanstack/react-router", () => ({ Link: () => null, useNavigate: () => mocks.navigate }));

vi.mock("@/lib/notify", () => ({
  errorMessage: (_e: unknown, fallback: string) => fallback,
  notify: { error: vi.fn(), undoable: mocks.undoable },
}));

vi.mock("@/hooks/use-evaluations", () => ({
  useDeleteEvalRunMutation: () => ({ mutate: vi.fn() }),
  useEvalRunDatasetsQuery: () => ({ data: [] }),
  useEvalRunsQuery: () => ({
    data: {
      count: 1,
      results: [
        {
          createdAt: new Date("2026-01-01T00:00:00Z"),
          evaluatorCount: 2,
          id: "run-1",
          name: "nightly",
          status: "completed",
          variantCount: 1,
        },
      ],
    },
    isLoading: false,
  }),
  useProjectCapabilitiesQuery: () => ({ data: { results: [] } }),
}));

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

afterEach(() => {
  cleanup();
  mocks.navigate.mockClear();
  mocks.undoable.mockClear();
});

const renderTable = () =>
  render(
    <RunsTable
      filters={{ run_capability: "all", run_dataset: "all", run_search: "", run_status: "all" }}
      onSearchChange={vi.fn()}
      page={1}
      pageSize={25}
      projectId="proj-1"
    />
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
});
