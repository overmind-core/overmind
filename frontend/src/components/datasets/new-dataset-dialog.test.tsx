// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { NewDatasetDialog } from "./new-dataset-dialog";

const mocks = vi.hoisted(() => ({
  create: vi.fn(),
  inspect: vi.fn(),
  navigate: vi.fn(),
  split: vi.fn(),
}));

vi.mock("@tanstack/react-router", () => ({ useNavigate: () => mocks.navigate }));
vi.mock("@/hooks/use-guest-gate", () => ({ useGuestGate: () => (action: unknown) => action }));
vi.mock("@/hooks/use-evaluations", () => ({
  useProjectCapabilitiesQuery: () => ({ data: { results: [{ id: "cap", name: "Support" }] } }),
}));
vi.mock("@/hooks/use-datasets", () => ({
  traceSelectionBody: (selection: unknown) => selection,
  useCreateDatasetMutation: () => ({ isPending: false, mutateAsync: mocks.create }),
  useCreateDatasetSplitMutation: () => ({ isPending: false, mutateAsync: mocks.split }),
}));
vi.mock("@/client", () => ({
  default: {
    uploads: {
      uploadsChunkUpdate: ({ body, offset }: { body: Blob; offset: number }) =>
        Promise.resolve({ received: offset + body.size }),
      uploadsCreate: ({ beginUploadRequest }: { beginUploadRequest: { filename: string } }) =>
        Promise.resolve({
          chunkBytes: 1024,
          maxBytes: 100 * 1024 ** 2,
          uploadId: beginUploadRequest.filename,
        }),
      uploadsInspectCreate: mocks.inspect,
      uploadsRetrieve: () => Promise.resolve({ received: 0 }),
    },
  },
}));

beforeEach(() => {
  vi.resetAllMocks();
  Element.prototype.scrollIntoView = vi.fn();
  mocks.inspect.mockImplementation(({ id }) =>
    Promise.resolve({ rows: id.endsWith(".pdf") ? null : id === "first.csv" ? 7 : 8 })
  );
  mocks.create.mockResolvedValue({ id: "created" });
  mocks.split.mockResolvedValue({ eval: { id: "eval" }, train: { id: "train" } });
});
afterEach(cleanup);

function choose(label: string, option: string) {
  fireEvent.keyDown(screen.getByRole("combobox", { name: label }), { key: "ArrowDown" });
  fireEvent.click(screen.getByRole("option", { name: option }));
}

function addFiles(...files: File[]) {
  fireEvent.change(screen.getByLabelText("Choose dataset files"), { target: { files } });
}

function showDialog() {
  return render(<NewDatasetDialog onOpenChange={vi.fn()} open projectId="project" />);
}

