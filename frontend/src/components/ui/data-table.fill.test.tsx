// @vitest-environment jsdom
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";

import { DataTable } from "@/components/ui/data-table";

afterEach(cleanup);

const CONTAINER_PX = 1000;

beforeAll(() => {
  // jsdom has no layout: every element reports 0×0 and never fires a resize. Both
  // measurement paths are stubbed to a fixed viewport so the assertions are about the
  // width math, not about jsdom.
  vi.stubGlobal(
    "ResizeObserver",
    class {
      observe() {}
      unobserve() {}
      disconnect() {}
    }
  );
  Object.defineProperty(HTMLElement.prototype, "clientWidth", {
    configurable: true,
    get() {
      return this.className?.includes?.("overflow-auto") ? CONTAINER_PX : 0;
    },
  });
});

const COLUMNS = [
  { accessorKey: "name", header: "Name", id: "name", size: 100 },
  { accessorKey: "status", header: "Status", id: "status", size: 100 },
];

const ROWS = { count: 1, results: [{ name: "a", status: "ok" }] };

function renderTable(props: Partial<Parameters<typeof DataTable>[0]> = {}) {
  return render(
    <DataTable
      columns={COLUMNS}
      data={ROWS}
      onPageChange={() => {}}
      onPageSizeChange={() => {}}
      page={1}
      pageSize={25}
      {...props}
    />
  );
}

const headerWidths = () =>
  screen
    .getAllByRole("columnheader")
    .map((th) => Number.parseInt((th as HTMLElement).style.width || "0", 10))
    .filter((w) => w > 0);

describe("DataTable column widths", () => {
  it("fills the container on the very first render that shows rows", () => {
    renderTable();
    const widths = headerWidths();
    expect(widths.reduce((sum, w) => sum + w, 0)).toBeGreaterThan(CONTAINER_PX * 0.98);
  });

  it("fills the container when rows arrive after a loading pass", () => {
    const { rerender } = renderTable({ isLoading: true });
    expect(screen.queryAllByRole("columnheader")).toHaveLength(0);

    rerender(
      <DataTable
        columns={COLUMNS}
        data={ROWS}
        isLoading={false}
        onPageChange={() => {}}
        onPageSizeChange={() => {}}
        page={1}
        pageSize={25}
      />
    );

    const widths = headerWidths();
    expect(widths.reduce((sum, w) => sum + w, 0)).toBeGreaterThan(CONTAINER_PX * 0.98);
  });
});
