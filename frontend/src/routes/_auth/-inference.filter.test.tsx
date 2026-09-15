// @vitest-environment jsdom
// Forwarding `?capability=all` would 400 the list (the backend filter is a
// `UUIDFilter`), and re-filtering a fetched page client-side would leave `count`
// describing a different set than `results`.
// `-` prefix: not a route, per `routeFileIgnorePrefix`.
import type { ReactNode } from "react";

import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

// `Route` is the same object the component reads its search + navigate off.
const routeState = vi.hoisted(() => ({
  navigate: vi.fn(),
  search: {} as Record<string, unknown>,
}));

vi.mock("@tanstack/react-router", () => ({
  createFileRoute: () => (options: { component: () => ReactNode }) => ({
    options,
    useNavigate: () => routeState.navigate,
    useSearch: () => routeState.search,
  }),
  Link: ({ children }: { children?: ReactNode }) => <>{children}</>,
  useNavigate: () => routeState.navigate,
}));

const deployedModelsQuery = vi.hoisted(() => vi.fn());
vi.mock("@/hooks/use-inference", () => ({
  useDeployedModelsQuery: (params: unknown) => {
    deployedModelsQuery(params);
    return { data: { count: 0, results: [] }, isLoading: false };
  },
  useModelLiveQuery: () => ({ data: undefined }),
}));

// The table isn't under test — only the props it is handed.
const tableProps = vi.hoisted(() => vi.fn());
vi.mock("@/components/ui/data-table", () => ({
  DataTable: (props: { toolbar?: ReactNode }) => {
    tableProps(props);
    return <div>{props.toolbar}</div>;
  },
}));

vi.mock("@/hooks/use-evaluations", () => ({
  useProjectCapabilitiesQuery: () => ({
    data: { results: [{ id: "capability-1", name: "billing-bot" }] },
  }),
}));

// Same mock, same reason, as `-inference.search.test.ts`.
vi.mock("@/components/model-provider-chip", () => ({
  getModelProviderInfo: (id: string) => ({ id, modelLabel: id, providerLabel: id }),
  ModelProviderChip: () => null,
}));

import { Route } from "./inference";

const InferencePage = (Route as unknown as { options: { component: () => ReactNode } }).options
  .component;

// `useDebouncedValue` is deliberately NOT mocked: the mount guard below holds
// only because the real hook seeds with the incoming value.
const renderPage = (search: Record<string, unknown>) => {
  routeState.search = {
    inf_capability: "all",
    inf_search: "",
    page: 1,
    page_size: 25,
    projectId: "proj-1",
    ...search,
  };
  return render(<InferencePage />);
};

const lastQueryParams = () => deployedModelsQuery.mock.calls.at(-1)?.[0];
const lastTableProps = () => tableProps.mock.calls.at(-1)?.[0];
const searchBox = () => screen.getByLabelText<HTMLInputElement>("Search models");
const type = (value: string) => act(() => fireEvent.change(searchBox(), { target: { value } }));

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  routeState.navigate.mockClear();
  deployedModelsQuery.mockClear();
  tableProps.mockClear();
});

describe("Inference capability filter request scope", () => {
  it("sends no capability param while the list is unfiltered", () => {
    renderPage({});

    expect(lastQueryParams()).toMatchObject({ capability: undefined, projectId: "proj-1" });
  });

  it("scopes the request to the selected capability", () => {
    renderPage({ inf_capability: "capability-1" });

    expect(lastQueryParams()).toMatchObject({ capability: "capability-1" });
  });

  // `DeployedModelFilter.capability` matches this sentinel against a null join rather
  // than parsing it as a UUID, so stripping it would silently unfilter the list.
  it("forwards the no-capability sentinel rather than stripping it", () => {
    renderPage({ inf_capability: "__none__" });

    expect(lastQueryParams()).toMatchObject({ capability: "__none__" });
  });
});

