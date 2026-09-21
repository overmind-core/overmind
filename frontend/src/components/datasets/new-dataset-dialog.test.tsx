// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { NewDatasetButton } from "./new-dataset-button";
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
vi.mock("@/components/traces/trace-bulk-source-picker", () => ({
  TraceBulkSourcePicker: () => <p>Trace filters</p>,
}));
vi.mock("@/client", () => ({
  default: {
    uploads: {
      uploadsChunkUpdate: ({ body, offset }: { body: Blob; offset: number }) =>
        Promise.resolve({ received: offset + body.size }),
      uploadsCreate: ({ beginUploadRequest }: { beginUploadRequest: { filename: string } }) =>
        Promise.resolve({
          chunkBytes: 1024,
          maxBytes: 2048,
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
    Promise.resolve({ rows: id === "first.csv" ? 7 : 8 })
  );
  mocks.create.mockResolvedValue({ id: "created" });
  mocks.split.mockResolvedValue({ eval: { id: "eval" }, train: { id: "train" } });
});
afterEach(cleanup);

function choose(label: string, option: string) {
  fireEvent.keyDown(screen.getByRole("combobox", { name: label }), { key: "ArrowDown" });
  fireEvent.click(screen.getByRole("option", { name: option }));
}

describe("new dataset sources", () => {
  it("offers only upload and trace sources before opening the dialog", () => {
    const onSelect = vi.fn();
    render(<NewDatasetButton onSelect={onSelect} />);
    fireEvent.keyDown(screen.getByRole("button", { name: "New dataset" }), { key: "ArrowDown" });
    expect(screen.getAllByRole("menuitem").map((item) => item.textContent)).toEqual([
      " Upload file",
      " Data from traces",
    ]);
    fireEvent.click(screen.getByRole("menuitem", { name: "Data from traces" }));
    expect(onSelect).toHaveBeenCalledWith("traces");
  });

  it("shows the chosen trace source without a second source selector", () => {
    render(
      <NewDatasetDialog initialSource="traces" onOpenChange={vi.fn()} open projectId="project" />
    );
    expect(screen.getByText("Trace filters")).toBeTruthy();
    expect(screen.queryByText("Paste rows")).toBeNull();
    expect(screen.queryByText("Source")).toBeNull();
    expect(screen.queryByRole("button", { name: "Choose files" })).toBeNull();
  });
});

describe("dataset file dialog", () => {
  it("requires a purpose and offers no automatic purpose", async () => {
    const props = {
      initialFiles: [new File(["a"], "first.csv")],
      onOpenChange: vi.fn(),
      projectId: "project",
    };
    const view = render(<NewDatasetDialog {...props} open />);
    await within(screen.getByRole("list", { name: "Selected files" })).findByText("7 rows");
    const create = screen.getByRole("button", { name: "Create dataset" }) as HTMLButtonElement;
    expect(create.disabled).toBe(true);
    expect(screen.getByRole("combobox", { name: "Purpose" }).textContent).toBe("Select purpose");
    fireEvent.keyDown(screen.getByRole("combobox", { name: "Purpose" }), { key: "ArrowDown" });
    expect(screen.getAllByRole("option").map((option) => option.textContent)).toEqual([
      "Evaluation",
      "Training",
      "Train + eval",
    ]);
    fireEvent.click(screen.getByRole("option", { name: "Training" }));
    expect(create.disabled).toBe(false);
    view.rerender(<NewDatasetDialog {...props} open={false} />);
    view.rerender(<NewDatasetDialog {...props} open />);
    expect(screen.getByRole("combobox", { name: "Purpose" }).textContent).toBe("Select purpose");
    expect(
      (screen.getByRole("button", { name: "Create dataset" }) as HTMLButtonElement).disabled
    ).toBe(true);
  });

  it.each([
    "Training",
    "Train + eval",
  ])("preserves an explicit None capability for %s", async (purpose) => {
    render(
      <NewDatasetDialog
        initialCapabilityId="cap"
        initialFiles={[new File(["a"], "first.csv")]}
        onOpenChange={vi.fn()}
        open
        projectId="project"
      />
    );
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
    render(<NewDatasetDialog onOpenChange={vi.fn()} open projectId="project" />);
    const input = screen.getByLabelText("Choose dataset files") as HTMLInputElement;
    expect(input.multiple).toBe(true);
    fireEvent.change(input, { target: { files: [new File(["a"], "first.csv")] } });
    await within(screen.getByRole("list", { name: "Selected files" })).findByText("7 rows");
    fireEvent.change(input, { target: { files: [new File(["bb"], "second.jsonl")] } });
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

  it("defaults evaluation to 30 percent and updates both counts before submitting", async () => {
    render(
      <NewDatasetDialog
        initialFiles={[new File(["a"], "first.csv"), new File(["b"], "second.jsonl")]}
        onOpenChange={vi.fn()}
        open
        projectId="project"
      />
    );
    await screen.findByText("15 rows");
    expect(screen.queryByRole("spinbutton", { name: "Data split" })).toBeNull();
    choose("Purpose", "Train + eval");
    expect(screen.queryByLabelText("Group columns")).toBeNull();
    expect(screen.queryByLabelText("Stratify by")).toBeNull();
    expect(screen.queryByText(/Estimated counts\./)).toBeNull();
    const share = screen.getByRole("spinbutton", { name: "Data split" }) as HTMLInputElement;
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
    render(
      <NewDatasetDialog
        initialFiles={[new File(["a"], "one.csv")]}
        onOpenChange={vi.fn()}
        open
        projectId="project"
      />
    );
    await screen.findByText("1 row");
    choose("Purpose", "Train + eval");
    expect(screen.getByRole("alert").textContent).toContain("At least two rows");
    const create = screen.getByRole("button", { name: "Create datasets" }) as HTMLButtonElement;
    expect(create.disabled).toBe(true);
    fireEvent.change(screen.getByRole("spinbutton", { name: "Data split" }), {
      target: { value: "" },
    });
    expect(screen.getByRole("alert").textContent).toContain("1 to 99");
    expect(create.disabled).toBe(true);
    choose("Purpose", "Training");
    expect(screen.queryByRole("spinbutton", { name: "Data split" })).toBeNull();
    expect(
      (screen.getByRole("button", { name: "Create dataset" }) as HTMLButtonElement).disabled
    ).toBe(false);
  });
});
