// @vitest-environment jsdom
// covers: console-logic
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, expect, it } from "vitest";

import { SortableHeader } from "@/components/ui/sortable-header";
import { Table, TableBody, TableCell, TableHeader, TableRow } from "@/components/ui/table";
import { useTableSort } from "./use-table-sort";

afterEach(cleanup);

const rows = [
  { name: "Model 10", score: 10 },
  { name: "Missing", score: null },
  { name: "Model 2", score: 2 },
  { name: "Zero", score: 0 },
  { name: "Tied", score: 2 },
];
function Fixture() {
  const { sort, onSort, sortRows } = useTableSort<"name" | "score">();
  return (
    <Table>
      <TableHeader>
        <TableRow>
          <SortableHeader label="Model" onSort={onSort} sort={sort} sortKey="name" />
          <SortableHeader label="Score" onSort={onSort} sort={sort} sortKey="score" />
        </TableRow>
      </TableHeader>
      <TableBody>
        {sortRows(rows, (row, key) => row[key]).map((row) => (
          <TableRow key={row.name}>
            <TableCell>{row.name}</TableCell>
            <TableCell>{row.score ?? "—"}</TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}
const names = () =>
  screen
    .getAllByRole("row")
    .slice(1)
    .map((row) => within(row).getAllByRole("cell")[0].textContent);

it("toggles numeric order, keeps missing values last and preserves ties and source rows", () => {
  render(<Fixture />);
  expect(names()).toEqual(rows.map((row) => row.name));
  fireEvent.click(screen.getByRole("button", { name: "Sort by Score" }));
  expect(names()).toEqual(["Zero", "Model 2", "Tied", "Model 10", "Missing"]);
  expect(screen.getByRole("columnheader", { name: "Score" }).getAttribute("aria-sort")).toBe(
    "ascending"
  );
  fireEvent.click(screen.getByRole("button", { name: "Sort by Score" }));
  expect(names()).toEqual(["Model 10", "Model 2", "Tied", "Zero", "Missing"]);
  expect(screen.getByRole("columnheader", { name: "Score" }).getAttribute("aria-sort")).toBe(
    "descending"
  );
  expect(rows[0].name).toBe("Model 10");
});

it("sorts text naturally and resets to ascending on a different column", () => {
  render(<Fixture />);
  fireEvent.click(screen.getByRole("button", { name: "Sort by Score" }));
  fireEvent.click(screen.getByRole("button", { name: "Sort by Model" }));
  expect(names()).toEqual(["Missing", "Model 2", "Model 10", "Tied", "Zero"]);
  expect(screen.getByRole("columnheader", { name: "Score" }).getAttribute("aria-sort")).toBe(
    "none"
  );
});