describe("Inference capability filter table wiring", () => {
  // A deep-linked page 3 outside the narrowed list would render as "no results".
  it("returns to the first page when the filter changes", () => {
    renderPage({ page: 3 });

    lastTableProps().toolbar.props.onCapabilityFilterChange("capability-1");

    const { search } = routeState.navigate.mock.calls[0][0];
    expect(search({ inf_capability: "all", page: 3 })).toEqual({
      inf_capability: "capability-1",
      page: 1,
    });
  });
});

// `?search=` runs against the viewset's `search_fields` (`model_id`,
// `base_model_id`, `finetuning_job__name`); a client-side pass would leave a
// match on page 2 unfindable.
describe("Inference search request scope", () => {
  it("forwards the committed search term", () => {
    renderPage({ inf_search: "llama" });

    expect(lastQueryParams()).toMatchObject({ search: "llama" });
  });

  it("keeps the request on the committed term while a draft is in flight", () => {
    vi.useFakeTimers();
    renderPage({ inf_search: "llama" });

    type("llama-3-8b");

    expect(lastQueryParams()).toMatchObject({ search: "llama" });
    expect(routeState.navigate).not.toHaveBeenCalled();
  });

  // 400ms, matching the training, evaluations, optimiser and datasets lists.
  it("publishes the settled term to the url, back on page 1", () => {
    vi.useFakeTimers();
    renderPage({ page: 3 });

    type("llama");
    expect(routeState.navigate).not.toHaveBeenCalled();
    act(() => vi.advanceTimersByTime(400));

    const { search } = routeState.navigate.mock.calls[0][0];
    expect(search({ inf_search: "", page: 3 })).toEqual({ inf_search: "llama", page: 1 });
  });

  // The debounce seeds with the incoming value, so an unguarded effect would
  // fire on mount and reset a deep-linked page to 1.
  it("leaves a deep-linked page alone on mount", () => {
    vi.useFakeTimers();
    renderPage({ inf_search: "llama", page: 3 });

    act(() => vi.advanceTimersByTime(400));

    expect(routeState.navigate).not.toHaveBeenCalled();
  });

  // Waiting 400ms to unfilter reads as a dropped click.
  it("commits the field's own clear immediately", () => {
    vi.useFakeTimers();
    renderPage({ inf_search: "llama", page: 3 });

    act(() => lastTableProps().toolbar.props.onSearchClear());

    const { search } = routeState.navigate.mock.calls[0][0];
    expect(search({ inf_search: "llama", page: 3 })).toEqual({ inf_search: "", page: 1 });
  });
});

describe("Inference search table wiring", () => {
  // Without the flag a narrowed list falls to the "No deployed models" state,
  // which reads as data loss and offers no way back out.
  it.each([
    ["nothing", {}, false],
    ["a capability", { inf_capability: "capability-1" }, true],
    ["a committed search", { inf_search: "nope" }, true],
    ["a status", { status: "failed" }, true],
  ])("narrowed by %s: %o reads as filtered=%s", (_label, search, filtered) => {
    renderPage(search);

    expect(lastTableProps().hasActiveFilters).toBe(filtered);
  });

  it("counts an uncommitted draft, unless it is only whitespace", () => {
    vi.useFakeTimers();
    renderPage({});

    type("   ");
    expect(lastTableProps().hasActiveFilters).toBe(false);

    type("ll");
    expect(lastTableProps().hasActiveFilters).toBe(true);
  });

  it("clears a search and a capability filter together", () => {
    vi.useFakeTimers();
    renderPage({ inf_capability: "capability-1", inf_search: "llama", page: 3 });

    expect(lastTableProps().hasActiveFilters).toBe(true);
    act(() => lastTableProps().onClearFilters());

    const { search } = routeState.navigate.mock.calls[0][0];
    expect(search({ inf_capability: "capability-1", inf_search: "llama", page: 3 })).toEqual({
      inf_capability: "all",
      inf_search: "",
      page: 1,
    });
    // A stale draft would keep Clear on screen over an unfiltered list.
    expect(searchBox().value).toBe("");
  });

  it("keeps both filters on the request at once", () => {
    renderPage({ inf_capability: "capability-1", inf_search: "llama" });

    expect(lastQueryParams()).toMatchObject({ capability: "capability-1", search: "llama" });
  });
});
