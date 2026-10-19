import { useEffect, useState } from "react";
import { createRoot } from "react-dom/client";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  createMemoryHistory,
  createRootRoute,
  createRouter,
  RouterProvider,
} from "@tanstack/react-router";

import { setAsyncTokenGetter } from "@/client";
import { DatasetNotebook } from "@/components/datasets/notebook/notebook-page";
import { Button } from "@/components/ui/button";
import { PageHeader } from "@/components/ui/page-header";
import { TooltipProvider } from "@/components/ui/tooltip";
import { WorkshopSidebarContext } from "@/contexts/workshop-sidebar-context";
import "@/styles.css";

if (!import.meta.env.DEV) throw new Error("This preview is development-only.");
setAsyncTokenGetter(async () => null);

const datasetId = "local-workshop-preview";
const projectId = "local-preview";
const columns = [
  { name: "message", type: "string" },
  { name: "channel", type: "string" },
  { name: "decision", type: "string" },
];
const rows = [
  {
    channel: "Email",
    decision: "Review",
    message:
      "Please review the invoice attached to your account. This is a synthetic example for the local interface preview.",
  },
  {
    channel: "SMS",
    decision: "Allow",
    message: "Your delivery is scheduled for tomorrow morning.",
  },
  {
    channel: "Email",
    decision: "Review",
    message: "Confirm your password immediately to keep your account active.",
  },
  { channel: "Email", decision: "Allow", message: "The weekly team agenda is ready." },
].map((row, index) => ({ ...row, _index: index, source_row: index }));

let step = 0;
let revision = Date.now();
let failRefresh = false;
const listeners = new Set<ReadableStreamDefaultController<Uint8Array>>();
const encoder = new TextEncoder();
const stages = [
  "Ready · open a row, then play the updates",
  "Operation started · the canvas keeps its size",
  "Progress received · existing cells stay mounted",
  "A new preparation cell appears",
  "Preparation is running",
  "Prepared rows arrive inside their cell",
  "A decision-data cell appears",
  "Decision data published · operation complete",
];

function cell(position: number, title: string, state = "ok") {
  return {
    columns,
    created_at: new Date(0).toISOString(),
    error: "",
    fingerprint: state === "ok" ? `fixture-${position}` : "",
    fits: { ok: false, reason: "Local preview" },
    frozen: false,
    id: `preview-cell-${position}`,
    input_fingerprint: "",
    note: "",
    position,
    rows: state === "ok" ? rows.length : 0,
    script: "",
    state,
    title,
    transformation: {},
    updated_at: new Date(revision).toISOString(),
    usage: {},
    version: `1.${position}`,
  };
}
function cells() {
  return [
    cell(0, "Sample messages"),
    ...(step >= 3
      ? [cell(1, "Normalise messages", step < 4 ? "queued" : step < 5 ? "running" : "ok")]
      : []),
    ...(step >= 6 ? [cell(2, "Decision training rows", step < 7 ? "running" : "ok")] : []),
  ];
}
function dataset() {
  const output = cells();
  return {
    active: output.filter((item) => item.state === "ok").at(-1)?.id,
    cells: output,
    created_at: new Date(0).toISOString(),
    error: "",
    id: datasetId,
    intent: "explore",
    name: "Workshop local preview",
    project: projectId,
    rows: 4,
    source_kind: "file",
    source_spec: { sources: [{ filename: "synthetic-messages.csv", id: "fixture", rows: 4 }] },
    state: step > 0 && step < 7 ? "running" : "idle",
    updated_at: new Date(revision).toISOString(),
  };
}
function emit(type: string) {
  const data = encoder.encode(`data: ${JSON.stringify({ type })}\n\n`);
  for (const listener of listeners) listener.enqueue(data);
}
function advance(next: number) {
  step = next;
  revision = Date.now();
  window.dispatchEvent(new Event("preview-step"));
  emit(next === 1 ? "run_started" : next === 7 ? "run_done" : "dataset_changed");
}

