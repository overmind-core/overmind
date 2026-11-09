// @vitest-environment jsdom
// covers: console-logic
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

import { TooltipProvider } from "@/components/ui/tooltip";
import { type Cell, type WorkshopMessage, WorkshopMessageToJSON } from "@/openapi";
import { WorkshopAgent } from "./workshop-agent";

const calls = vi.hoisted(() => ({
  control: vi.fn(),
  list: vi.fn(),
  queue: vi.fn(),
  send: vi.fn(),
  stream: vi.fn(),
}));
vi.mock("@/client", () => ({
  default: {
    workshopTasks: {
      workshopTasksControlCreate: calls.control,
      workshopTasksEventsRetrieveRaw: calls.stream,
      workshopTasksList: calls.list,
      workshopTasksMessagesControlCreate: calls.queue,
      workshopTasksMessagesCreate: calls.send,
    },
  },
}));
let qc: QueryClient;
let stream: ReadableStreamDefaultController<Uint8Array>;
let sequence: number;
const task = {
  desiredState: "running",
  id: "task",
  objective: "Prepare this data",
  result: { activity: "Checking missing values" },
  state: "running",
};
const cell = { id: "cell", rows: 3, title: "Clean dates" } as Cell;
beforeEach(() => {
  vi.resetAllMocks();
  vi.stubGlobal(
    "matchMedia",
    vi.fn(() => ({ addEventListener: vi.fn(), matches: false, removeEventListener: vi.fn() }))
  );
  vi.stubGlobal(
    "ResizeObserver",
    class {
      observe() {}
      unobserve() {}
      disconnect() {}
    }
  );
  HTMLElement.prototype.scrollTo = vi.fn();
  HTMLCanvasElement.prototype.getContext = vi.fn(() => ({
    measureText: (text: string) => ({ width: text.length * 7 }),
  })) as unknown as typeof HTMLCanvasElement.prototype.getContext;
  sequence = 0;
  calls.list.mockResolvedValue({ enabled: true, tasks: [task] });
  calls.send.mockResolvedValue(task);
  calls.stream.mockImplementation((_query, options) => {
    const body = new ReadableStream<Uint8Array>({
      start(controller) {
        stream = controller;
        options.signal.addEventListener(
          "abort",
          () => {
            try {
              controller.close();
            } catch {
              /* The test may already have closed this stream. */
            }
          },
          { once: true }
        );
      },
    });
    return Promise.resolve({ raw: new Response(body) });
  });
  qc = new QueryClient({
    defaultOptions: { mutations: { retry: false }, queries: { retry: false } },
  });
});
afterEach(() => {
  cleanup();
  qc.clear();
});
function setup(onSelectCell = vi.fn()) {
  render(
    <QueryClientProvider client={qc}>
      <TooltipProvider>
        <WorkshopAgent
          cells={[cell]}
          datasetId="dataset"
          onSelectCell={onSelectCell}
          projectId="project"
          selectedCell="cell"
        />
      </TooltipProvider>
    </QueryClientProvider>
  );
  return screen.getByRole("region", { name: "Workshop chat" });
}
function publish(message: WorkshopMessage, id = ++sequence, taskId = "task") {
  act(() =>
    stream.enqueue(
      new TextEncoder().encode(
        `data: ${JSON.stringify({ cursor: id, events: [{ id, message: WorkshopMessageToJSON(message), task: taskId }] })}\n\n`
      )
    )
  );
}

