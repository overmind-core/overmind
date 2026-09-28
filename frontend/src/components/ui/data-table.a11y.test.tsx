// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";

import { DATA_TABLE_PAGE_JUMP_ROWS, DataTable } from "@/components/ui/data-table";

afterEach(cleanup);

beforeAll(() => {
  vi.stubGlobal(
    "ResizeObserver",
    class {
      observe() {}
      unobserve() {}
      disconnect() {}
    }
  );
});

const COLUMNS = [
  {
    accessorKey: "name",
    header: "Name",
    id: "name",
    meta: { label: "Name", orderingField: "name" },
    size: 100,
  },
  { accessorKey: "status", header: "Status", id: "status", size: 100 },
];

const ROWS = { count: 1, results: [{ name: "a", status: "ok" }] };

function page(n: number) {
  return {
    count: n,
    results: Array.from({ length: n }, (_, i) => ({ name: `row-${i}`, status: "ok" })),
  };
}

type TableProps = Partial<Parameters<typeof DataTable>[0]>;

function renderTable(props: TableProps = {}) {
  const element = (p: TableProps) => (
    <DataTable
      columns={COLUMNS}
      data={ROWS}
      onPageChange={() => {}}
      onPageSizeChange={() => {}}
      page={1}
      pageSize={25}
      {...p}
    />
  );
  const view = render(element(props));
  return {
    ...view,
    /** Re-render with some props changed — stands in for paging / filtering. */
    rerenderWith: (next: TableProps) => view.rerender(element({ ...props, ...next })),
  };
}

const dataRow = () => screen.getAllByRole("row").at(-1) as HTMLElement;
/** Every `<tr>` except the single header row. */
const dataRows = () => screen.getAllByRole("row").slice(1) as HTMLElement[];
const tabStops = () => dataRows().map((r) => r.getAttribute("tabindex"));
const focusedRowIndex = () => dataRows().indexOf(document.activeElement as HTMLElement);
const focusRow = (el: HTMLElement) =>
  act(() => {
    el.focus();
  });

describe("DataTable rows", () => {
  it("are tabbable and activate on Enter and Space", () => {
    const onRowClick = vi.fn();
    renderTable({ onRowClick });

    const row = dataRow();
    expect(row.tagName).toBe("TR");
    expect(row.getAttribute("role")).toBe("row");
    expect(row.getAttribute("tabindex")).toBe("0");

    fireEvent.keyDown(row, { key: "Enter" });
    fireEvent.keyDown(row, { key: " " });
    expect(onRowClick).toHaveBeenCalledTimes(2);

    fireEvent.click(row);
    expect(onRowClick).toHaveBeenCalledTimes(3);
  });

  it("marks the row backing an open detail panel", () => {
    renderTable({ isRowActive: () => true, onRowClick: () => {} });
    expect(dataRow().getAttribute("aria-current")).toBe("true");
  });

  it("ignores keys aimed at a control inside the row", () => {
    const onRowClick = vi.fn();
    renderTable({
      columns: [
        { cell: () => <button type="button">Open</button>, header: "Name", id: "name", size: 100 },
      ],
      onRowClick,
    });

    fireEvent.keyDown(screen.getByRole("button", { name: "Open" }), { key: " " });
    expect(onRowClick).not.toHaveBeenCalled();
  });
});

/** `role="grid"` promises arrow-key navigation, so the roving tabindex is part of the
 *  ARIA contract, not a nicety. */
