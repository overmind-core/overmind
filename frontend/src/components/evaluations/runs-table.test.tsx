// @vitest-environment jsdom
// Dataset options come from the runs facet, not `/api/datasets/cards/`: that
// endpoint's newest-100 window can omit a dataset that has runs.
import type { ComponentProps } from "react";

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

// Radix Select needs a layout engine jsdom lacks. The trigger keeps its label,
// which is how a test tells the selects apart.
vi.mock("@/components/ui/select", () => ({
  Select: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  SelectContent: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  SelectItem: ({ children, value }: { children: React.ReactNode; value: string }) => (
    <div data-value={value}>{children}</div>
  ),
  SelectTrigger: ({ "aria-label": label }: { "aria-label"?: string }) => <div aria-label={label} />,
  SelectValue: () => null,
}));

// A button standing in for a row exercises the same `onRowClick` the real
// `DataTable` calls. The click *guards* need the real one — see
// runs-table.row-click.test.tsx.
const tableProps = vi.hoisted(() => vi.fn());
vi.mock("@/components/ui/data-table", () => ({
  DataTable: (props: {
    data?: { results: { id: string }[] };
    onRowClick?: (row: { id: string }) => void;
    toolbar: React.ReactNode;
  }) => {
    tableProps(props);
    return (
      <div>
        {props.toolbar}
        <button onClick={() => props.onRowClick?.(props.data!.results[0])} type="button">
          open row
        </button>
      </div>
    );
  },
}));

const navigate = vi.hoisted(() => vi.fn());
vi.mock("@tanstack/react-router", () => ({ Link: () => null, useNavigate: () => navigate }));

// `useDebouncedValue` is deliberately NOT mocked: the mount guard below holds
// only because the real hook seeds with the incoming value.

vi.mock("@/hooks/use-persisted-state", () => ({
  usePersistedState: (_k: string, initial: string) => [initial, vi.fn()],
}));

const facet = vi.hoisted(() => ({
  rows: [{ id: "ds-old-but-used", name: "Old dataset with runs" }],
}));

vi.mock("@/hooks/use-evaluations", () => ({
  useDeleteEvalRunMutation: () => ({ mutate: vi.fn() }),
  useEvalRunDatasetsQuery: () => ({ data: facet.rows }),
  useEvalRunsQuery: () => ({ data: { count: 1, results: [{ id: "run-1" }] }, isLoading: false }),
  useProjectCapabilitiesQuery: () => ({ data: { results: [] } }),
}));

// Decoy: what the project's dataset list would offer instead — a dataset with
// no runs, crowding out `ds-old-but-used`.
vi.mock("@/hooks/use-datasets", () => ({
  useDatasetsQuery: () => ({
    data: { results: [{ id: "ds-newer-unused", name: "Newer dataset, never evaluated" }] },
  }),
}));

import { EvalRunsListStatusEnum } from "@/openapi";
import { RunsTable } from "./runs-table";

afterEach(() => {
  cleanup();
  navigate.mockClear();
  tableProps.mockClear();
  facet.rows = [{ id: "ds-old-but-used", name: "Old dataset with runs" }];
});

const renderTable = (
  filters: Partial<ComponentProps<typeof RunsTable>["filters"]> = {},
  props: Partial<ComponentProps<typeof RunsTable>> = {}
) =>
  render(
    <RunsTable
      filters={{
        run_capability: "all",
        run_dataset: "all",
        run_search: "",
        run_status: "all",
        ...filters,
      }}
      onSearchChange={vi.fn()}
      page={1}
      pageSize={25}
      projectId="proj-1"
      {...props}
    />
  );

const datasetOptionValues = () =>
  [...document.querySelectorAll("[data-value^='ds-']")].map((el) => el.getAttribute("data-value"));

// Each stubbed `Select` wraps its own trigger and options, so the labelled
// trigger's parent scopes the query to one select.
const optionValuesFor = (label: string) => {
  const select = document.querySelector(`[aria-label="${label}"]`)?.parentElement;
  return [...(select?.querySelectorAll("[data-value]") ?? [])].map((el) =>
    el.getAttribute("data-value")
  );
};

const lastEmptyState = () => tableProps.mock.calls.at(-1)?.[0].emptyState as React.ReactNode;

describe("RunsTable dataset filter", () => {
  it("offers the runs' datasets, not the project's dataset list", () => {
    renderTable();

    expect(datasetOptionValues()).toEqual(["ds-old-but-used"]);
  });

  // Dataset.name is blank=True/default="" on the backend.
  it("labels an unnamed dataset instead of rendering a blank option", () => {
    facet.rows = [{ id: "ds-unnamed-01", name: "" }];

    renderTable();

    expect(document.querySelector("[data-value='ds-unnamed-01']")?.textContent).toBe(
      "Dataset ds-unnam"
    );
  });

  // A `?run_dataset=` the facet doesn't carry still filters the query, so the
  // select must stay mounted and name it, or the filter has no control to clear.
  it("keeps a dataset the facet doesn't carry selectable", () => {
    facet.rows = [];

    renderTable({ run_dataset: "ds-gone" });

    expect(document.querySelector("[data-value='ds-gone']")?.textContent).toBe("Unknown dataset");
  });

  it("keeps a capability the facet doesn't carry selectable", () => {
    renderTable({ run_capability: "capability-gone" });

    expect(document.querySelector("[data-value='capability-gone']")?.textContent).toBe(
      "Unknown capability"
    );
  });
});

// The eval-runs endpoint 400s on statuses outside `EvalRunsListStatusEnum` —
// notably `JobStatusEnum`'s `partially_completed`.
describe("RunsTable status filter", () => {
  it("offers exactly the eval-run statuses plus the unfiltered sentinel", () => {
    renderTable();

    expect(optionValuesFor("Filter by status")).toEqual([
      "all",
      ...Object.values(EvalRunsListStatusEnum),
    ]);
  });
});

describe("RunsTable filtered-empty state", () => {
  it("hands the table a real empty state, wired to clear the filters", () => {
    const onSearchChange = vi.fn();

    renderTable({ run_status: "running" }, { onSearchChange });
    const emptyState = lastEmptyState();
    expect(emptyState).toBeTruthy();

    render(<div>{emptyState}</div>);
    screen.getByRole("button", { name: "Clear filters" }).click();

    expect(onSearchChange).toHaveBeenCalledWith({
      page: 1,
      run_capability: "all",
      run_dataset: "all",
      run_search: "",
      run_status: "all",
    });
  });
});

// The URL is the source of truth for the search box, so publishing the settled
// value must be a no-op until the user types.
describe("RunsTable deep link", () => {
  it("doesn't rewrite a deep-linked page + search on mount", () => {
    const onSearchChange = vi.fn();

    renderTable({ run_search: "nightly" }, { onSearchChange, page: 3 });

    expect(onSearchChange).not.toHaveBeenCalled();
  });
});

// A literal `search: { projectId }` overwrites the search instead of extending
// it, and every way back to the list rebuilds its URL from what survives.
describe("RunsTable row click", () => {
  it("carries the whole search into the run, not just projectId", () => {
    renderTable({ run_search: "nightly", run_status: "failed" }, { page: 3 });

    screen.getByRole("button", { name: "open row" }).click();

    const { search, to } = navigate.mock.calls[0][0];
    const url = { page: 3, projectId: "proj-1", run_search: "nightly", run_status: "failed" };
    expect(to).toBe("/evaluations/runs/$runId");
    expect(typeof search === "function" ? search(url) : search).toEqual(url);
  });
});
