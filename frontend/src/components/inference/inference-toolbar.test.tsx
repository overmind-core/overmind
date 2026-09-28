// @vitest-environment jsdom
import type { ComponentProps } from "react";

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { DeployedModelsListStatusEnum } from "@/openapi";

// Radix Select needs a layout engine jsdom lacks. The trigger keeps its label
// so the tests can tell the two selects apart.
vi.mock("@/components/ui/select", () => ({
  Select: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  SelectContent: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  SelectItem: ({ children, value }: { children: React.ReactNode; value: string }) => (
    <div data-value={value}>{children}</div>
  ),
  SelectTrigger: ({ "aria-label": label }: { "aria-label"?: string }) => <div aria-label={label} />,
  SelectValue: () => null,
}));

const capabilities = vi.hoisted(() => ({
  rows: [
    { id: "capability-2", name: "checkout-bot" },
    { id: "capability-1", name: "billing-bot" },
  ] as { id: string; name?: string }[],
}));

vi.mock("@/hooks/use-evaluations", () => ({
  useProjectCapabilitiesQuery: () => ({ data: { results: capabilities.rows } }),
}));

import { InferenceToolbar, NO_CAPABILITY } from "./inference-toolbar";

afterEach(() => {
  cleanup();
  capabilities.rows = [
    { id: "capability-2", name: "checkout-bot" },
    { id: "capability-1", name: "billing-bot" },
  ];
});

const renderToolbar = (props: Partial<ComponentProps<typeof InferenceToolbar>> = {}) =>
  render(
    <InferenceToolbar
      capabilityFilter="all"
      hasActiveFilters={false}
      onCapabilityFilterChange={vi.fn()}
      onClearFilters={vi.fn()}
      onSearchChange={vi.fn()}
      onSearchClear={vi.fn()}
      onStatusFilterChange={vi.fn()}
      projectId="proj-1"
      search=""
      {...props}
    />
  );

const STATUS_OPTION_VALUES = new Set<string>(Object.values(DeployedModelsListStatusEnum));

const capabilityOptions = () =>
  [...document.querySelectorAll("[data-value]")]
    .filter((el) => {
      const value = el.getAttribute("data-value") ?? "";
      if (STATUS_OPTION_VALUES.has(value)) return false;
      // Status select's "all" is "All statuses"; capability select is "All capabilities".
      if (value === "all") return el.textContent === "All capabilities";
      return true;
    })
    .map((el) => ({
      label: el.textContent,
      value: el.getAttribute("data-value"),
    }));

describe("InferenceToolbar capability filter", () => {
  it("offers the project's capabilities by name, sorted, between the all and none buckets", () => {
    renderToolbar();

    expect(capabilityOptions()).toEqual([
      { label: "All capabilities", value: "all" },
      { label: "billing-bot", value: "capability-1" },
      { label: "checkout-bot", value: "capability-2" },
      { label: "No capability", value: NO_CAPABILITY },
    ]);
  });

  // The bucket is resolved server-side, so its absence from the capability list must
  // not label it "Unknown capability".
  it("treats the none bucket as a real selection, not a stale id", () => {
    renderToolbar({ capabilityFilter: NO_CAPABILITY });

    expect(document.querySelector("[data-value='__none__']")?.textContent).toBe("No capability");
    expect(screen.queryByText("Unknown capability")).toBeNull();
  });

  // `Capability.name` is blank=True/default="" on the backend.
  it("labels an unnamed capability instead of rendering a blank option", () => {
    capabilities.rows = [{ id: "capability-unnamed-01", name: "" }];

    renderToolbar();

    expect(document.querySelector("[data-value='capability-unnamed-01']")?.textContent).toBe(
      "Capability capabili"
    );
  });

  // A `?inf_capability=` the project's capabilities don't carry still scopes the request,
  // so the select must stay mounted and name it or there is nothing to clear.
  it("keeps a capability the project list doesn't carry selectable", () => {
    capabilities.rows = [];

    renderToolbar({ capabilityFilter: "capability-gone" });

    expect(document.querySelector("[data-value='capability-gone']")?.textContent).toBe(
      "Unknown capability"
    );
  });

  // With no named capabilities every deployment is unassigned, so both remaining
  // options would mean "everything" — but the deployments stay worth searching.
  it("drops the select but keeps the search box when the project has no capabilities", () => {
    capabilities.rows = [];

    renderToolbar();

    expect(document.querySelector("[aria-label='Filter by capability']")).toBeNull();
    expect(screen.getByLabelText("Search models")).toBeTruthy();
  });

  it("stays mounted for an active none-bucket filter even with no named capabilities", () => {
    capabilities.rows = [];

    renderToolbar({ capabilityFilter: NO_CAPABILITY, hasActiveFilters: true });

    expect(document.querySelector("[aria-label='Filter by capability']")).not.toBeNull();
    expect(screen.getByRole("button", { name: /Clear filters/ })).toBeTruthy();
  });
});

describe("InferenceToolbar search box", () => {
  it("renders the draft it is handed", () => {
    renderToolbar({ search: "llama" });

    expect(screen.getByLabelText<HTMLInputElement>("Search models").value).toBe("llama");
  });

  it("offers the field's own clear only while it has text", () => {
    renderToolbar();
    expect(screen.queryByRole("button", { name: "Clear search" })).toBeNull();

    cleanup();
    const onSearchClear = vi.fn();
    renderToolbar({ onSearchClear, search: "llama" });
    screen.getByRole("button", { name: "Clear search" }).click();

    expect(onSearchClear).toHaveBeenCalled();
  });
});

describe("InferenceToolbar clear affordance", () => {
  it("stays hidden while the list is unfiltered", () => {
    renderToolbar();

    expect(screen.queryByRole("button", { name: /Clear filters/ })).toBeNull();
  });

  it("clears back to all capabilities once a filter is active", () => {
    const onClearFilters = vi.fn();

    renderToolbar({ capabilityFilter: "capability-1", hasActiveFilters: true, onClearFilters });
    screen.getByRole("button", { name: /Clear filters/ }).click();

    expect(onClearFilters).toHaveBeenCalled();
  });

  // The route folds the search draft into the same flag, so there is one clear
  // control, not a second search-specific one.
  it("appears for a search with no capability filter", () => {
    renderToolbar({ hasActiveFilters: true, search: "llama" });

    expect(screen.getByRole("button", { name: /Clear filters/ })).toBeTruthy();
  });
});

// The debounced search reshuffles the table a beat after the last keystroke —
// silent unless you can see the rows.
describe("InferenceToolbar result count", () => {
  it("announces the match count politely", () => {
    renderToolbar({ resultCount: 12 });

    const live = document.querySelector("[aria-live='polite']");
    expect(live?.textContent).toBe("12 models");
    expect(live?.className).toContain("sr-only");
  });

  it("keeps the noun singular for a single match", () => {
    renderToolbar({ resultCount: 1 });

    expect(document.querySelector("[aria-live='polite']")?.textContent).toBe("1 model");
  });

  // A count of nothing and an unknown count are different claims.
  it("says nothing until the count is known", () => {
    renderToolbar();

    expect(document.querySelector("[aria-live='polite']")).toBeNull();
  });
});
