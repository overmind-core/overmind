// @vitest-environment jsdom

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { Table } from "@tanstack/react-table";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

// Same stubs as `filters.test.tsx`: the Radix primitives need a layout engine
// jsdom doesn't have.
vi.mock("@/components/ui/popover", () => ({
  Popover: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  PopoverContent: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  PopoverTrigger: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
}));
vi.mock("@/components/ui/tooltip", () => ({
  Tooltip: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  TooltipContent: () => null,
  TooltipProvider: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  TooltipTrigger: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
}));
vi.mock("@/components/ui/dropdown-menu", () => ({
  DropdownMenu: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  DropdownMenuCheckboxItem: () => null,
  DropdownMenuContent: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  DropdownMenuItem: () => null,
  DropdownMenuLabel: () => null,
  DropdownMenuSeparator: () => null,
  DropdownMenuTrigger: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
}));
vi.mock("@/components/ui/select", () => ({
  Select: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  SelectContent: () => null,
  SelectItem: () => null,
  SelectTrigger: () => null,
  SelectValue: () => null,
}));

import {
  ANY_VALUE,
  type FilterEntry,
  parseFiltersFromSearchParams,
  serializeFiltersToSearchParams,
} from "./filters";
import { TracesTableToolbar } from "./traces-table-toolbar";

afterEach(cleanup);

const emptyTable = { getAllColumns: () => [] } as unknown as Table<unknown>;

function renderToolbar(onFiltersChange = vi.fn(), filters: FilterEntry[] = []) {
  render(
    <QueryClientProvider client={new QueryClient()}>
      <TracesTableToolbar
        customTimeRange={{}}
        filters={filters}
        isRefetching={false}
        onFiltersChange={onFiltersChange}
        onRefresh={vi.fn()}
        onResetAll={vi.fn()}
        onSearchChange={vi.fn()}
        onStatusChange={vi.fn()}
        onTimeRangeChange={vi.fn()}
        projectId="proj-1"
        searchValue=""
        status="all"
        table={emptyTable}
        timeRange="all"
        view="roots"
      />
    </QueryClientProvider>
  );
  return onFiltersChange;
}

describe("TracesTableToolbar quick filters", () => {
  it("toggles the model field to 'Any model', not span_type=llm_call", () => {
    const onFiltersChange = renderToolbar();

    fireEvent.click(screen.getByRole("button", { name: "LLM calls" }));

    expect(onFiltersChange).toHaveBeenCalledTimes(1);
    const emitted = onFiltersChange.mock.calls[0][0];
    expect(emitted).toEqual([
      { field: "model", id: expect.any(String), lookup: "eq", value: ANY_VALUE },
    ]);
    // `span_type` is what makes `observability.tsx` force the span-level view, and
    // this predicate is trace-scoped, so it must serialise to `has_model` alone.
    expect(serializeFiltersToSearchParams(emitted)).toMatchObject({
      has_model: "true",
      model: undefined,
      span_type: undefined,
    });
  });

  it("shows the LLM calls preset as active for a ?has_model=true deep link", () => {
    const filters = parseFiltersFromSearchParams({ has_model: "true" });

    renderToolbar(vi.fn(), filters);

    expect(screen.getByRole("button", { name: "LLM calls" }).getAttribute("aria-pressed")).toBe(
      "true"
    );
  });
});
