// @vitest-environment jsdom
import { cleanup, render, screen } from "@testing-library/react";
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

const COLUMNS = [{ accessorKey: "name", header: "Name", id: "name", size: 100 }];

const table = (data: { count: number; results: { name: string }[] } | undefined) => (
  <DataTable
    columns={COLUMNS}
    data={data}
    emptyState={<p>nothing here yet</p>}
    isLoading={false}
    onPageChange={() => {}}
    onPageSizeChange={() => {}}
    page={1}
    pageSize={25}
  />
);

describe("DataTable empty state", () => {
  it("waits for a page to arrive before claiming there is nothing", () => {
    const { rerender } = render(table(undefined));
    expect(screen.queryByText("nothing here yet")).toBeNull();

    rerender(table({ count: 0, results: [] }));
    expect(screen.getByText("nothing here yet")).toBeTruthy();
  });
});
