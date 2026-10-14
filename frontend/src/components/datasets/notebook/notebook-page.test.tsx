// @vitest-environment jsdom

import type { ReactNode } from "react";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

import { DatasetNotebook } from "@/components/datasets/notebook/notebook-page";
import type { Cell } from "@/openapi";
import { DatasetFromJSON, ExecutionFromJSON } from "@/openapi";

const state = vi.hoisted(() => ({
  cancelImport: vi.fn(),
  cancelRun: vi.fn(),
  create: vi.fn(),
  dataset: {} as ReturnType<typeof DatasetFromJSON>,
  executions: [] as ReturnType<typeof ExecutionFromJSON>[],
  guest: false,
  patch: vi.fn(),
  prompt: vi.fn(),
  resume: vi.fn(),
  upgrade: vi.fn(),
}));

vi.mock("@/client", () => ({
  default: {
    capabilities: { capabilitiesList: vi.fn(() => Promise.resolve({ results: [] })) },
    datasets: {
      datasetsCancelImportCreate: state.cancelImport,
      datasetsCreate: state.create,
      datasetsExecutionsCancelCreate: state.cancelRun,
      datasetsExecutionsList: vi.fn(() => Promise.resolve(state.executions)),
      datasetsPartialUpdate: state.patch,
      datasetsPreparePromptRetrieve: state.prompt,
      datasetsResumeImportCreate: state.resume,
      datasetsRetrieve: vi.fn(() => Promise.resolve(state.dataset)),
    },
  },
  fetchWithAuth: vi.fn(() => new Promise(() => {})),
}));
vi.mock("@tanstack/react-router", () => ({
  Link: ({ children }: { children: ReactNode }) => <span>{children}</span>,
  useNavigate: () => vi.fn(),
}));
vi.mock("@/contexts/auth-context", () => ({
  useAuthContext: () => ({ isGuest: state.guest, requestUpgrade: state.upgrade }),
}));
vi.mock("@/contexts/workshop-sidebar-context", () => ({
  useWorkshopSidebar: () => ({ close: vi.fn(), open: false, toggle: vi.fn() }),
}));
vi.mock("@/components/datasets/notebook/cell-flow", () => ({
  VersionFlow: ({
    cells,
    renderVersion,
  }: {
    cells: Cell[];
    renderVersion: (cell: Cell) => ReactNode;
  }) => (
    <div>
      {cells.map((cell) => (
        <div key={cell.id}>{renderVersion(cell)}</div>
      ))}
    </div>
  ),
}));

const DOCS = "https://docs.overmindlab.ai/platform/mcp";

const cell = (fields: Record<string, unknown>) => ({
  capability_report: null,
  columns: [{ name: "messages", type: "json" }],
  created_at: "2026-10-12T10:00:00Z",
  fingerprint: "f",
  fits: { ok: true, reason: "" },
  frozen: false,
  impact: {},
  intent_report: { train: { ok: true } },
  rows: 120,
  script: "",
  seconds: 0,
  stats: null,
  stdout: "",
  title: "Source",
  used_at: null,
  ...fields,
});

const SOURCE = cell({ id: "c0", parent: null, position: 0, version: "v0" });
const NORMALIZED = cell({
  frozen: true,
  id: "c1",
  impact: {
    identity_preserved: true,
    input_evidence_removed: 0,
    instruction_changes: 3,
    rows_added: 0,
    rows_after: 96,
    rows_before: 120,
    rows_removed: 24,
  },
  parent: "c0",
  position: 1,
  rows: 96,
  script: "df = df[df.messages.map(len) > 1]",
  title: "Drop short chats",
  version: "v1",
});

const dataset = (fields: Record<string, unknown>) =>
  DatasetFromJSON({
    active: null,
    active_version: "",
    capability_name: null,
    capability_rank: [],
    cells: [],
    created_at: "2026-10-12T10:00:00Z",
    created_by: null,
    error: "",
    id: "dataset",
    import_blocked: false,
    intent: "train",
    name: "support",
    project: "project",
    rows: 0,
    running: 0,
    source_kind: "file",
    source_spec: {},
    state: "idle",
    updated_at: "2026-10-12T10:00:00Z",
    ...fields,
  });

