// @vitest-environment jsdom
// covers: console-logic
import { useState } from "react";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { RowSelectionState } from "@tanstack/react-table";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

import { TooltipProvider } from "@/components/ui/tooltip";
import type { Dataset } from "@/openapi";
import { DatasetsBrowser, DatasetsToolbar } from "./datasets-table";
import { WorkshopComposer } from "./workshop-composer";

const calls = vi.hoisted(() => ({
  create: vi.fn(),
  inspect: vi.fn(),
  navigate: vi.fn(),
  retrieve: vi.fn(),
  start: vi.fn(),
}));
vi.mock("@tanstack/react-router", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@tanstack/react-router")>()),
  useNavigate: () => calls.navigate,
}));
vi.mock("@/client", () => ({
  default: {
    datasets: { datasetsCreate: calls.create, datasetsRetrieve: calls.retrieve },
    uploads: {
      uploadsChunkUpdate: ({ body, offset }: { body: Blob; offset: number }) =>
        Promise.resolve({ received: offset + body.size }),
      uploadsCreate: ({ beginUploadRequest }: { beginUploadRequest: { filename: string } }) =>
        Promise.resolve({
          chunkBytes: 1024,
          maxBytes: 4096,
          uploadId: beginUploadRequest.filename,
        }),
      uploadsInspectCreate: calls.inspect,
      uploadsRetrieve: () => Promise.resolve({ received: 0 }),
    },
    workshopTasks: { workshopTasksCreate: calls.start },
  },
}));

let qc: QueryClient;
beforeEach(() => {
  vi.resetAllMocks();
  Element.prototype.scrollTo = vi.fn();
  vi.stubGlobal(
    "ResizeObserver",
    class {
      observe() {}
      unobserve() {}
      disconnect() {}
    }
  );
  vi.stubGlobal(
    "matchMedia",
    vi.fn(() => ({ matches: true }))
  );
  qc = new QueryClient({
    defaultOptions: { mutations: { retry: false }, queries: { retry: false } },
  });
  calls.inspect.mockResolvedValue({ rows: 2 });
  calls.create.mockResolvedValue({ id: "dataset" });
  calls.retrieve.mockResolvedValue({ active: null, id: "dataset", state: "landing" });
});
afterEach(() => {
  cleanup();
  qc.clear();
  vi.unstubAllGlobals();
});

const existing = ["Passengers", "Ports"].map(
  (name, index) =>
    ({
      activeVersion: "v1.0",
      id: `existing-${index}`,
      name,
      roles: ["source"],
      rows: 2,
      sourceKind: "file",
      sourceSpec: {},
      state: "idle",
      updatedAt: new Date(),
    }) as Dataset
);

function SelectionComposer({ onStarted }: { onStarted: (id: string) => void }) {
  const [rowSelection, setRowSelection] = useState<RowSelectionState>({});
  const [search, setSearch] = useState("");
  const selected = Object.keys(rowSelection)
    .filter((id) => rowSelection[id])
    .map((id) => existing.find((dataset) => dataset.id === id)!);
  const filters = {
    datasets: existing,
    roleFilter: "all",
    search,
    setRoleFilter: () => {},
    setSearch,
  };
  return (
    <>
      <WorkshopComposer
        datasets={selected}
        onRemoveDataset={(id) => setRowSelection((current) => ({ ...current, [id]: false }))}
        onStarted={onStarted}
        projectId="project"
      />
      <DatasetsToolbar {...filters} />
      <DatasetsBrowser
        {...filters}
        isLoading={false}
        onRowSelectionChange={setRowSelection}
        rowSelection={rowSelection}
      />
    </>
  );
}

function setup(selection = false) {
  const started = vi.fn();
  render(
    <QueryClientProvider client={qc}>
      <TooltipProvider>
        {selection ? (
          <SelectionComposer onStarted={started} />
        ) : (
          <WorkshopComposer onStarted={started} projectId="project" />
        )}
      </TooltipProvider>
    </QueryClientProvider>
  );
  fireEvent.change(screen.getByRole("textbox", { name: "What would you like to train?" }), {
    target: { value: "Prepare a decision dataset" },
  });
  return started;
}

