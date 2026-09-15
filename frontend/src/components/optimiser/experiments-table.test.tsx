// @vitest-environment jsdom
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { OptimizerExperiment } from "@/openapi";

const mocks = vi.hoisted(() => ({ navigate: vi.fn(), rows: vi.fn() }));

// Radix Select needs a layout engine jsdom lacks.
vi.mock("@/components/ui/select", () => ({
  Select: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  SelectContent: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  SelectItem: ({ children, value }: { children: React.ReactNode; value: string }) => (
    <div data-value={value}>{children}</div>
  ),
  SelectTrigger: () => null,
  SelectValue: () => null,
}));

// The real `DataTable` calls `onRowClick` from a row's `onClick`, so a button
// standing in for the row exercises the same handler.
vi.mock("@/components/ui/data-table", () => ({
  DataTable: ({
    data,
    onRowClick,
    toolbar,
  }: {
    data: { results: OptimizerExperiment[] };
    onRowClick?: (row: OptimizerExperiment) => void;
    toolbar: React.ReactNode;
  }) => {
    mocks.rows(data.results.map((e) => e.id));
    return (
      <div>
        {toolbar}
        <button onClick={() => onRowClick?.(data.results[0])} type="button">
          open row
        </button>
      </div>
    );
  },
}));

// `Link` is only here so the EntityRef import chain resolves.
vi.mock("@tanstack/react-router", () => ({
  Link: () => null,
  useNavigate: () => mocks.navigate,
}));

// `useDebouncedValue` is deliberately NOT mocked: the mount guard below holds
// only because the real hook seeds with the incoming value.

vi.mock("@/hooks/use-optimizer", () => {
  const make = (id: string, capability: string, capabilityName: string) =>
    ({
      capability,
      capabilityName,
      createdAt: new Date("2026-01-01T00:00:00Z"),
      currentIteration: 1,
      datasetName: "ds",
      evalSetName: "es",
      id,
      numIterations: 3,
      scores: null,
      status: "completed",
    }) as unknown as OptimizerExperiment;
  return {
    useOptimizerExperimentsQuery: () => ({
      data: {
        results: [
          make("exp-a", "capability-1", "Billing Capability"),
          make("exp-b", "capability-2", "Billing Capability"),
          make("exp-c", "capability-3", ""),
        ],
      },
      isFetched: true,
    }),
  };
});

import { ExperimentsTable } from "./experiments-table";

afterEach(() => {
  cleanup();
  mocks.navigate.mockClear();
});

function renderTable(runCapability: string) {
  render(
    <ExperimentsTable
      filters={{ run_capability: runCapability, run_search: "", run_status: "all" }}
      onSearchChange={vi.fn()}
      page={1}
      pageSize={25}
      projectId="proj-1"
    />
  );
  return mocks.rows.mock.calls.at(-1)?.[0] as string[];
}

// The stubbed trigger carries no label, so capability options are picked out by the
// fixture id prefix; no status value starts with "capability-".
const capabilityOptionValues = () =>
  [...document.querySelectorAll("[data-value^='capability-']")].map((el) =>
    el.getAttribute("data-value")
  );

describe("ExperimentsTable capability filter", () => {
  it("gives two same-named capabilities their own option and filters by id", () => {
    const rows = renderTable("capability-2");

    expect(capabilityOptionValues()).toEqual(["capability-1", "capability-2", "capability-3"]);
    expect(rows).toEqual(["exp-b"]);
  });

  it("keeps a nameless capability selectable under a fallback label", () => {
    renderTable("all");

    expect(capabilityOptionValues()).toContain("capability-3");
    expect(screen.getByText("Unnamed capability")).toBeTruthy();
  });

  it("surfaces an unknown incoming id instead of a blank select", () => {
    const rows = renderTable("capability-gone");

    expect(capabilityOptionValues()).toContain("capability-gone");
    expect(screen.getByText("Unknown capability")).toBeTruthy();
    expect(rows).toEqual([]);
  });
});

// The URL is the source of truth for the search box, so publishing the settled
// value must be a no-op until the user types.
describe("ExperimentsTable deep link", () => {
  it("doesn't rewrite a deep-linked page + search on mount", () => {
    const onSearchChange = vi.fn();

    // Two rows match "billing" at one per page, so page 2 needs no clamping.
    render(
      <ExperimentsTable
        filters={{ run_capability: "all", run_search: "billing", run_status: "all" }}
        onSearchChange={onSearchChange}
        page={2}
        pageSize={1}
        projectId="proj-1"
      />
    );

    expect(onSearchChange).not.toHaveBeenCalled();
  });
});

// A literal `search: { projectId }` overwrites the search instead of extending
// it, and "Back to optimiser" rebuilds the list URL from what survives.
describe("ExperimentsTable row click", () => {
  it("carries the whole search into the experiment, not just projectId", () => {
    renderTable("all");

    screen.getByRole("button", { name: "open row" }).click();

    const { search, to } = mocks.navigate.mock.calls[0][0];
    const url = { page: 2, projectId: "proj-1", run_search: "billing" };
    expect(to).toBe("/optimiser/$experimentId");
    expect(typeof search === "function" ? search(url) : search).toEqual(url);
  });
});
