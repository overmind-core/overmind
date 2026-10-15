// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  createMemoryHistory,
  createRootRoute,
  createRouter,
  RouterProvider,
} from "@tanstack/react-router";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

import { TooltipProvider } from "@/components/ui/tooltip";
import { TrajectoryRunPicker } from "./TrajectoryRunPicker";
import { TrajectoryScoresProvider, useTrajectoryRun } from "./TrajectoryRunScores";

const requests: URL[] = [];
const clients: QueryClient[] = [];
const run = (traceId: string, name: string) => ({
  duration_ns: 2e9,
  end_time_ns: 12e9,
  name,
  start_time_ns: 10e9,
  status_code: 1,
  trace_id: traceId,
  trace_status: "settled",
});

beforeEach(() => {
  requests.length = 0;
  vi.stubGlobal(
    "ResizeObserver",
    class {
      observe() {}
      unobserve() {}
      disconnect() {}
    }
  );
  Element.prototype.scrollIntoView = vi.fn();
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: string | Request | URL) => {
      const url = new URL(
        typeof input === "string" ? input : input instanceof URL ? input.href : input.url
      );
      requests.push(url);
      if (url.pathname.includes("eval")) return Response.json({ count: 0, results: [] });
      if (/\/traces\/[^/]+\/$/.test(url.pathname))
        return Response.json({
          root: null,
          span_count: 0,
          spans: [],
          trace_id: "trace-1",
          trace_status: "settled",
          usage: {},
        });
      const search = url.searchParams.get("search");
      const page = url.searchParams.get("page") ?? "1";
      return Response.json({
        count: search ? 1 : 200_000,
        next: page === "1" && !search ? "http://localhost:8000/api/traces/?page=2" : null,
        results: search
          ? [run("old-trace-id", "An older run")]
          : [run(`trace-${page}`, `Recent run ${page}`)],
      });
    })
  );
});

afterEach(() => {
  cleanup();
  for (const client of clients) client.clear();
  clients.length = 0;
  vi.unstubAllGlobals();
});

const renderPicker = () => {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  clients.push(client);
  const onSelect = vi.fn();
  render(
    <QueryClientProvider client={client}>
      <TooltipProvider>
        <TrajectoryRunPicker
          capability="capability-id"
          highlighted={false}
          onSelect={onSelect}
          project="project-id"
        />
      </TooltipProvider>
    </QueryClientProvider>
  );
  return onSelect;
};

it("searches all capability runs on the server, including runs outside the first page", async () => {
  const onSelect = renderPicker();
  expect(requests).toHaveLength(0);
  fireEvent.click(screen.getByRole("button", { name: "Select trajectory run: Select run" }));
  await screen.findByText("trace-1");
  expect(screen.queryByText("Map view")).toBeNull();
  expect(screen.queryByText("Follow latest run")).toBeNull();
  expect(screen.queryByText("Recent run 1")).toBeNull();
  expect(requests[0].searchParams.get("capability")).toBe("capability-id");
  expect(requests[0].searchParams.get("project")).toBe("project-id");
  expect(requests[0].searchParams.get("page_size")).toBe("25");
  fireEvent.change(screen.getByRole("combobox", { name: "Search runs" }), {
    target: { value: "old-trace-id" },
  });
  await screen.findByText("old-trace-id");
  expect(requests.at(-1)?.searchParams.get("search")).toBe("old-trace-id");
  fireEvent.click(screen.getByText("old-trace-id"));
  expect(onSelect).toHaveBeenCalledWith("old-trace-id");
  expect(screen.queryByRole("dialog")).toBeNull();
});