describe("dataset file dialog", () => {
  it("starts with the purpose undecided and lets the user choose one", async () => {
    showDialog();
    addFiles(new File(["a"], "first.csv"));
    await within(screen.getByRole("list", { name: "Selected files" })).findByText("7 rows");
    const purpose = screen.getByRole("combobox", { name: "Purpose" });
    expect(purpose.textContent).toBe("Decide later");
    fireEvent.keyDown(purpose, { key: "ArrowDown" });
    expect(screen.getAllByRole("option").map((option) => option.textContent)).toEqual([
      "Decide later",
      "Training",
      "Evaluation",
      "Split into training and evaluation",
    ]);
    fireEvent.click(screen.getByRole("option", { name: "Decide later" }));
    fireEvent.click(screen.getByRole("button", { name: "Create dataset" }));
    await waitFor(() =>
      expect(mocks.create).toHaveBeenCalledWith({
        capabilityId: undefined,
        intent: "pending",
        name: "first",
        projectId: "project",
        source: { uploads: ["first.csv"] },
      })
    );
  });

  it.each([
    "Training",
    "Split into training and evaluation",
  ])("preserves an explicit None capability for %s", async (purpose) => {
    showDialog();
    addFiles(new File(["a"], "first.csv"));
    await within(screen.getByRole("list", { name: "Selected files" })).findByText("7 rows");
    choose("Capability", "None");
    choose("Purpose", purpose);
    fireEvent.click(
      screen.getByRole("button", {
        name: purpose === "Training" ? "Create dataset" : "Create datasets",
      })
    );
    await waitFor(() =>
      expect(purpose === "Training" ? mocks.create : mocks.split).toHaveBeenCalledWith(
        expect.objectContaining({ capabilityId: null })
      )
    );
  });

  it("lists file details, appends selections, and excludes removed files when creating", async () => {
    showDialog();
    const input = screen.getByLabelText("Choose dataset files") as HTMLInputElement;
    expect(input.multiple).toBe(true);
    addFiles(new File(["a"], "first.csv"));
    await within(screen.getByRole("list", { name: "Selected files" })).findByText("7 rows");
    addFiles(new File(["bb"], "second.jsonl"));
    await screen.findByText("15 rows");
    const list = screen.getByRole("list", { name: "Selected files" });
    expect(within(list).getAllByRole("listitem")).toHaveLength(2);
    expect(within(list).getByText("CSV")).toBeTruthy();
    expect(within(list).getByText("JSONL")).toBeTruthy();
    expect(within(list).getByText("2 B")).toBeTruthy();
    fireEvent.change(screen.getByLabelText("Dataset name"), { target: { value: "Support data" } });
    fireEvent.click(screen.getByRole("button", { name: "Remove first.csv" }));
    expect(within(list).queryByText("first.csv")).toBeNull();
    expect((screen.getByLabelText("Dataset name") as HTMLInputElement).value).toBe("Support data");
    choose("Capability", "Support");
    choose("Purpose", "Evaluation");
    fireEvent.click(screen.getByRole("button", { name: "Create dataset" }));
    await waitFor(() =>
      expect(mocks.create).toHaveBeenCalledWith({
        capabilityId: "cap",
        intent: "eval",
        name: "Support data",
        projectId: "project",
        source: { uploads: ["second.jsonl"] },
      })
    );
  });

  it("lands documents without a row count until their text is extracted", async () => {
    showDialog();
    expect(
      screen.getByText("CSV, TSV, JSON, JSONL, Parquet, PDF, DOCX, Markdown, text or images")
    ).toBeTruthy();
    addFiles(new File(["%PDF"], "policies.pdf"), new File(["%PDF"], "terms.pdf"));
    const list = screen.getByRole("list", { name: "Selected files" });
    await waitFor(() => expect(within(list).getAllByText("Ready")).toHaveLength(2));
    expect(within(list).getAllByText("PDF")).toHaveLength(2);
    expect(screen.queryByText(/^[\d,]+ rows?$/)).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Create dataset" }));
    await waitFor(() =>
      expect(mocks.create).toHaveBeenCalledWith(
        expect.objectContaining({
          name: "policies",
          source: { uploads: ["policies.pdf", "terms.pdf"] },
        })
      )
    );
  });

  it("refuses tables and documents in one dataset", async () => {
    showDialog();
    addFiles(new File(["a"], "first.csv"), new File(["%PDF"], "policies.pdf"));
    await within(screen.getByRole("list", { name: "Selected files" })).findByText("7 rows");
    expect(screen.getByText("Upload tables and documents as separate datasets.")).toBeTruthy();
    expect(
      (screen.getByRole("button", { name: "Create dataset" }) as HTMLButtonElement).disabled
    ).toBe(true);
    fireEvent.click(screen.getByRole("button", { name: "Remove policies.pdf" }));
    expect(
      (screen.getByRole("button", { name: "Create dataset" }) as HTMLButtonElement).disabled
    ).toBe(false);
  });

  it("defaults evaluation to 30 percent and updates both counts before submitting", async () => {
    showDialog();
    addFiles(new File(["a"], "first.csv"), new File(["b"], "second.jsonl"));
    await screen.findByText("15 rows");
    expect(screen.queryByRole("spinbutton", { name: "Evaluation share" })).toBeNull();
    choose("Purpose", "Split into training and evaluation");
    const share = screen.getByRole("spinbutton", { name: "Evaluation share" }) as HTMLInputElement;
    expect(share.value).toBe("30");
    const split = screen.getByRole("region", { name: "Data split" });
    expect(within(split).getByText("10 rows")).toBeTruthy();
    expect(within(split).getByText("5 rows")).toBeTruthy();
    fireEvent.change(share, { target: { value: "40" } });
    expect(within(split).getByText("9 rows")).toBeTruthy();
    expect(within(split).getByText("6 rows")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Create datasets" }));
    await waitFor(() =>
      expect(mocks.split).toHaveBeenCalledWith({
        capabilityId: undefined,
        deduplicate: true,
        evalPercent: 40,
        name: "first",
        position: "tail",
        projectId: "project",
        source: { uploads: ["first.csv", "second.jsonl"] },
      })
    );
  });

  it("blocks splits with too few rows or an invalid percentage", async () => {
    mocks.inspect.mockResolvedValue({ rows: 1 });
    showDialog();
    addFiles(new File(["a"], "one.csv"));
    await within(screen.getByRole("list", { name: "Selected files" })).findByText("1 row");
    choose("Purpose", "Split into training and evaluation");
    expect(screen.getByRole("alert").textContent).toContain("At least two rows");
    const create = screen.getByRole("button", { name: "Create datasets" }) as HTMLButtonElement;
    expect(create.disabled).toBe(true);
    fireEvent.change(screen.getByRole("spinbutton", { name: "Evaluation share" }), {
      target: { value: "" },
    });
    expect(screen.getByRole("alert").textContent).toContain("1 to 99");
    expect(create.disabled).toBe(true);
    choose("Purpose", "Training");
    expect(screen.queryByRole("spinbutton", { name: "Evaluation share" })).toBeNull();
    expect(
      (screen.getByRole("button", { name: "Create dataset" }) as HTMLButtonElement).disabled
    ).toBe(false);
  });
});

describe("dataset from traces", () => {
  it("creates one row per selected trace", async () => {
    render(
      <NewDatasetDialog
        initialTraceIds={["t1", "t2"]}
        onOpenChange={vi.fn()}
        open
        projectId="project"
      />
    );
    expect(screen.getByText("2 selected traces")).toBeTruthy();
    expect(screen.queryByLabelText("Choose dataset files")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Create dataset" }));
    await waitFor(() =>
      expect(mocks.create).toHaveBeenCalledWith(
        expect.objectContaining({
          intent: "pending",
          source: { traces: { trace_ids: ["t1", "t2"] } },
        })
      )
    );
  });
});
