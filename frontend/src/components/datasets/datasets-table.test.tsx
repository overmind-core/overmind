// @vitest-environment jsdom
// covers: console-logic

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { TooltipProvider } from "@/components/ui/tooltip";
import type { Dataset } from "@/openapi";
import { DatasetsBrowser } from "./datasets-table";

vi.mock("@tanstack/react-router", () => ({
  Link: ({ children }: { children: unknown }) => children,
  useNavigate: () => vi.fn(),
}));
vi.stubGlobal(
  "ResizeObserver",
  class {
    observe() {}
    unobserve() {}
    disconnect() {}
  }
);

afterEach(cleanup);

const dataset = (fields: Partial<Dataset>): Dataset =>
  ({
    activeVersion: "1.0",
    capability: null,
    capabilityName: null,
    cells: [],
    id: crypto.randomUUID(),
    intent: "pending",
    name: "rows",
    roles: [],
    rows: 10,
    sourceKind: "file",
    sourceSpec: {},
    state: "idle",
    trainFormat: "",
    updatedAt: "2026-11-18T10:00:00Z",
    work: null,
    ...fields,
  }) as unknown as Dataset;

function table(datasets: Dataset[]) {
  render(
    <QueryClientProvider client={new QueryClient()}>
      <TooltipProvider>
        <DatasetsBrowser
          datasets={datasets}
          isLoading={false}
          roleFilter="all"
          search=""
          setRoleFilter={vi.fn()}
          setSearch={vi.fn()}
        />
      </TooltipProvider>
    </QueryClientProvider>
  );
  return (name: string) => within(screen.getByText(name).closest("tr") as HTMLElement);
}

describe("datasets table", () => {
  it("shows every row's role, count and source in one shape", () => {
    const row = table([
      dataset({
        intent: "train",
        name: "Tickets",
        roles: ["training"],
        rows: 48_213,
        sourceSpec: {
          sources: [{ filename: "a.csv" }, { filename: "b.csv" }, { filename: "c.csv" }],
        },
        trainFormat: "sft",
      }),
      dataset({ name: "Calls", sourceKind: "traces" }),
      dataset({ activeVersion: undefined, name: "Draft", sourceKind: "pending" }),
      dataset({ name: "Split source", roles: ["training", "evaluation"] }),
    ]);

    expect(row("Tickets").getByText("Train · SFT")).toBeTruthy();
    expect(row("Tickets").getByLabelText("Used")).toBeTruthy();
    expect(row("Tickets").getByText("48.2K")).toBeTruthy();
    expect(row("Tickets").getByText("a.csv")).toBeTruthy();
    expect(row("Tickets").getByText("+2")).toBeTruthy();
    expect(row("Calls").getByText("Traces")).toBeTruthy();
    expect(row("Calls").queryByLabelText("Used")).toBeNull();
    // A draft has no role, count or source: each shows the same dash.
    expect(row("Draft").getAllByText("—")).toHaveLength(3);
    // With no intent, a used dataset shows what it was used for, and rows sent inline say so.
    expect(row("Split source").getByText("Train + Eval")).toBeTruthy();
    expect(row("Split source").getByText("Inline rows")).toBeTruthy();
  });

  it("shows running work and its measured count beside the name", () => {
    const row = table([
      dataset({
        name: "Orders",
        work: {
          actorKind: null,
          completed: 340_000,
          label: "Writing",
          status: "running",
          total: 1_000_000,
          unit: "rows",
        },
      } as Partial<Dataset>),
    ]);
    expect(row("Orders").getByText("Writing · 340K of 1M rows")).toBeTruthy();
  });
});
