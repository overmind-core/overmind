// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

import { DataProjectForm } from "./data-project-form";

const mocks = vi.hoisted(() => ({ dataset: vi.fn(), files: [] as unknown[], project: vi.fn() }));
vi.mock("@/client", () => ({
  default: {
    datasets: { datasetsCreate: mocks.dataset },
    projects: { projectsCreate: mocks.project },
  },
}));
vi.mock("@/hooks/use-uploads", () => ({
  useDatasetUploads: () => ({
    add: vi.fn(),
    files: mocks.files,
    remove: vi.fn(),
    reset: vi.fn(),
    retry: vi.fn(),
  }),
}));

beforeEach(() => {
  vi.clearAllMocks();
  mocks.files = [
    {
      file: new File(["input,output\na,b"], "source.csv"),
      id: "file-1",
      percent: 100,
      rows: 1,
      status: "ready",
      uploadId: "upload-1",
    },
  ];
  mocks.project.mockResolvedValue({ id: "project-1", name: "Support" });
  mocks.dataset.mockResolvedValue({ id: "dataset-1" });
});
afterEach(cleanup);
function setup() {
  const done = vi.fn();
  render(
    <QueryClientProvider
      client={new QueryClient({ defaultOptions: { mutations: { retry: false } } })}
    >
      <DataProjectForm onCreated={done} />
    </QueryClientProvider>
  );
  fireEvent.change(screen.getByLabelText("Project name"), { target: { value: "Support" } });
  return done;
}

it("creates the project and uploaded training source together without extra questions", async () => {
  const done = setup();
  expect(screen.queryByLabelText("Capability")).toBeNull();
  expect(screen.queryByLabelText("Purpose")).toBeNull();
  expect(screen.queryByLabelText("Intent (optional)")).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Create dataset" }));
  await waitFor(() =>
    expect(done).toHaveBeenCalledWith({ datasetId: "dataset-1", projectId: "project-1" })
  );
  expect(mocks.dataset).toHaveBeenCalledWith({
    datasetCreateRequest: expect.objectContaining({
      brief: "Explore the uploaded data and determine how best to prepare it for training.",
      capability: null,
      intent: "train",
      project: "project-1",
      source: { uploads: ["upload-1"] },
    }),
  });
});

it("retries a failed dataset creation without creating another project", async () => {
  mocks.dataset.mockRejectedValueOnce(new Error("Landing unavailable"));
  const done = setup();
  fireEvent.click(screen.getByRole("button", { name: "Create dataset" }));
  await screen.findByText("Landing unavailable");
  expect(done).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "Create dataset" }));
  await waitFor(() => expect(done).toHaveBeenCalled());
  expect(mocks.project).toHaveBeenCalledTimes(1);
  expect(mocks.dataset).toHaveBeenCalledTimes(2);
});

it.each([
  "queued",
  "uploading",
  "counting",
  "error",
  "empty",
])("waits for usable uploads: %s", (status) => {
  mocks.files =
    status === "empty"
      ? []
      : [{ file: new File(["a"], "data.csv"), id: "file-1", percent: 0, status }];
  setup();
  expect(
    (screen.getByRole("button", { name: "Create dataset" }) as HTMLButtonElement).disabled
  ).toBe(true);
  expect(mocks.project).not.toHaveBeenCalled();
});
