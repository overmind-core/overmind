// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
// Renders the real `DataTable` on purpose: navigation happens on the row's
// `onClick`, and the sibling file's stub calls `onRowClick` directly, so it
// would pass either way.
import {
  act,
  cleanup,
  fireEvent,
  render as renderComponent,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

// Radix Select needs a layout engine jsdom lacks.
vi.mock("@/components/ui/select", () => ({
  Select: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  SelectContent: () => null,
  SelectItem: () => null,
  SelectTrigger: () => null,
  SelectValue: () => null,
}));

const mocks = vi.hoisted(() => ({
  error: vi.fn(),
  mutateAsync: vi.fn(),
  navigate: vi.fn(),
  rows: [] as {
    id: string;
    kind: "generative" | "decision";
    name: string;
    status: string;
    createdAt: Date;
    evaluatorCount: number;
    variantCount: number;
  }[],
  undoable: vi.fn(),
}));
vi.mock("@tanstack/react-router", () => ({
  Link: () => null,
  useNavigate: () => mocks.navigate,
  useSearch: () => ({ projectId: "proj-1" }),
}));

vi.mock("@/lib/notify", () => ({
  errorMessage: (_e: unknown, fallback: string) => fallback,
  notify: { error: mocks.error, undoable: mocks.undoable },
}));

vi.mock("@/hooks/use-evaluations", () => ({
  useDeleteEvalRunMutation: () => ({ mutateAsync: mocks.mutateAsync }),
  useEvalRunDatasetsQuery: () => ({ data: [] }),
  useEvalRunsQuery: () => ({
    data: {
      count: mocks.rows.length,
      results: mocks.rows,
    },
    isLoading: false,
  }),
  useProjectCapabilitiesQuery: () => ({ data: { results: [] } }),
}));

vi.mock("@/hooks/use-persisted-state", () => ({
  usePersistedState: (_k: string, initial: string) => [initial, vi.fn()],
}));

import { RunsTable } from "./runs-table";

beforeEach(() => {
  mocks.mutateAsync.mockReset().mockResolvedValue(undefined);
  mocks.error.mockClear();
  mocks.rows = [
    {
      createdAt: new Date("2026-01-01T00:00:00Z"),
      evaluatorCount: 2,
      id: "run-1",
      kind: "generative",
      name: "nightly",
      status: "completed",
      variantCount: 1,
    },
  ];
});
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

it("selects and deletes a decision run without navigating", async () => {
  mocks.rows[0].kind = "decision";
  renderTable();
  fireEvent.click(screen.getByRole("checkbox", { name: "Select run nightly" }));
  expect(screen.getByText("1 selected")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Delete run" }));
  const deletion = mocks.undoable.mock.calls[0][0];
  act(() => deletion.optimistic());
  expect(screen.queryByText("nightly")).toBeNull();
  act(() => deletion.rollback());
  expect(screen.getByText("nightly")).toBeTruthy();
  expect(mocks.mutateAsync).not.toHaveBeenCalled();
  await act(() => deletion.commit());
  expect(mocks.mutateAsync).toHaveBeenCalledWith(
    expect.objectContaining({ id: "run-1", kind: "decision" })
  );
  expect(mocks.navigate).not.toHaveBeenCalled();
});

it("keeps only failed rows selected after mixed bulk deletion, including colliding IDs", async () => {
  mocks.rows.push({ ...mocks.rows[0], kind: "decision", name: "probabilities" });
  const failure = new Error("Resolve provider work before deleting");
  mocks.mutateAsync.mockImplementation(async (run) => {
    if (run.kind === "decision") throw failure;
  });
  renderTable();
  fireEvent.click(screen.getByRole("checkbox", { name: "Select all runs" }));
  expect(screen.getByText("2 selected")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Delete 2" }));
  fireEvent.click(
    within(screen.getByRole("alertdialog")).getByRole("button", { name: "Delete 2 runs" })
  );
  await waitFor(() => expect(screen.getByText("1 selected")).toBeTruthy());
  expect(screen.queryByText("nightly")).toBeNull();
  expect(
    screen.getByRole("checkbox", { name: "Select run probabilities" }).getAttribute("data-state")
  ).toBe("checked");
  expect(mocks.error).toHaveBeenCalledWith(failure, expect.any(String));
  expect(mocks.mutateAsync).toHaveBeenCalledWith({ id: "run-1", kind: "generative" });
  expect(mocks.mutateAsync).toHaveBeenCalledWith({ id: "run-1", kind: "decision" });
  mocks.mutateAsync.mockResolvedValue(undefined);
  fireEvent.click(screen.getByRole("button", { name: "Delete 1" }));
  fireEvent.click(
    within(screen.getByRole("alertdialog")).getByRole("button", { name: "Delete 1 run" })
  );
  await waitFor(() => expect(screen.queryByText("1 selected")).toBeNull());
  expect(screen.queryByText("probabilities")).toBeNull();
});

it("restores a failed single deletion", async () => {
  mocks.rows[0].kind = "decision";
  mocks.mutateAsync.mockRejectedValue(new Error("In-flight work"));
  renderTable();
  fireEvent.click(screen.getByRole("button", { name: "Delete run" }));
  const deletion = mocks.undoable.mock.calls[0][0];
  act(() => deletion.optimistic());
  expect(screen.queryByText("nightly")).toBeNull();
  await act(() => deletion.commit());
  expect(screen.getByText("nightly")).toBeTruthy();
  expect(mocks.error).toHaveBeenCalled();
});

function render(children: React.ReactNode) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return renderComponent(children, {
    wrapper: ({ children }) => (
      <QueryClientProvider client={client}>{children}</QueryClientProvider>
    ),
  });
}