let queryClient: QueryClient;

beforeEach(() => {
  vi.clearAllMocks();
  state.guest = false;
  state.executions = [];
  state.dataset = dataset({
    error: "Documents cannot be read right now. Retry the import.",
    import_blocked: true,
    state: "error",
  });
  state.resume.mockImplementation(() => new Promise(() => {}));
  state.cancelImport.mockImplementation(() => new Promise(() => {}));
  state.cancelRun.mockImplementation(() => new Promise(() => {}));
  state.create.mockImplementation(() => new Promise(() => {}));
  state.prompt.mockResolvedValue({ prompt: "Prepare dataset dataset in project project" });
  state.patch.mockImplementation(() => new Promise(() => {}));
  queryClient = new QueryClient({
    defaultOptions: { mutations: { retry: false }, queries: { retry: false, staleTime: Infinity } },
  });
  vi.stubGlobal(
    "ResizeObserver",
    class {
      observe() {}
      unobserve() {}
      disconnect() {}
    }
  );
});

afterEach(() => {
  cleanup();
  queryClient.clear();
  vi.unstubAllGlobals();
});

function showNotebook() {
  queryClient.setQueryData(["datasets", "detail", "dataset"], state.dataset);
  queryClient.setQueryData(["eval-capabilities", "project"], { results: [] });
  return render(
    <QueryClientProvider client={queryClient}>
      <DatasetNotebook datasetId="dataset" projectId="project" />
    </QueryClientProvider>
  );
}

it("retries a blocked import once and shows why it stopped", async () => {
  showNotebook();
  expect(screen.getByRole("alert").textContent).toBe(
    "Documents cannot be read right now. Retry the import."
  );
  const button = screen.getByRole("button", { name: "Retry import" }) as HTMLButtonElement;
  fireEvent.click(button);
  await waitFor(() => expect(state.resume).toHaveBeenCalledWith({ id: "dataset" }));
  await waitFor(() => expect(button.disabled).toBe(true));
  fireEvent.click(button);
  expect(state.resume).toHaveBeenCalledTimes(1);
});

it("gates the retry for guests", () => {
  state.guest = true;
  showNotebook();
  fireEvent.click(screen.getByRole("button", { name: "Retry import" }));
  expect(state.upgrade).toHaveBeenCalledOnce();
  expect(state.resume).not.toHaveBeenCalled();
});

it("offers no retry for a failed landing that cannot resume", () => {
  state.dataset = dataset({ error: "The PDF could not be read.", state: "error" });
  showNotebook();
  expect(screen.getByRole("alert").textContent).toBe("The PDF could not be read.");
  expect(screen.queryByRole("button", { name: "Retry import" })).toBeNull();
});

it("shows the import in progress while the source lands", () => {
  state.dataset = dataset({ state: "landing" });
  showNotebook();
  expect(screen.getByText("Importing…")).toBeTruthy();
  expect(screen.queryByRole("button", { name: "Retry import" })).toBeNull();
});

it("points a dataset with only its source to a coding agent", () => {
  state.dataset = dataset({ active: "c0", active_version: "v0", cells: [SOURCE], rows: 120 });
  showNotebook();
  expect(screen.getByText("No versions yet")).toBeTruthy();
  expect(
    screen.getByRole("link", { name: "Connect a coding agent to add them" }).getAttribute("href")
  ).toBe(DOCS);
});

it("shows a version's script, its changes and that a run used it", () => {
  state.dataset = dataset({
    active: "c1",
    active_version: "v1",
    cells: [SOURCE, NORMALIZED],
  });
  showNotebook();
  expect(screen.queryByText("No versions yet")).toBeNull();
  const card = screen.getByRole("article", { name: "v1 Drop short chats" });
  expect(within(card).getByRole("region", { name: "Script of v1" }).textContent).toContain(
    "df = df[df.messages.map(len) > 1]"
  );
  expect(within(card).getByText("120 → 96 rows")).toBeTruthy();
  expect(within(card).getByText("24 removed")).toBeTruthy();
  expect(within(card).getByText("Instructions changed in 3 rows")).toBeTruthy();
  expect(within(card).queryByText("Row identity not kept")).toBeNull();
  expect(within(card).queryByText(/Input evidence removed/)).toBeNull();
  expect(within(card).getByText("Used")).toBeTruthy();
  expect(within(card).queryByRole("textbox")).toBeNull();
});

