// @vitest-environment jsdom

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("@/client", () => ({
  default: {
    capabilities: { capabilitiesList: vi.fn().mockResolvedValue({ results: [] }) },
    traces: {
      tracesModelsRetrieve: vi.fn().mockResolvedValue(["claude-sonnet-4-5", "gpt-5-nano"]),
    },
  },
}));

// Radix portals need a layout engine jsdom lacks (ResizeObserver, pointer
// capture), so the primitives become passthroughs.
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
  DropdownMenuContent: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  DropdownMenuItem: ({
    children,
    onSelect,
  }: {
    children: React.ReactNode;
    onSelect: (e: { preventDefault: () => void }) => void;
  }) => (
    <button onClick={() => onSelect({ preventDefault: () => {} })} type="button">
      {children}
    </button>
  ),
  DropdownMenuTrigger: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
}));
// A native `<select>`, so the option values — what reaches the backend — are assertable.
vi.mock("@/components/ui/select", () => ({
  Select: ({
    children,
    value,
    onValueChange,
  }: {
    children: React.ReactNode;
    value: string;
    onValueChange: (v: string) => void;
  }) => (
    <select onChange={(e) => onValueChange(e.target.value)} value={value}>
      {children}
    </select>
  ),
  SelectContent: ({ children }: { children: React.ReactNode }) => children,
  SelectItem: ({ children, value }: { children: React.ReactNode; value: string }) => (
    <option value={value}>{children}</option>
  ),
  SelectTrigger: () => null,
  SelectValue: () => null,
}));

import {
  ANY_VALUE,
  describeFilter,
  type FilterEntry,
  parseFiltersFromSearchParams,
  TracesFilters,
} from "./filters";

afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

function renderFilters(onFiltersChange = vi.fn(), filters: FilterEntry[] = []) {
  render(
    <QueryClientProvider client={new QueryClient()}>
      <TracesFilters filters={filters} onFiltersChange={onFiltersChange} projectId="proj-1" />
    </QueryClientProvider>
  );
  return onFiltersChange;
}

const addRow = (label: string) => fireEvent.click(screen.getByRole("button", { name: label }));
const valueInput = () => screen.getByPlaceholderText("Enter value…");
/** A row renders field / lookup / value selects, in that order. */
const valueSelect = () => screen.getAllByRole("combobox")[2] as HTMLSelectElement;
const optionValues = () => [...valueSelect().options].map((o) => o.value);