it("waits for all files and landed source, then retries the same agent request without recreating data", async () => {
  const started = setup();
  const send = screen.getByRole("button", { name: "Send" }) as HTMLButtonElement;
  expect(send.disabled).toBe(true);
  fireEvent.focus(screen.getByRole("group", { name: "Send" }));
  expect((await screen.findByRole("tooltip")).textContent).toMatch(/attach data/i);
  fireEvent.keyDown(screen.getByRole("textbox"), { key: "Enter" });
  expect(calls.create).not.toHaveBeenCalled();
  fireEvent.blur(screen.getByRole("group", { name: "Send" }));
  let finishInspect: (value: { rows: number }) => void = () => undefined;
  calls.inspect.mockImplementationOnce(
    () =>
      new Promise((resolve) => {
        finishInspect = resolve;
      })
  );
  fireEvent.change(screen.getByLabelText("Attach dataset files"), {
    target: { files: [new File(["a,b\n1,2"], "first.csv"), new File(["a,b\n3,4"], "second.csv")] },
  });
  await waitFor(() => expect(calls.inspect).toHaveBeenCalledTimes(1));
  expect(send.disabled).toBe(true);
  finishInspect({ rows: 1 });
  await waitFor(() => expect(send.disabled).toBe(false));
  fireEvent.click(send);
  await waitFor(() => expect(calls.retrieve).toHaveBeenCalled());
  expect(calls.create.mock.calls[0][0].datasetCreateRequest).toMatchObject({
    brief: "Prepare a decision dataset",
    intent: "pending",
    project: "project",
    source: { uploads: ["first.csv", "second.csv"] },
  });
  expect(calls.start).not.toHaveBeenCalled();
  calls.start.mockRejectedValueOnce(new Error("Connection interrupted"));
  calls.retrieve.mockResolvedValue({
    active: null,
    cells: [{ fingerprint: "source-fingerprint", id: "source-cell", state: "ok" }],
    id: "dataset",
    state: "idle",
  });
  await qc.invalidateQueries({ queryKey: ["datasets", "detail", "dataset"] });
  await screen.findByRole("alert");
  const request = calls.start.mock.calls[0][0];
  expect(started).not.toHaveBeenCalled();
  calls.start.mockResolvedValue({ id: "task" });
  fireEvent.click(screen.getByRole("button", { name: "Retry" }));
  await waitFor(() => expect(started).toHaveBeenCalledWith("dataset"));
  expect(calls.start.mock.calls[1][0]).toEqual(request);
  expect(calls.create).toHaveBeenCalledTimes(1);
});

it("keeps table selections across filters, synchronizes chip removal, and starts on existing data", async () => {
  const started = setup(true);
  calls.retrieve.mockResolvedValue({
    cells: [{ fingerprint: "hash", id: "cell", state: "ok" }],
    id: "existing-0",
    state: "idle",
  });
  calls.start.mockResolvedValue({ id: "task" });
  const send = screen.getByRole("button", { name: "Send" }) as HTMLButtonElement;
  fireEvent.click(screen.getByRole("checkbox", { name: "Attach Passengers to chat" }));
  fireEvent.click(screen.getByRole("checkbox", { name: "Attach Ports to chat" }));
  expect(calls.navigate).not.toHaveBeenCalled();
  expect(send.disabled).toBe(false);
  fireEvent.change(screen.getByRole("searchbox", { name: "Search datasets" }), {
    target: { value: "Ports" },
  });
  expect(screen.queryByRole("checkbox", { name: "Attach Passengers to chat" })).toBeNull();
  expect(screen.getByRole("button", { name: "Dataset details: Passengers" })).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Remove Ports from chat" }));
  expect(
    screen.getByRole("checkbox", { name: "Attach Ports to chat" }).getAttribute("data-state")
  ).toBe("unchecked");
  fireEvent.click(screen.getByRole("button", { name: "Remove Passengers from chat" }));
  expect(send.disabled).toBe(true);
  fireEvent.change(screen.getByRole("searchbox", { name: "Search datasets" }), {
    target: { value: "" },
  });
  fireEvent.click(screen.getByRole("checkbox", { name: "Attach Passengers to chat" }));
  fireEvent.click(screen.getByRole("checkbox", { name: "Attach Ports to chat" }));
  fireEvent.click(send);
  await waitFor(() => expect(started).toHaveBeenCalledWith("existing-0"));
  expect(calls.create).not.toHaveBeenCalled();
  expect(calls.start.mock.calls[0][0].workshopStartRequest).toMatchObject({
    contextDatasets: ["existing-1"],
    dataset: "existing-0",
    objective: "Prepare a decision dataset",
  });
});

it("retains selected datasets as references alongside uploaded files", async () => {
  const started = setup(true);
  calls.retrieve.mockResolvedValue({
    cells: [{ fingerprint: "hash", id: "cell", state: "ok" }],
    id: "dataset",
    state: "idle",
  });
  calls.start.mockResolvedValue({ id: "task" });
  fireEvent.click(screen.getByRole("checkbox", { name: "Attach Ports to chat" }));
  fireEvent.change(screen.getByLabelText("Attach dataset files"), {
    target: { files: [new File(["a,b\n1,2"], "data.csv")] },
  });
  const send = screen.getByRole("button", { name: "Send" }) as HTMLButtonElement;
  await waitFor(() => expect(send.disabled).toBe(false));
  fireEvent.click(send);
  await waitFor(() => expect(started).toHaveBeenCalledWith("dataset"));
  expect(calls.create.mock.calls[0][0].datasetCreateRequest.source).toEqual({
    uploads: ["data.csv"],
  });
  expect(calls.start.mock.calls[0][0].workshopStartRequest).toMatchObject({
    contextDatasets: ["existing-1"],
    dataset: "dataset",
  });
});

it("retains an errored source for inspection and never sends it to the agent", async () => {
  const started = setup();
  calls.retrieve.mockResolvedValue({ active: null, id: "dataset", state: "error" });
  fireEvent.change(screen.getByLabelText("Attach dataset files"), {
    target: { files: [new File(["a,b\n1,2"], "data.csv")] },
  });
  const send = screen.getByRole("button", { name: "Send" }) as HTMLButtonElement;
  await waitFor(() => expect(send.disabled).toBe(false));
  fireEvent.click(send);
  await screen.findByRole("alert");
  expect(calls.start).not.toHaveBeenCalled();
  expect((screen.getByRole("textbox") as HTMLTextAreaElement).value).toBe(
    "Prepare a decision dataset"
  );
  fireEvent.click(screen.getByRole("button", { name: "Open dataset" }));
  expect(started).toHaveBeenCalledWith("dataset");
});