it("sets another version active only if the active version did not change meanwhile", async () => {
  state.dataset = dataset({ active: "c1", active_version: "v1", cells: [SOURCE, NORMALIZED] });
  showNotebook();
  const source = screen.getByRole("article", { name: "v0 Source" });
  fireEvent.click(within(source).getByRole("button", { name: "Set active" }));
  await waitFor(() =>
    expect(state.patch).toHaveBeenCalledWith({
      id: "dataset",
      patchedDatasetRequest: { active: "c0", expectedActive: "c1" },
    })
  );
  const active = screen.getByRole("article", { name: "v1 Drop short chats" });
  expect(within(active).queryByRole("button", { name: "Set active" })).toBeNull();
});

it("counts running scripts and lists recent runs in Activity", async () => {
  state.dataset = dataset({
    active: "c1",
    active_version: "v1",
    cells: [SOURCE, NORMALIZED],
    running: 2,
  });
  state.executions = [
    ExecutionFromJSON({
      cell: null,
      created_by: null,
      error:
        "File \"<script>\", line 1, in <module>\n    df = df.drop(columns=['messages'])\nKeyError: 'messages'\n",
      finished_at: "2026-10-12T10:00:03Z",
      id: "run-failed",
      mode: "try",
      parent: "v1",
      queued_at: "2026-10-12T10:00:00Z",
      run_seconds: 2.5,
      script_head: "df = df.drop(columns=['messages'])",
      started_at: "2026-10-12T10:00:00Z",
      state: "failed",
      title: "",
      transient: false,
    }),
    ExecutionFromJSON({
      cell: "v1",
      created_by: null,
      error: "",
      finished_at: "2026-10-12T09:59:02Z",
      id: "run-added",
      mode: "cell",
      parent: "v0",
      queued_at: "2026-10-12T09:59:00Z",
      run_seconds: 1.2,
      script_head: "df = df[df.messages.map(len) > 1]",
      started_at: "2026-10-12T09:59:00Z",
      state: "ok",
      title: "Drop short chats",
      transient: false,
    }),
  ];
  showNotebook();
  fireEvent.click(screen.getByRole("button", { name: "Running 2 scripts" }));
  const activity = await screen.findByRole("list", { name: "Activity" });
  const [failed, added] = within(activity).getAllByRole("listitem");
  expect(within(failed).getByText("Preview")).toBeTruthy();
  expect(within(failed).getByText("df = df.drop(columns=['messages'])")).toBeTruthy();
  expect(within(failed).getByText("KeyError: 'messages'")).toBeTruthy();
  expect(within(added).getByText("Version")).toBeTruthy();
  expect(within(added).getByText("Drop short chats")).toBeTruthy();
  expect(within(added).getByRole("button", { name: "Show v1" })).toBeTruthy();
});

it("renames the dataset and changes nothing else", async () => {
  state.dataset = dataset({
    active: "c0",
    active_version: "v0",
    cells: [SOURCE],
    intent: "pending",
  });
  showNotebook();
  fireEvent.click(screen.getByRole("button", { name: "Rename dataset" }));
  expect(screen.queryByRole("combobox")).toBeNull();
  fireEvent.change(screen.getByLabelText("Name"), { target: { value: "support v2" } });
  fireEvent.click(screen.getByRole("button", { name: "Save" }));
  await waitFor(() =>
    expect(state.patch).toHaveBeenCalledWith({
      id: "dataset",
      patchedDatasetRequest: { name: "support v2" },
    })
  );
});

it("stops the import while the source lands", async () => {
  state.dataset = dataset({ state: "landing" });
  showNotebook();
  fireEvent.click(screen.getByRole("button", { name: "Stop import" }));
  await waitFor(() => expect(state.cancelImport).toHaveBeenCalledWith({ id: "dataset" }));
});