it("opens a floating conversation on follow-up and preserves the retry key and selected cell", async () => {
  calls.send.mockRejectedValueOnce(new Error("Connection interrupted"));
  const dock = setup();
  await screen.findByText("Checking missing values");
  publish(
    { id: "old-action", role: "activity", status: "running", text: "An interrupted old action" },
    ++sequence,
    "previous-task"
  );
  expect(screen.queryByText("An interrupted old action")).toBeNull();
  const toggle = screen.getByRole("button", { name: "Expand Workshop chat" });
  expect(toggle.querySelector("svg")).toBeNull();
  fireEvent.click(toggle);
  fireEvent.change(screen.getByLabelText("Transformation request"), {
    target: { value: "Why were these rows removed?" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Queue" }));
  await screen.findByRole("alert");
  expect(dock.getAttribute("data-panel")).toBe("true");
  const first = calls.send.mock.calls[0][0];
  fireEvent.click(screen.getByRole("button", { name: "Queue" }));
  await waitFor(() => expect(calls.send).toHaveBeenCalledTimes(2));
  expect(calls.send.mock.calls[1][0]).toEqual(first);
  expect(first.workshopMessageRequestRequest).toMatchObject({
    cell: "cell",
    dataset: "dataset",
    project: "project",
    text: "Why were these rows removed?",
  });
});

it("retains keyboard drafts and allows follow-ups while the task runs", async () => {
  const dock = setup();
  await screen.findByText("Checking missing values");
  act(() => screen.getByRole("button", { name: "Expand Workshop chat" }).focus());
  const input = screen.getByLabelText("Transformation request") as HTMLTextAreaElement;
  expect(document.activeElement).toBe(input);
  fireEvent.change(input, { target: { value: "Keep the held-out split" } });
  fireEvent.mouseLeave(dock);
  expect(screen.queryByText("Checking missing values")).toBeNull();
  expect(screen.queryByRole("button", { name: "Expand Workshop chat" })).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Open full conversation" }));
  fireEvent.click(screen.getByRole("button", { name: "Close full conversation" }));
  expect(screen.getByText("Checking missing values")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Expand Workshop chat" }));
  fireEvent.keyDown(input, { key: "Escape" });
  fireEvent.click(screen.getByRole("button", { name: "Expand Workshop chat" }));
  expect(input.value).toBe("Keep the held-out split");
  expect(input.readOnly).toBe(false);
  await waitFor(() =>
    expect((screen.getByRole("button", { name: "Queue" }) as HTMLButtonElement).disabled).toBe(
      false
    )
  );
  fireEvent.keyDown(input, { key: "Enter" });
  await waitFor(() => expect(calls.send).toHaveBeenCalledOnce());
  expect(screen.queryByRole("button", { name: "Pause" })).toBeNull();
  expect(screen.queryByRole("button", { name: "Resume" })).toBeNull();
  expect(screen.getByRole("button", { name: "Stop" })).toBeTruthy();
});

it("shows live activity before prose arrives and keeps current tool steps visible", async () => {
  setup();
  await screen.findByText("Checking missing values");
  fireEvent.click(screen.getByRole("button", { name: "Open full conversation" }));
  expect(screen.getByRole("status").textContent).toContain("Checking missing values");
  publish({
    id: "task:scan",
    role: "activity",
    status: "running",
    text: "Inspecting column types",
  });
  await screen.findByText("Inspecting column types");
  publish({
    id: "task:scan",
    role: "activity",
    status: "completed",
    text: "Inspecting column types",
  });
  await waitFor(() =>
    expect(
      screen.getByRole("button", { name: "1 tool call Done" }).getAttribute("aria-expanded")
    ).toBe("true")
  );
});

it("resizes within its bounds and retains the chosen width when the panel is reopened", async () => {
  const dock = setup();
  fireEvent.click(screen.getByRole("button", { name: "Open full conversation" }));
  const handle = screen.getByRole("separator", { name: "Chat panel width" });
  const initial = Number(handle.getAttribute("aria-valuenow"));
  fireEvent.keyDown(handle, { key: "ArrowRight" });
  const wider = Number(handle.getAttribute("aria-valuenow"));
  expect(wider).toBeGreaterThan(initial);
  expect(dock.style.getPropertyValue("--workshop-panel-width")).toBe(`${wider}px`);
  fireEvent.click(screen.getByRole("button", { name: "Close full conversation" }));
  expect(screen.queryByRole("separator")).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Open full conversation" }));
  const reopened = screen.getByRole("separator", { name: "Chat panel width" });
  expect(Number(reopened.getAttribute("aria-valuenow"))).toBe(wider);
  fireEvent.keyDown(reopened, { key: "End" });
  fireEvent.keyDown(reopened, { key: "ArrowRight", shiftKey: true });
  expect(reopened.getAttribute("aria-valuenow")).toBe(reopened.getAttribute("aria-valuemax"));
  fireEvent.keyDown(reopened, { key: "Home" });
  fireEvent.keyDown(reopened, { key: "ArrowLeft" });
  expect(reopened.getAttribute("aria-valuenow")).toBe(reopened.getAttribute("aria-valuemin"));
  fireEvent.doubleClick(reopened);
  expect(Number(reopened.getAttribute("aria-valuenow"))).toBe(initial);
  await waitFor(() => expect(calls.stream).toHaveBeenCalledOnce());
  expect(calls.send).not.toHaveBeenCalled();
});

it("updates streamed Markdown in place and focuses exact output cells", async () => {
  const select = vi.fn();
  setup(select);
  fireEvent.click(screen.getByRole("button", { name: "Expand Workshop chat" }));
  fireEvent.click(screen.getByRole("button", { name: "Open full conversation" }));
  await waitFor(() => expect(calls.stream).toHaveBeenCalledOnce());
  publish({ id: "answer", role: "assistant", status: "streaming", text: "**Checking" });
  await screen.findByText("Checking");
  publish({ id: "answer", role: "assistant", status: "completed", text: "**Checking complete**" });
  expect((await screen.findByText("Checking complete")).closest("strong")).not.toBeNull();
  expect(screen.queryByText("Checking")).toBeNull();
  publish({
    code: "assert rows == 3",
    id: "action",
    role: "activity",
    status: "completed",
    text: "Verify dates",
  });
  const tools = await screen.findByRole("button", { name: /1 tool call.*Done/ });
  if (tools.getAttribute("aria-expanded") !== "true") fireEvent.click(tools);
  fireEvent.click(screen.getByText("Verify dates"));
  expect(
    (await screen.findByText("assert rows == 3"))
      .closest('[data-slot="tool-fallback-content"]')
      ?.getAttribute("data-state")
  ).toBe("open");
  publish({
    cells: [{ dataset: "dataset", id: "cell", name: "Clean dates", rows: 3 }],
    id: "output",
    role: "publication",
    status: "completed",
    text: "Published",
  });
  fireEvent.click(await screen.findByRole("button", { name: "Show Clean dates cell" }));
  expect(select).toHaveBeenCalledWith("cell");
  publish({
    id: "linked-answer",
    role: "assistant",
    status: "completed",
    text: "See [the verified cell](cell:cell).",
  });
  fireEvent.click(await screen.findByRole("button", { name: "the verified cell" }));
  expect(select).toHaveBeenCalledTimes(2);
});

it("groups consecutive tool calls, keeps their details, and separates later turns", async () => {
  const select = vi.fn();
  setup(select);
  fireEvent.click(screen.getByRole("button", { name: "Open full conversation" }));
  await waitFor(() => expect(calls.stream).toHaveBeenCalledOnce());
  publish({ id: "inspect", role: "activity", status: "completed", text: "Inspect columns" });
  publish({
    cells: [{ dataset: "dataset", id: "cell", name: "Clean dates", rows: 3 }],
    code: "assert unique_ids",
    id: "verify",
    role: "activity",
    status: "running",
    text: "Check source identities",
    tool: "execute_python",
  });
  const steps = await screen.findByRole("button", { name: /2 tool calls.*Running/ });
  expect(steps.getAttribute("aria-expanded")).toBe("true");
  expect(screen.getByText("Inspect columns").closest("button")).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Show Clean dates cell" }));
  expect(select).toHaveBeenCalledWith("cell");
  fireEvent.click(screen.getByText("Check source identities"));
  expect(screen.getByText("execute_python")).toBeTruthy();
  expect(screen.getAllByText("Check source identities")).toHaveLength(1);
  expect(screen.getByText("assert unique_ids").closest("pre")).toBeTruthy();
  expect(
    screen
      .getByText("assert unique_ids")
      .closest('[data-slot="tool-fallback-content"]')
      ?.getAttribute("data-state")
  ).toBe("open");
  publish({
    error: "Duplicate source identities",
    id: "verify",
    role: "activity",
    status: "failed",
    text: "Check source identities",
    tool: "execute_python",
  });
  await screen.findByText("Duplicate source identities");
  expect(screen.getByRole("button", { name: /2 tool calls.*Failed/ })).toBe(steps);
  fireEvent.click(steps);
  publish({ id: "user", role: "user", status: "delivered", text: "Explain the duplicates" });
  publish({ id: "explain", role: "activity", status: "running", text: "Inspect duplicates" });
  expect(await screen.findByRole("button", { name: /1 tool call.*Running/ })).toBeTruthy();
  expect(steps.getAttribute("aria-expanded")).toBe("false");
});

it("reconnects from the retained cursor without duplicating the transcript", async () => {
  setup();
  fireEvent.click(screen.getByRole("button", { name: "Expand Workshop chat" }));
  fireEvent.click(screen.getByRole("button", { name: "Open full conversation" }));
  await waitFor(() => expect(calls.stream).toHaveBeenCalledOnce());
  publish({ id: "answer", role: "assistant", status: "streaming", text: "First observation" });
  await screen.findByText("First observation");
  act(() => stream.close());
  await waitFor(() => expect(calls.stream).toHaveBeenCalledTimes(2), { timeout: 2500 });
  expect(calls.stream.mock.calls[1][0]).toMatchObject({
    after: 1,
    dataset: "dataset",
    project: "project",
  });
  publish({ id: "answer", role: "assistant", status: "streaming", text: "First observation" }, 1);
  publish(
    { id: "answer", role: "assistant", status: "completed", text: "Observation verified" },
    2
  );
  await screen.findByText("Observation verified");
  expect(screen.queryByText("First observation")).toBeNull();
  expect(screen.getAllByText("Observation verified")).toHaveLength(1);
});

it("edits and removes queued messages with retained retry keys and revision checks", async () => {
  setup();
  fireEvent.click(screen.getByRole("button", { name: "Open full conversation" }));
  await waitFor(() => expect(calls.stream).toHaveBeenCalledOnce());
  publish({
    id: "user:queued",
    revision: 0,
    role: "user",
    status: "pending",
    text: "Keep 10 rows",
  });
  fireEvent.click(await screen.findByRole("button", { name: "Edit queued message 1" }));
  expect(document.activeElement).toBe(screen.getByLabelText("Edit queued message"));
  fireEvent.change(screen.getByLabelText("Edit queued message"), {
    target: { value: "Keep all rows" },
  });
  calls.queue.mockRejectedValueOnce(new Error("Connection interrupted"));
  fireEvent.click(screen.getByRole("button", { name: "Save" }));
  await screen.findByRole("alert");
  calls.queue.mockResolvedValue({});
  fireEvent.click(screen.getByRole("button", { name: "Save" }));
  await waitFor(() => expect(calls.queue).toHaveBeenCalledTimes(2));
  await waitFor(() =>
    expect(document.activeElement).toBe(
      screen.getByRole("button", { name: "Edit queued message 1" })
    )
  );
  expect(calls.queue.mock.calls[0][0]).toEqual(calls.queue.mock.calls[1][0]);
  expect(calls.queue.mock.calls[0][0]).toMatchObject({
    id: "task",
    workshopMessageControlRequest: {
      command: "edit",
      message: "user:queued",
      revision: 0,
      text: "Keep all rows",
    },
  });
  publish({
    id: "user:queued",
    revision: 1,
    role: "user",
    status: "pending",
    text: "Keep all rows",
  });
  await screen.findByText("Keep all rows");
  fireEvent.click(await screen.findByRole("button", { name: "Remove queued message 1" }));
  await waitFor(() => expect(calls.queue).toHaveBeenCalledTimes(3));
  expect(calls.queue.mock.calls[2][0].workshopMessageControlRequest).toMatchObject({
    command: "remove",
    revision: 1,
  });
  publish({
    id: "user:queued",
    revision: 2,
    role: "user",
    status: "removed",
    text: "Keep all rows",
  });
  await waitFor(() => expect(screen.queryByRole("region", { name: "Queued messages" })).toBeNull());
});

it("stops the run without losing the draft or allowing another submission during cancellation", async () => {
  setup();
  fireEvent.click(screen.getByRole("button", { name: "Expand Workshop chat" }));
  await screen.findByRole("button", { name: "Stop" });
  const input = screen.getByLabelText("Transformation request") as HTMLTextAreaElement;
  fireEvent.change(input, { target: { value: "Keep this draft" } });
  const stopping = { ...task, desiredState: "cancelled" };
  calls.control.mockResolvedValue(stopping);
  calls.list.mockResolvedValue({ enabled: true, tasks: [stopping] });
  fireEvent.click(screen.getByRole("button", { name: "Stop" }));
  await waitFor(() =>
    expect(calls.control).toHaveBeenCalledWith({
      id: "task",
      workshopControlRequest: { command: "cancel" },
    })
  );
  expect(
    ((await screen.findByRole("button", { name: "Stopping…" })) as HTMLButtonElement).disabled
  ).toBe(true);
  expect((screen.getByRole("button", { name: "Queue" }) as HTMLButtonElement).disabled).toBe(true);
  expect(input.value).toBe("Keep this draft");
  fireEvent.keyDown(input, { key: "Enter" });
  expect(calls.send).not.toHaveBeenCalled();
});

it("renders retained Tool UI results and submits a source-bound answer without clearing the composer", async () => {
  const fixture = (await import("./fixtures/workshop-presentation.json")).default;
  const select = vi.fn();
  setup(select);
  await screen.findByText("Checking missing values");
  fireEvent.click(screen.getByRole("button", { name: "Expand Workshop chat" }));
  fireEvent.click(screen.getByRole("button", { name: "Open full conversation" }));
  publish({
    cells: [
      {
        dataset: "dataset",
        id: "158f6e56-b105-40f2-8811-a1950f30aff5",
        name: "Passengers",
        rows: 891,
      },
    ],
    id: "presentation:1",
    presentation: fixture,
    role: "assistant",
    status: "completed",
    text: "",
  });
  await screen.findByText("Passenger class", {}, { timeout: 10000 });
  expect(screen.queryByRole("alert")).toBeNull();
  const chart = screen.getByRole("figure", { name: "Passenger class" });
  fireEvent.mouseDown(within(chart).getByRole("tab", { name: "Table" }), {
    button: 0,
    ctrlKey: false,
  });
  const search = await within(chart).findByRole("textbox", { name: "Search Passenger class" });
  fireEvent.change(search, { target: { value: "Second" } });
  expect(within(chart).getByRole("cell", { name: "184" })).toBeTruthy();
  expect(within(chart).queryByRole("cell", { name: "216" })).toBeNull();
  expect(within(chart).getByRole("button", { name: "Download JSON" })).toBeTruthy();
  fireEvent.mouseDown(within(chart).getByRole("tab", { name: "Chart" }), {
    button: 0,
    ctrlKey: false,
  });
  expect(within(chart).queryByRole("textbox")).toBeNull();
  fireEvent.click(screen.getAllByRole("button", { name: "Passengers" })[0]);
  expect(select).toHaveBeenCalledWith("158f6e56-b105-40f2-8811-a1950f30aff5");
  const input = screen.getByLabelText("Transformation request") as HTMLTextAreaElement;
  fireEvent.change(input, { target: { value: "Keep my separate draft" } });
  fireEvent.change(screen.getByLabelText("Context *"), {
    target: { value: "Use pre-departure facts" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Close full conversation" }));
  fireEvent.click(screen.getByRole("button", { name: "Expand Workshop chat" }));
  fireEvent.click(screen.getByRole("button", { name: "Open full conversation" }));
  expect(((await screen.findByLabelText("Context *")) as HTMLInputElement).value).toBe(
    "Use pre-departure facts"
  );
  fireEvent.click(screen.getByRole("button", { name: "Send response" }));
  await waitFor(() =>
    expect(calls.send).toHaveBeenCalledWith({
      workshopMessageRequestRequest: expect.objectContaining({
        interaction: {
          component: "intent",
          message: "task:presentation:1",
          values: { audit: false, notes: "Use pre-departure facts" },
        },
      }),
    })
  );
  expect(input.value).toBe("Keep my separate draft");
  publish({
    id: "user:reply",
    interaction: {
      component: "intent",
      message: "task:presentation:1",
      values: { audit: false, notes: "Use pre-departure facts" },
    },
    role: "user",
    status: "completed",
    text: "Use pre-departure facts",
  });
  expect(
    ((await screen.findByRole("button", { name: "Sent" })) as HTMLButtonElement).disabled
  ).toBe(true);
  fireEvent.click(screen.getByRole("tab", { name: "Counts" }));
  expect(
    within(screen.getByRole("figure", { name: "Class counts table" })).getByRole("table")
  ).toBeTruthy();
  fireEvent.change(screen.getByLabelText("Search Class counts"), { target: { value: "Third" } });
  expect(screen.queryByText("1 row")).toBeNull();
  expect(
    within(
      within(screen.getByRole("figure", { name: "Class counts table" })).getByRole("table")
    ).getByText("491")
  ).toBeTruthy();
  publish({
    id: "user:reply",
    interaction: {
      component: "intent",
      message: "task:presentation:1",
      values: { audit: false, notes: "Use pre-departure facts" },
    },
    role: "user",
    status: "removed",
    text: "Use pre-departure facts",
  });
  await waitFor(() =>
    expect(
      (screen.getByRole("button", { name: "Send response" }) as HTMLButtonElement).disabled
    ).toBe(false)
  );
  fireEvent.click(screen.getByRole("button", { name: "Review leakage" }));
  await waitFor(() =>
    expect(calls.send).toHaveBeenLastCalledWith({
      workshopMessageRequestRequest: expect.objectContaining({
        interaction: {
          component: "next",
          message: "task:presentation:1",
          values: { choice: "leakage" },
        },
      }),
    })
  );
}, 15000);

it("reorders queued messages by dragging and sends a selected message next", async () => {
  setup();
  fireEvent.click(screen.getByRole("button", { name: "Open full conversation" }));
  await waitFor(() => expect(calls.stream).toHaveBeenCalledOnce());
  for (const [index, text] of ["First request", "Second request"].entries()) {
    publish({ id: `user:${index}`, revision: 0, role: "user", status: "pending", text });
  }
  const handle = await screen.findByRole("button", { name: "Move queued message 2" });
  const transfer = { effectAllowed: "", setData: vi.fn() };
  fireEvent.dragStart(handle, { dataTransfer: transfer });
  fireEvent.drop(screen.getByText("First request"));
  await waitFor(() => expect(calls.queue).toHaveBeenCalledOnce());
  expect(calls.queue.mock.calls[0][0].workshopMessageControlRequest).toMatchObject({
    command: "move",
    message: "user:1",
    position: 0,
    revision: 0,
  });
  publish({
    id: "user:1",
    queuePosition: 0,
    revision: 1,
    role: "user",
    status: "pending",
    text: "Second request",
  });
  publish({
    id: "user:0",
    queuePosition: 1,
    revision: 1,
    role: "user",
    status: "pending",
    text: "First request",
  });
  const queue = screen.getByRole("region", { name: "Queued messages" });
  await waitFor(() =>
    expect(
      within(queue)
        .getAllByRole("paragraph")
        .map((item) => item.textContent)
    ).toEqual(["Second request", "First request"])
  );
  await waitFor(() =>
    expect(
      (within(queue).getAllByRole("button", { name: "Send" })[1] as HTMLButtonElement).disabled
    ).toBe(false)
  );
  fireEvent.click(within(queue).getAllByRole("button", { name: "Send" })[1]);
  await waitFor(() => expect(calls.queue).toHaveBeenCalledTimes(2));
  expect(calls.queue.mock.calls[1][0].workshopMessageControlRequest).toMatchObject({
    command: "move",
    message: "user:0",
    position: 0,
    revision: 1,
  });
});

it("renders metric values from the retained column key", async () => {
  const fixture = (await import("./fixtures/workshop-presentation.json")).default;
  setup();
  fireEvent.click(screen.getByRole("button", { name: "Open full conversation" }));
  await waitFor(() => expect(calls.stream).toHaveBeenCalledOnce());
  publish({
    id: "metric",
    presentation: {
      ...fixture,
      document: {
        ...fixture.document,
        root: {
          component: "Metric",
          props: { data: "counts", key: "count", label: "First-class passengers" },
        },
      },
    },
    role: "assistant",
    status: "completed",
    text: "",
  });
  const metric = await screen.findByRole("figure", { name: "First-class passengers" });
  expect(within(metric).getByText("216")).toBeTruthy();
});

it("updates one visible tool row from streamed preparation through execution", async () => {
  setup();
  fireEvent.click(screen.getByRole("button", { name: "Open full conversation" }));
  await waitFor(() => expect(calls.stream).toHaveBeenCalledOnce());
  publish({
    code: "print(",
    id: "action:0",
    role: "activity",
    status: "preparing",
    text: "Inspecting missing values",
    tool: "python",
  });
  const group = await screen.findByRole("button", { name: "1 tool call Preparing" });
  expect(group.getAttribute("aria-expanded")).toBe("true");
  expect(screen.getAllByText("python").some((item) => item.closest("button"))).toBe(true);
  publish({
    code: "print(df.isna().sum())",
    id: "action:0",
    role: "activity",
    status: "running",
    text: "Inspecting missing values",
    tool: "python",
  });
  await screen.findByRole("button", { name: "1 tool call Running" });
  publish({
    code: "print(df.isna().sum())",
    id: "action:0",
    role: "activity",
    status: "completed",
    text: "Inspecting missing values",
    tool: "python",
  });
  await screen.findByRole("button", { name: "1 tool call Done" });
  expect(screen.getAllByText("Inspecting missing values")).toHaveLength(1);
});

it("stops through the shared runtime without clearing an unsent draft", async () => {
  calls.control.mockResolvedValue({ ...task, desiredState: "cancelled" });
  setup();
  await screen.findByText("Checking missing values");
  fireEvent.click(screen.getByRole("button", { name: "Expand Workshop chat" }));
  const input = screen.getByLabelText("Transformation request") as HTMLTextAreaElement;
  fireEvent.change(input, { target: { value: "Keep this question" } });
  fireEvent.keyDown(input, { key: "Enter", shiftKey: true });
  expect(calls.send).not.toHaveBeenCalled();
  calls.list.mockResolvedValue({ enabled: true, tasks: [{ ...task, desiredState: "cancelled" }] });
  fireEvent.click(screen.getByRole("button", { name: "Stop" }));
  await waitFor(() =>
    expect(calls.control).toHaveBeenCalledWith({
      id: "task",
      workshopControlRequest: { command: "cancel" },
    })
  );
  await screen.findByRole("button", { name: "Stopping…" });
  expect(input.value).toBe("Keep this question");
  fireEvent.keyDown(input, { key: "Enter" });
  expect(calls.send).not.toHaveBeenCalled();
});

it("keeps narration, tool activity and the answer in one assistant turn", async () => {
  calls.list.mockResolvedValue({ enabled: true, tasks: [{ ...task, state: "completed" }] });
  setup();
  fireEvent.click(screen.getByRole("button", { name: "Open full conversation" }));
  await waitFor(() => expect(calls.stream).toHaveBeenCalledOnce());
  publish({
    id: "intro",
    role: "assistant",
    status: "completed",
    text: "I will inspect the source.",
  });
  publish({
    id: "scan",
    role: "activity",
    status: "completed",
    text: "Read source rows",
    tool: "python",
  });
  publish({
    id: "answer",
    role: "assistant",
    status: "completed",
    text: "The source has three rows.",
  });
  const answer = await screen.findByText("The source has three rows.");
  const turn = answer.closest('[aria-label="Overmind response"]') as HTMLElement;
  expect(within(turn).getByText("I will inspect the source.")).toBeTruthy();
  expect(within(turn).getByRole("button", { name: "1 tool call Done" })).toBeTruthy();
  expect(within(turn).getAllByRole("button", { name: "Copy response" })).toHaveLength(1);
  publish({ id: "followup", role: "user", status: "delivered", text: "Which columns?" });
  publish({ id: "columns", role: "assistant", status: "completed", text: "Name and date." });
  await screen.findByText("Name and date.");
  expect(screen.getAllByLabelText("Overmind response")).toHaveLength(2);
});

it("accepts a free-text clarification into the composer with Tab and sends the edited answer", async () => {
  setup();
  fireEvent.click(screen.getByRole("button", { name: "Open full conversation" }));
  await waitFor(() => expect(calls.stream).toHaveBeenCalledOnce());
  const fixture = (await import("./fixtures/workshop-presentation.json")).default;
  publish({
    id: "presentation:suggestion",
    presentation: {
      ...fixture,
      document: {
        data: {},
        images: {},
        root: {
          component: "Question",
          props: {
            fields: [
              {
                label: "Your request",
                name: "request",
                placeholder: "Compare passenger groups",
                required: true,
                type: "textarea",
              },
            ],
            id: "next",
            prompt: "What would you like to do next?",
          },
        },
      },
    },
    role: "assistant",
    status: "completed",
    text: "",
  });
  const input = (await screen.findByPlaceholderText(
    "Compare passenger groups"
  )) as HTMLTextAreaElement;
  expect(input.value).toBe("");
  expect(screen.queryByLabelText("Your request *")).toBeNull();
  fireEvent.keyDown(input, { key: "Tab", shiftKey: true });
  expect(input.value).toBe("");
  fireEvent.keyDown(input, { key: "Tab" });
  expect(input.value).toBe("Compare passenger groups");
  expect(calls.send).not.toHaveBeenCalled();
  fireEvent.change(input, { target: { value: "Compare passenger groups by age" } });
  fireEvent.keyDown(input, { key: "Tab" });
  expect(input.value).toBe("Compare passenger groups by age");
  fireEvent.click(screen.getByRole("button", { name: "Queue" }));
  await waitFor(() =>
    expect(calls.send).toHaveBeenCalledWith({
      workshopMessageRequestRequest: expect.objectContaining({
        interaction: {
          component: "next",
          message: "task:presentation:suggestion",
          values: { request: "Compare passenger groups by age" },
        },
        text: "Compare passenger groups by age",
      }),
    })
  );
});