describe("TracesFilters", () => {
  it("labels the empty-state add-field trigger 'Add field', with no separate model-reported field", () => {
    renderFilters();

    expect(screen.getByRole("button", { name: "Add field" })).toBeTruthy();
    expect(screen.queryByText("Reported a model")).toBeNull();
  });

  it("commits 'Any model' as a real filter, not as an empty/uncommitted value", async () => {
    const onFiltersChange = renderFilters();

    addRow("Model");
    await waitFor(() => expect(optionValues()).toContain(ANY_VALUE));

    fireEvent.change(valueSelect(), { target: { value: ANY_VALUE } });

    expect(onFiltersChange).toHaveBeenCalledWith([
      { field: "model", id: expect.any(String), lookup: "eq", value: ANY_VALUE },
    ]);
    expect(describeFilter({ field: "model", id: "x", lookup: "eq", value: ANY_VALUE })).toBe(
      "Model is any model"
    );
  });

  it("gives a second row of the same field its next free lookup", () => {
    renderFilters();

    addRow("Total tokens");
    addRow("Total tokens");

    // The two halves of a token band, which the backend ANDs. A second `gte` row
    // would overwrite the first.
    expect(screen.getAllByRole("combobox").map((s) => (s as HTMLSelectElement).value)).toEqual([
      "total_tokens",
      "gte",
      "total_tokens",
      "lte",
    ]);
  });

  it("refuses a row only once every lookup for the field is taken", () => {
    renderFilters();

    addRow("Service name");
    addRow("Service name");
    addRow("Service name");

    // `icontains` then `eq`; there is no third lookup to serialise to.
    expect(screen.getAllByPlaceholderText("Enter value…")).toHaveLength(2);
  });

  it("commits a typed value once, after the debounce", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const onFiltersChange = renderFilters();

    addRow("Service name");
    for (const value of ["wor", "work", "worker"]) {
      fireEvent.change(valueInput(), { target: { value } });
    }
    expect(onFiltersChange).not.toHaveBeenCalled();

    await act(async () => {
      vi.advanceTimersByTime(400);
    });

    expect(onFiltersChange).toHaveBeenCalledTimes(1);
    expect(onFiltersChange.mock.calls[0][0]).toEqual([
      { field: "service_name", id: expect.any(String), lookup: "icontains", value: "worker" },
    ]);
  });

  it("commits a pending draft when the closing popover tears the row down", () => {
    const onFiltersChange = renderFilters();

    addRow("Service name");
    fireEvent.change(valueInput(), { target: { value: "worker" } });
    expect(onFiltersChange).not.toHaveBeenCalled();

    // What Escape / clicking outside does: `PopoverContent` unmounts the row
    // while the 400ms commit is still pending.
    cleanup();

    expect(onFiltersChange).toHaveBeenCalledWith([
      { field: "service_name", id: expect.any(String), lookup: "icontains", value: "worker" },
    ]);
  });

  it("keeps a pending draft's debounce running across a parent re-render", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const onFiltersChange = renderFilters();

    addRow("Service name");
    fireEvent.change(valueInput(), { target: { value: "worker" } });
    await act(async () => {
      vi.advanceTimersByTime(250);
    });
    // Any parent state change re-creates the row's `onUpdate` arrow.
    addRow("Operation");
    await act(async () => {
      vi.advanceTimersByTime(250);
    });

    expect(onFiltersChange).toHaveBeenCalledTimes(1);
    expect(onFiltersChange.mock.calls[0][0]).toEqual([
      { field: "service_name", id: expect.any(String), lookup: "icontains", value: "worker" },
    ]);
  });

  it("offers the models the project's spans actually reported", async () => {
    renderFilters();

    addRow("Model");

    await waitFor(() =>
      expect(optionValues()).toEqual(["__any__", "claude-sonnet-4-5", "gpt-5-nano"])
    );
  });

  it("doesn't duplicate the 'Any model' option when the model field is already set to it", async () => {
    const entry: FilterEntry = {
      field: "model",
      id: "f-model",
      lookup: "eq",
      value: ANY_VALUE,
    };
    renderFilters(vi.fn(), [entry]);

    await waitFor(() => expect(optionValues()).toContain("claude-sonnet-4-5"));

    expect(optionValues()).toEqual(["__any__", "claude-sonnet-4-5", "gpt-5-nano"]);
    expect(valueSelect().value).toBe(ANY_VALUE);
  });

  it("commits the OTel status code, not its label, without waiting for the debounce", () => {
    const onFiltersChange = renderFilters();

    fireEvent.click(screen.getByRole("button", { name: "Status" }));
    expect(optionValues()).toEqual(["__any__", "0", "1", "2"]);

    fireEvent.change(valueSelect(), { target: { value: "2" } });

    expect(onFiltersChange).toHaveBeenCalledWith([
      { field: "status_code", id: expect.any(String), lookup: "eq", value: "2" },
    ]);
    expect(describeFilter({ field: "status_code", id: "x", lookup: "eq", value: "2" })).toBe(
      "Status is Error"
    );
  });

  it("keeps a span type outside the option list selectable and described", () => {
    const entry: FilterEntry = {
      field: "span_type",
      id: "f-span_type",
      lookup: "eq",
      value: "obs",
    };
    renderFilters(vi.fn(), [entry]);

    expect(optionValues()).toEqual([
      "__any__",
      "llm_call",
      "tool_call",
      "entry_point",
      "batch",
      "obs",
    ]);
    expect(valueSelect().value).toBe("obs");
    expect(describeFilter(entry)).toBe("Span type is obs");
  });

  it("drops the Any sentinel out of the URL instead of building a chip from it", () => {
    expect(parseFiltersFromSearchParams({ model: ANY_VALUE })).toEqual([]);
    expect(parseFiltersFromSearchParams({ has_model: "true" })).toEqual([
      { field: "model", id: "f-has_model", lookup: "eq", value: ANY_VALUE },
    ]);
  });
});