it("stops a running script from Activity", async () => {
  state.dataset = dataset({ active: "c0", active_version: "v0", cells: [SOURCE], running: 1 });
  state.executions = [
    ExecutionFromJSON({
      attachment: "labels.csv",
      cell: null,
      created_by: null,
      error: "",
      finished_at: null,
      id: "run-live",
      mode: "cell",
      parent: "v0",
      queued_at: "2026-10-12T10:00:00Z",
      run_seconds: null,
      script_head: "df = df.merge(attachment, on='source_row')",
      started_at: "2026-10-12T10:00:00Z",
      state: "running",
      title: "Labels",
      transient: false,
    }),
  ];
  showNotebook();
  fireEvent.click(screen.getByRole("button", { name: "Running 1 script" }));
  const activity = await screen.findByRole("list", { name: "Activity" });
  expect(within(activity).getByText("with labels.csv")).toBeTruthy();
  fireEvent.click(within(activity).getByRole("button", { name: "Stop Labels" }));
  await waitFor(() =>
    expect(state.cancelRun).toHaveBeenCalledWith({ executionId: "run-live", id: "dataset" })
  );
});

it("shows a version's label balance, empty fields and the file its script read", () => {
  const labelled = cell({
    analytics: {
      inputs: {
        constant: [{ field: "model", of: 6, rows: 6 }],
        empty: [{ field: "input.order", of: 6, rows: 3 }],
      },
      labels: [
        {
          classes: [
            { label: "refund", rows: 4 },
            { label: "other", rows: 2 },
          ],
          field: "expected_output",
          labels: 2,
          majority: { label: "refund", share: 0.6667 },
          rows: 6,
        },
      ],
    },
    attachment: { bytes: 60, filename: "labels.csv", rows: 6 },
    id: "c2",
    parent: "c0",
    position: 2,
    rows: 6,
    script: "df = df.merge(attachment, on='source_row')",
    title: "Labels",
    version: "v2",
  });
  state.dataset = dataset({ active: "c2", active_version: "v2", cells: [SOURCE, labelled] });
  showNotebook();
  const card = screen.getByRole("article", { name: "v2 Labels" });
  expect(within(card).getByRole("button", { name: /^Analytics/ }).textContent).toBe(
    "Analyticsexpected_output · refund 67% · 2 field warnings"
  );
  const analytics = within(card).getByLabelText("Analytics");
  expect(within(analytics).getByRole("rowheader", { name: "refund" })).toBeTruthy();
  expect(within(analytics).getByText("Empty in 3 of 6 rows")).toBeTruthy();
  expect(within(analytics).getByText("One value in 6 of 6 rows")).toBeTruthy();
  expect(within(card).getByRole("button", { name: "labels.csv" })).toBeTruthy();
});

it("copies the prepare-dataset prompt for a version", async () => {
  const writeText = vi.fn(() => Promise.resolve());
  vi.stubGlobal("navigator", { clipboard: { writeText } });
  state.dataset = dataset({ active: "c1", active_version: "v1", cells: [SOURCE, NORMALIZED] });
  showNotebook();
  fireEvent.click(screen.getByRole("button", { name: "Prepare v1 with agent" }));
  const dialog = await screen.findByRole("dialog", { name: "Prepare v1 with agent" });
  expect(state.prompt).toHaveBeenCalledWith({ cell: "c1", id: "dataset" });
  await within(dialog).findByDisplayValue("Prepare dataset dataset in project project");
  fireEvent.click(within(dialog).getByRole("button", { name: "Copy prompt" }));
  await waitFor(() =>
    expect(writeText).toHaveBeenCalledWith("Prepare dataset dataset in project project")
  );
});

it("lands a version as a new dataset", async () => {
  state.dataset = dataset({ active: "c1", active_version: "v1", cells: [SOURCE, NORMALIZED] });
  showNotebook();
  fireEvent.click(screen.getByRole("button", { name: "New dataset from v1" }));
  const dialog = await screen.findByRole("dialog", { name: "New dataset" });
  expect(within(dialog).getByText("From support · v1 · 96 rows")).toBeTruthy();
  fireEvent.click(within(dialog).getByRole("button", { name: "Create dataset" }));
  await waitFor(() =>
    expect(state.create).toHaveBeenCalledWith({
      datasetCreateRequest: expect.objectContaining({
        name: "support v1",
        source: { version: { dataset: "dataset", version: "c1" } },
      }),
    })
  );
});