it("pages through bounded server results instead of loading every interaction", async () => {
  renderPicker();
  fireEvent.click(screen.getByRole("button", { name: "Select trajectory run: Select run" }));
  await screen.findByText("trace-1");
  expect(screen.getByText("200,000 runs · page 1")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Next runs" }));
  await screen.findByText("trace-2");
  expect(requests.at(-1)?.searchParams.get("page")).toBe("2");
  await waitFor(() =>
    expect(screen.getByRole("button", { name: "Next runs" }).hasAttribute("disabled")).toBe(true)
  );
});

function RunState() {
  const run = useTrajectoryRun();
  return (
    <>
      <output aria-label="Run highlight state">{`${run.traceId ?? "none"}:${run.highlighted}`}</output>
      <output aria-label="Loaded run">{`${run.trace?.traceId ?? "none"}:${run.loading}`}</output>
    </>
  );
}

it("pins a selected run and toggles its highlight from the row or the closed-picker shortcut", async () => {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  clients.push(client);
  const route = createRootRoute({
    component: () => (
      <TrajectoryScoresProvider capability="capability-id" project="project-id">
        {(controls) => (
          <>
            {controls}
            <RunState />
          </>
        )}
      </TrajectoryScoresProvider>
    ),
  });
  const router = createRouter({
    history: createMemoryHistory({ initialEntries: ["/"] }),
    routeTree: route,
  });
  render(
    <QueryClientProvider client={client}>
      <TooltipProvider>
        <RouterProvider router={router} />
      </TooltipProvider>
    </QueryClientProvider>
  );
  await screen.findByRole("button", { name: "Select trajectory run: Select run" });
  expect(requests).toHaveLength(0);
  fireEvent.click(screen.getByRole("button", { name: "Select trajectory run: Select run" }));
  fireEvent.click(await screen.findByText("trace-1"));
  const toggle = await screen.findByRole("button", { name: "Highlight selected run" });
  expect(toggle.getAttribute("aria-pressed")).toBe("true");
  expect(screen.getByLabelText("Run highlight state").textContent).toBe("trace-1:true");
  const link = screen.getByRole("link", { name: "Open selected run in observability" });
  expect(link.getAttribute("href")).toContain("/observability/trace-1?");
  expect(link.getAttribute("href")).toContain("capability=capability-id");
  fireEvent.click(toggle);
  expect(screen.getByLabelText("Run highlight state").textContent).toBe("trace-1:false");
  fireEvent.click(screen.getByRole("button", { name: "Select trajectory run: Run trace-1" }));
  fireEvent.click(await screen.findByText("trace-1"));
  expect(screen.getByLabelText("Run highlight state").textContent).toBe("trace-1:true");
  fireEvent.click(screen.getByRole("button", { name: "Select trajectory run: Run trace-1" }));
  fireEvent.click(await screen.findByText("trace-1"));
  expect(screen.getByLabelText("Run highlight state").textContent).toBe("trace-1:false");

  await waitFor(() =>
    expect(screen.getByLabelText("Loaded run").textContent).toBe("trace-1:false")
  );
  let finishRequest!: (response: Response) => void;
  const nextTrace = new Promise<Response>((resolve) => {
    finishRequest = resolve;
  });
  const fetch = globalThis.fetch;
  vi.stubGlobal("fetch", (input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
    return url.includes("/traces/trace-2/") ? nextTrace : fetch(input, init);
  });
  fireEvent.click(screen.getByRole("button", { name: "Select trajectory run: Run trace-1" }));
  await screen.findByText("trace-1");
  await waitFor(() =>
    expect(screen.getByRole("button", { name: "Next runs" }).hasAttribute("disabled")).toBe(false)
  );
  fireEvent.click(screen.getByRole("button", { name: "Next runs" }));
  fireEvent.click(await screen.findByText("trace-2"));
  expect(screen.getByLabelText("Run highlight state").textContent).toBe("trace-2:true");
  expect(screen.getByLabelText("Loaded run").textContent).toBe("trace-1:true");
  finishRequest(
    Response.json({
      root: null,
      span_count: 0,
      spans: [],
      trace_id: "trace-2",
      trace_status: "completed",
      usage: {},
    })
  );
  await waitFor(() =>
    expect(screen.getByLabelText("Loaded run").textContent).toBe("trace-2:false")
  );
});
