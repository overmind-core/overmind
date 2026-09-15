// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";

import { DataTable } from "@/components/ui/data-table";

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
  { accessorKey: "name", header: "Name", id: "name", size: 100 },
  { accessorKey: "status", header: "Status", id: "status", size: 100 },
];

const ROWS = { count: 1, results: [{ name: "a", status: "ok" }] };

describe("DataTable column visibility", () => {
  it("hides a column from the toolbar when visibility is not controlled", () => {
    render(
      <DataTable
        columns={COLUMNS}
        data={ROWS}
        onPageChange={() => {}}
        onPageSizeChange={() => {}}
        page={1}
        pageSize={25}
        toolbar={(table) => (
          <button onClick={() => table.getColumn("status")?.toggleVisibility(false)} type="button">
            Hide status
          </button>
        )}
      />
    );

    expect(screen.getByRole("columnheader", { name: "Status" })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Hide status" }));
    expect(screen.queryByRole("columnheader", { name: "Status" })).toBeNull();
    expect(screen.getByRole("columnheader", { name: "Name" })).toBeTruthy();
  });
});