describe("DataTable roving tabindex", () => {
  const interactive = (props: TableProps = {}) =>
    renderTable({ data: page(5), onRowClick: () => {}, ...props });

  it("keeps exactly one row in the tab order", () => {
    interactive();
    expect(tabStops()).toEqual(["0", "-1", "-1", "-1", "-1"]);
    expect(screen.getByRole("grid")).toBeTruthy();
  });

  it("moves focus and the tab stop with ArrowDown and ArrowUp", () => {
    interactive();
    focusRow(dataRows()[0]);

    fireEvent.keyDown(dataRows()[0], { key: "ArrowDown" });
    expect(focusedRowIndex()).toBe(1);
    expect(tabStops()).toEqual(["-1", "0", "-1", "-1", "-1"]);

    fireEvent.keyDown(dataRows()[1], { key: "ArrowUp" });
    expect(focusedRowIndex()).toBe(0);
    expect(tabStops()).toEqual(["0", "-1", "-1", "-1", "-1"]);
  });

  it("stops at the ends instead of wrapping", () => {
    interactive();
    focusRow(dataRows()[0]);
    fireEvent.keyDown(dataRows()[0], { key: "ArrowUp" });
    expect(focusedRowIndex()).toBe(0);

    fireEvent.keyDown(dataRows()[0], { key: "End" });
    fireEvent.keyDown(dataRows()[4], { key: "ArrowDown" });
    expect(focusedRowIndex()).toBe(4);
  });

  it("jumps to the first and last row with Home and End", () => {
    interactive();
    focusRow(dataRows()[0]);

    fireEvent.keyDown(dataRows()[0], { key: "End" });
    expect(focusedRowIndex()).toBe(4);
    expect(tabStops()).toEqual(["-1", "-1", "-1", "-1", "0"]);

    fireEvent.keyDown(dataRows()[4], { key: "Home" });
    expect(focusedRowIndex()).toBe(0);
  });

  it("moves by a page with PageDown and PageUp", () => {
    const size = DATA_TABLE_PAGE_JUMP_ROWS * 2 + 1;
    interactive({ data: page(size), pageSize: size });
    focusRow(dataRows()[0]);

    fireEvent.keyDown(dataRows()[0], { key: "PageDown" });
    expect(focusedRowIndex()).toBe(DATA_TABLE_PAGE_JUMP_ROWS);

    fireEvent.keyDown(dataRows()[DATA_TABLE_PAGE_JUMP_ROWS], { key: "PageUp" });
    expect(focusedRowIndex()).toBe(0);
  });

  it("lets keys it does not handle through untouched", () => {
    interactive();
    const row = dataRows()[0];
    // fireEvent returns false once the handler called preventDefault.
    expect(fireEvent.keyDown(row, { key: "a" })).toBe(true);
    expect(fireEvent.keyDown(row, { key: "Tab" })).toBe(true);
    expect(fireEvent.keyDown(dataRows()[0], { key: "ArrowDown" })).toBe(false);
  });

  it("leaves arrow keys inside a cell's own input alone", () => {
    interactive({
      columns: [
        { cell: () => <input aria-label="Rename" />, header: "Name", id: "name", size: 100 },
      ],
    });
    const input = screen.getAllByRole("textbox")[0];
    focusRow(input);

    expect(fireEvent.keyDown(input, { key: "ArrowDown" })).toBe(true);
    expect(document.activeElement).toBe(input);
    expect(tabStops()).toEqual(["0", "-1", "-1", "-1", "-1"]);
  });

  it("keeps focus in the table when the focused row is filtered away", () => {
    const view = interactive();
    focusRow(dataRows()[4]);
    expect(focusedRowIndex()).toBe(4);

    view.rerenderWith({ data: page(2) });

    expect(document.activeElement).not.toBe(document.body);
    expect(focusedRowIndex()).toBe(1);
    expect(tabStops()).toEqual(["-1", "0"]);
  });

  it("keeps a read-only table out of the grid contract and the tab order", () => {
    renderTable({ data: page(5) });
    expect(screen.queryByRole("grid")).toBeNull();
    expect(screen.getByRole("table")).toBeTruthy();
    expect(tabStops()).toEqual([null, null, null, null, null]);
  });
});

describe("DataTable sorting", () => {
  it("exposes the sorted column and names the control", () => {
    renderTable({ onOrderingChange: () => {}, ordering: "-name" });

    const [sorted, plain] = screen.getAllByRole("columnheader");
    expect(sorted.getAttribute("aria-sort")).toBe("descending");
    // Not sortable: no ordering field, so no state to announce.
    expect(plain.getAttribute("aria-sort")).toBeNull();
    expect(screen.getByRole("button", { name: "Sort by Name" })).toBeTruthy();
  });

  it("reports an unsorted sortable column as none", () => {
    renderTable({ onOrderingChange: () => {}, ordering: "-other" });
    expect(screen.getAllByRole("columnheader")[0].getAttribute("aria-sort")).toBe("none");
  });
});

describe("DataTable empty states", () => {
  const EMPTY = { count: 0, results: [] };

  it("offers a way out of a filtered-empty list", () => {
    const onClearFilters = vi.fn();
    renderTable({
      data: EMPTY,
      emptyState: <p>No datasets yet</p>,
      hasActiveFilters: true,
      onClearFilters,
    });

    expect(screen.queryByText("No datasets yet")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Clear filters" }));
    expect(onClearFilters).toHaveBeenCalled();
  });

  it("falls back to a shared no-results state", () => {
    renderTable({ data: EMPTY });
    expect(screen.getByText("No results")).toBeTruthy();
  });
});

describe("DataTable grouping", () => {
  it("inserts a header above clustered rows and skips blank keys", () => {
    renderTable({
      data: {
        count: 3,
        results: [
          { name: "a", status: "ok" },
          { name: "b", status: "ok" },
          { name: "c", status: "ok" },
        ],
      },
      getGroupKey: (row) => ((row as { name: string }).name === "c" ? null : "conv-1"),
      renderGroupHeader: (key, count) => `${key} · ${count}`,
    });

    expect(screen.getByText("conv-1 · 2")).toBeTruthy();
    expect(screen.getAllByRole("row")).toHaveLength(5);
  });

  it("does not treat a group header as a data row", () => {
    const onRowClick = vi.fn();
    renderTable({
      data: {
        count: 2,
        results: [
          { name: "a", status: "ok" },
          { name: "b", status: "ok" },
        ],
      },
      getGroupKey: () => "g",
      onRowClick,
      renderGroupHeader: () => "Group g",
    });

    fireEvent.click(screen.getByText("Group g"));
    expect(onRowClick).not.toHaveBeenCalled();
    fireEvent.click(screen.getByText("a"));
    expect(onRowClick).toHaveBeenCalledTimes(1);
  });

  it("activates a group header button without selecting a row", () => {
    const onRowClick = vi.fn();
    const onGroupHeaderClick = vi.fn();
    renderTable({
      data: {
        count: 2,
        results: [
          { name: "a", status: "ok" },
          { name: "b", status: "ok" },
        ],
      },
      getGroupKey: () => "g",
      onGroupHeaderClick,
      onRowClick,
      renderGroupHeader: () => "Group g",
    });

    fireEvent.click(screen.getByRole("button", { name: "Group g" }));
    expect(onGroupHeaderClick).toHaveBeenCalledWith("g");
    expect(onRowClick).not.toHaveBeenCalled();
  });
});