// Every fetch is answered locally, including unrecognised requests. No API fallback.
window.fetch = async (input, init) => {
  const url = new URL(input instanceof Request ? input.url : String(input), location.origin);
  const path = url.pathname;
  const signal = init?.signal ?? (input instanceof Request ? input.signal : undefined);
  if (path.endsWith("/events/")) {
    let controller: ReadableStreamDefaultController<Uint8Array>;
    const stream = new ReadableStream<Uint8Array>({
      cancel() {
        listeners.delete(controller);
      },
      start(value) {
        controller = value;
        listeners.add(value);
        value.enqueue(encoder.encode('data: {"type":"replay.done"}\n\n'));
        signal?.addEventListener(
          "abort",
          () => {
            listeners.delete(value);
            value.close();
          },
          { once: true }
        );
      },
    });
    return new Response(stream, { headers: { "Content-Type": "text/event-stream" } });
  }
  await new Promise<void>((resolve, reject) => {
    if (signal?.aborted) {
      reject(new DOMException("Aborted", "AbortError"));
      return;
    }
    const timer = setTimeout(resolve, path.endsWith("/rows/") ? 1000 : 500);
    signal?.addEventListener(
      "abort",
      () => {
        clearTimeout(timer);
        reject(new DOMException("Aborted", "AbortError"));
      },
      { once: true }
    );
  });
  if (path.endsWith("/cancel/")) {
    advance(7);
    return Response.json(dataset());
  }
  if ((init?.method ?? "GET").toUpperCase() !== "GET") {
    return Response.json(
      { detail: "This local preview does not perform this action." },
      { status: 403 }
    );
  }
  if (path.endsWith("/preparation/")) {
    if (failRefresh) return Response.json({ detail: "Simulated refresh failure" }, { status: 503 });
    const output = cells();
    return Response.json({
      edges: output
        .slice(1)
        .map((item, index) => ({ label: "", source: output[index].id, target: item.id })),
      nodes: output.map((item) => ({
        cell: item,
        dataset: datasetId,
        dataset_name: "Local preview",
        intent: "explore",
        role: "",
      })),
      selected: output.at(-1)?.id,
    });
  }
  if (path.endsWith("/rows/")) {
    const search = (url.searchParams.get("search") ?? "").toLowerCase();
    const filtered = rows.filter((row) => JSON.stringify(row).toLowerCase().includes(search));
    const offset = Number(url.searchParams.get("offset") ?? 0);
    const limit = Number(url.searchParams.get("limit") ?? 10);
    return Response.json({
      columns,
      limit,
      marks: {},
      offset,
      rows: filtered.slice(offset, offset + limit),
      total: filtered.length,
    });
  }
  if (path.endsWith("/columns/")) return Response.json([]);
  if (path === `/api/datasets/${datasetId}/`) return Response.json(dataset());
  if (path === "/api/capabilities/") return Response.json({ count: 0, results: [] });
  return Response.json({ detail: `No local fixture for ${path}` }, { status: 404 });
};

const queryClient = new QueryClient({
  defaultOptions: { queries: { refetchOnWindowFocus: false, retry: false } },
});
function Preview() {
  const [current, setCurrent] = useState(step);
  const [playing, setPlaying] = useState(false);
  const [epoch, setEpoch] = useState(0);
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    const update = () => {
      setCurrent(step);
      if (step === 7) setPlaying(false);
    };
    window.addEventListener("preview-step", update);
    return () => window.removeEventListener("preview-step", update);
  }, []);
  useEffect(() => {
    if (!playing) return;
    const timer = setInterval(() => advance(Math.min(step + 1, 7)), 2200);
    return () => clearInterval(timer);
  }, [playing]);
  const reset = () => {
    setPlaying(false);
    failRefresh = false;
    setFailed(false);
    step = 0;
    revision = Date.now();
    setCurrent(0);
    queryClient.clear();
    setEpoch((value) => value + 1);
  };
  return (
    <div className="flex h-dvh flex-col gap-3 bg-background p-4 text-foreground">
      <PageHeader
        description="Local simulation · synthetic messages · no live API calls"
        title="Workshop preview"
      />
      <div className="flex flex-wrap items-center gap-2">
        <Button disabled={current === 7} onClick={() => setPlaying(!playing)} size="sm">
          {playing ? "Pause" : "Play updates"}
        </Button>
        <Button
          disabled={playing || current === 7}
          onClick={() => advance(step + 1)}
          size="sm"
          variant="secondary"
        >
          Next update
        </Button>
        <Button onClick={reset} size="sm" variant="secondary">
          Replay loading
        </Button>
        <Button
          onClick={() => {
            failRefresh = !failed;
            setFailed(!failed);
            emit("dataset_changed");
          }}
          size="sm"
          variant="secondary"
        >
          {failed ? "Recover refresh" : "Simulate refresh failure"}
        </Button>
      </div>
      <p aria-live="polite" className="min-h-10 text-sm text-muted-foreground">
        {current + 1} / {stages.length} · {stages[current]}
      </p>
      <div className="relative min-h-0 flex-1 overflow-hidden rounded-md border border-border bg-card">
        <DatasetNotebook datasetId={datasetId} key={epoch} projectId={projectId} />
      </div>
      <p className="text-xs text-muted-foreground">
        Try expanding a row or changing zoom before playing. Use Fit view to see newly added cells.
      </p>
    </div>
  );
}
const route = createRootRoute({ component: Preview });
const router = createRouter({
  history: createMemoryHistory({ initialEntries: ["/"] }),
  routeTree: route,
});
createRoot(document.getElementById("app")!).render(
  <QueryClientProvider client={queryClient}>
    <TooltipProvider>
      <WorkshopSidebarContext.Provider value={{ close: () => {}, open: false, toggle: () => {} }}>
        <RouterProvider router={router} />
      </WorkshopSidebarContext.Provider>
    </TooltipProvider>
  </QueryClientProvider>
);
