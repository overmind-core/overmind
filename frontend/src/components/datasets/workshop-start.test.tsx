// @vitest-environment jsdom

import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useDatasetUploads } from "@/hooks/use-uploads";
import { WorkshopStart } from "./workshop-start";

const mocks = vi.hoisted(() => ({
  create: vi.fn(),
  inspect: vi.fn(),
  navigate: vi.fn(),
}));

vi.mock("@tanstack/react-router", () => ({ useNavigate: () => mocks.navigate }));
vi.mock("@/hooks/use-guest-gate", () => ({ useGuestGate: () => (action: unknown) => action }));
vi.mock("@/hooks/use-datasets", () => ({
  useCreateDatasetMutation: () => ({ isPending: false, mutateAsync: mocks.create }),
}));
vi.mock("@/client", () => ({
  default: {
    uploads: {
      uploadsChunkUpdate: ({ body, offset }: { body: Blob; offset: number }) =>
        Promise.resolve({ received: offset + body.size }),
      uploadsCreate: ({ beginUploadRequest }: { beginUploadRequest: { filename: string } }) =>
        Promise.resolve({
          chunkBytes: 1024,
          maxBytes: 4096,
          uploadId: beginUploadRequest.filename,
        }),
      uploadsInspectCreate: mocks.inspect,
      uploadsRetrieve: () => Promise.resolve({ received: 0 }),
    },
  },
}));

function Composer() {
  const uploads = useDatasetUploads();
  return <WorkshopStart projectId="project" uploads={uploads} />;
}

function attach(...names: string[]) {
  fireEvent.change(screen.getByLabelText("Attach source files"), {
    target: { files: names.map((name) => new File(["delivery,days\nstandard,3"], name)) },
  });
}

beforeEach(() => {
  vi.resetAllMocks();
  mocks.inspect.mockResolvedValue({ rows: 1 });
  mocks.create.mockResolvedValue({ id: "created" });
});
afterEach(cleanup);

describe("Workshop upload composer", () => {
  it("requires a prompt and ready attachments, submitting only retained files", async () => {
    let finish!: (result: { rows: number }) => void;
    mocks.inspect.mockReturnValueOnce(
      new Promise((resolve) => {
        finish = resolve;
      })
    );
    render(<Composer />);
    attach("first.csv", "second.csv");
    await screen.findByText("Checking file…");
    expect(
      (screen.getByRole("button", { name: "Start workshop" }) as HTMLButtonElement).disabled
    ).toBe(true);
    fireEvent.submit(screen.getByRole("form", { name: "Start a dataset" }));
    expect(mocks.create).not.toHaveBeenCalled();
    await act(async () => finish({ rows: 1 }));
    await waitFor(() => expect(screen.getAllByText("Ready")).toHaveLength(2));
    fireEvent.click(screen.getByRole("button", { name: "Remove first.csv" }));
    fireEvent.change(screen.getByRole("textbox", { name: "Describe your data task" }), {
      target: { value: "   " },
    });
    fireEvent.submit(screen.getByRole("form", { name: "Start a dataset" }));
    expect(mocks.create).not.toHaveBeenCalled();
    expect(
      (screen.getByRole("button", { name: "Start workshop" }) as HTMLButtonElement).disabled
    ).toBe(true);
    fireEvent.change(screen.getByRole("textbox", { name: "Describe your data task" }), {
      target: { value: "Prepare delivery examples" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Start workshop" }));
    await waitFor(() =>
      expect(mocks.create).toHaveBeenCalledWith(
        expect.objectContaining({
          brief: "Prepare delivery examples",
          name: "second",
          projectId: "project",
          source: { uploads: ["second.csv"] },
        })
      )
    );
    expect(mocks.navigate).toHaveBeenCalledWith(
      expect.objectContaining({ to: "/datasets/$datasetId" })
    );
  });

  it("keeps failed attachments visible and retries them before submitting the request", async () => {
    mocks.inspect.mockRejectedValueOnce(new Error("Invalid CSV"));
    render(<Composer />);
    fireEvent.change(screen.getByRole("textbox", { name: "Describe your data task" }), {
      target: { value: "Prepare delivery examples" },
    });
    attach("delivery.csv");
    expect((await screen.findByRole("alert")).textContent).toContain("Invalid CSV");
    expect(
      (screen.getByRole("button", { name: "Start workshop" }) as HTMLButtonElement).disabled
    ).toBe(true);
    fireEvent.click(screen.getByRole("button", { name: "Retry delivery.csv" }));
    await screen.findByText("Ready");
    fireEvent.click(screen.getByRole("button", { name: "Start workshop" }));
    await waitFor(() =>
      expect(mocks.create).toHaveBeenCalledWith(
        expect.objectContaining({
          brief: "Prepare delivery examples",
          source: { uploads: ["delivery.csv"] },
        })
      )
    );
  });

  it("retains the request and attachments when creation fails", async () => {
    mocks.create.mockRejectedValueOnce(new Error("Service unavailable"));
    render(<Composer />);
    fireEvent.change(screen.getByRole("textbox", { name: "Describe your data task" }), {
      target: { value: "Inspect this data" },
    });
    attach("source.csv");
    await screen.findByText("Ready");
    fireEvent.click(screen.getByRole("button", { name: "Start workshop" }));
    await screen.findByRole("alert");
    expect(
      (screen.getByRole("textbox", { name: "Describe your data task" }) as HTMLTextAreaElement)
        .value
    ).toBe("Inspect this data");
    expect(
      within(screen.getByRole("list", { name: "Attached files" })).getByText("source.csv")
    ).toBeTruthy();
    expect(mocks.navigate).not.toHaveBeenCalled();
  });

  it("requires an attachment for Enter submission and preserves Shift+Enter and composition", async () => {
    render(<Composer />);
    const input = screen.getByRole("textbox", { name: "Describe your data task" });
    fireEvent.change(input, { target: { value: "Explore my data" } });
    fireEvent.keyDown(input, { key: "Enter" });
    expect(mocks.create).not.toHaveBeenCalled();
    attach("source.csv");
    await screen.findByText("Ready");
    expect(fireEvent.keyDown(input, { key: "Enter", shiftKey: true })).toBe(true);
    expect(fireEvent.keyDown(input, { isComposing: true, key: "Enter" })).toBe(true);
    expect(mocks.create).not.toHaveBeenCalled();
    expect(fireEvent.keyDown(input, { key: "Enter" })).toBe(false);
    await waitFor(() =>
      expect(mocks.create).toHaveBeenCalledWith(
        expect.objectContaining({ brief: "Explore my data", source: { uploads: ["source.csv"] } })
      )
    );
  });

  it("opens attachment details from the filename and keeps removal separate", async () => {
    render(<Composer />);
    attach("delivery.csv");
    await screen.findByText("Ready");
    expect(screen.queryByRole("dialog")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "View delivery.csv" }));
    const details = await screen.findByRole("dialog", { name: "File details: delivery.csv" });
    expect(within(details).getByText("CSV · 24 B")).toBeTruthy();
    expect(within(details).getByText("1 row")).toBeTruthy();
    expect(mocks.create).not.toHaveBeenCalled();
  });
});
