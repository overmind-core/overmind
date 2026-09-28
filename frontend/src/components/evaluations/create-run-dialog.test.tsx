// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

import { CreateRunDialog } from "./create-run-dialog";

const mocks = vi.hoisted(() => ({ create: vi.fn(), navigate: vi.fn(), preview: vi.fn() }));
vi.mock("@/client", () => ({
  default: {
    evalRuns: { evalRunsContextCheckCreate: mocks.preview, evalRunsCreate: mocks.create },
  },
}));
vi.mock("@tanstack/react-router", () => ({ useNavigate: () => mocks.navigate }));
vi.mock("@/hooks/use-evaluations", () => ({
  useEvalSetsQuery: () => ({
    data: {
      results: [
        { capability: "cap", generativeCount: 1, id: "set", name: "Quality", project: "project" },
        { generativeCount: 1, id: "foreign", name: "Other project", project: "other" },
        {
          capability: "other-cap",
          generativeCount: 1,
          id: "unrelated",
          name: "Other capability",
          project: "project",
        },
      ],
    },
  }),
  useModelCatalogQuery: () => ({
    data: {
      models: [
        { id: "candidate", name: "Candidate" },
        { id: "candidate:batch", name: "Batch candidate" },
      ],
    },
  }),
  useProjectCapabilitiesQuery: () => ({ data: { results: [{ id: "cap", name: "Capability" }] } }),
  useProjectDatasetsForEvalQuery: () => ({
    data: { results: [{ active: "cell", capability: "cap", id: "data", name: "Questions" }] },
  }),
}));
vi.mock("./searchable-select", () => ({
  SearchableSelect: ({
    ariaLabel,
    value,
    onChange,
    options,
  }: {
    ariaLabel: string;
    value: string;
    onChange: (v: string) => void;
    options: { value: string; label: string }[];
  }) => (
    <select aria-label={ariaLabel} onChange={(e) => onChange(e.target.value)} value={value}>
      <option value="">Select</option>
      {options.map((option) => (
        <option key={option.value} value={option.value}>
          {option.label}
        </option>
      ))}
    </select>
  ),
}));
vi.mock("./judge-model-select", () => ({
  JudgeModelSelect: ({ value, onChange }: { value: string; onChange: (v: string) => void }) => (
    <select aria-label="Judge model" onChange={(e) => onChange(e.target.value)} value={value}>
      <option value="">Eval set default</option>
      <option value="gpt-5.6-luna">Alternative judge</option>
    </select>
  ),
}));

afterEach(cleanup);
beforeEach(() => {
  vi.clearAllMocks();
  mocks.create.mockResolvedValue({ id: "new-run" });
  mocks.preview.mockResolvedValue({ checks: [], judgeModels: [] });
});

function setup() {
  const client = new QueryClient({
    defaultOptions: { mutations: { retry: false }, queries: { retry: false } },
  });
  render(
    <QueryClientProvider client={client}>
      <CreateRunDialog projectId="project" />
    </QueryClientProvider>
  );
  fireEvent.click(screen.getByRole("button", { name: "New evaluation" }));
  fireEvent.change(screen.getByLabelText("Name"), { target: { value: " New run " } });
  fireEvent.change(screen.getByLabelText("Eval dataset"), { target: { value: "data" } });
  fireEvent.change(screen.getByLabelText("Model being tested"), { target: { value: "candidate" } });
  fireEvent.change(screen.getByLabelText("Eval set"), { target: { value: "set" } });
}

it("previews and launches with the same run-only judge selection", async () => {
  setup();
  expect(screen.queryByText("Other project")).toBeNull();
  expect(screen.queryByText("Other capability")).toBeNull();
  expect(screen.queryByText("Batch candidate")).toBeNull();
  fireEvent.change(screen.getByLabelText("Judge model"), { target: { value: "gpt-5.6-luna" } });
  await waitFor(() =>
    expect(mocks.preview).toHaveBeenLastCalledWith({
      evaluationContextRequestRequest: {
        capability: "cap",
        cell: "cell",
        dataset: "data",
        evalSet: "set",
        judgeModel: "gpt-5.6-luna",
        project: "project",
        variants: [{ modelName: "candidate" }],
      },
    })
  );
  fireEvent.click(screen.getByRole("button", { name: "Start evaluation" }));
  await waitFor(() =>
    expect(mocks.create).toHaveBeenCalledWith({
      evalRunRequest: {
        cell: "cell",
        dataSource: "dataset",
        dataset: "data",
        evalSet: "set",
        judgeModel: "gpt-5.6-luna",
        maxItems: 100,
        name: "New run",
        project: "project",
        sampling: 1,
        variantsInput: [{ label: "candidate", mode: "generate", model_name: "candidate" }],
      },
    })
  );
  await waitFor(() =>
    expect(mocks.navigate).toHaveBeenCalledWith({
      params: { runId: "new-run" },
      search: { projectId: "project" },
      to: "/evaluations/runs/$runId",
    })
  );
});

it("warns before launch but allows the saved judge without changing it", async () => {
  mocks.preview.mockResolvedValue({
    checks: [{ model: "small", role: "judge", status: "warning" }],
    judgeModels: [],
  });
  setup();
  await waitFor(() =>
    expect(screen.getByRole("button", { name: "Start evaluation" }).className).toContain(
      "bg-warning"
    )
  );
  fireEvent.click(screen.getByRole("button", { name: "Start evaluation" }));
  expect(mocks.create).not.toHaveBeenCalled();
  expect(screen.getByText("Evaluations may fail")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Start anyway" }));
  await waitFor(() => expect(mocks.create).toHaveBeenCalled());
  expect(mocks.create.mock.calls[0][0].evalRunRequest.judgeModel).toBe("");
});

it("keeps unknown context neutral and validates the sample count", async () => {
  mocks.preview.mockResolvedValue({
    checks: [{ model: "unknown", role: "judge", status: "unknown" }],
    judgeModels: [],
  });
  setup();
  await waitFor(() => expect(mocks.preview).toHaveBeenCalled());
  expect(screen.getByRole("button", { name: "Start evaluation" }).className).not.toContain(
    "bg-warning"
  );
  fireEvent.change(screen.getByLabelText("Maximum samples"), { target: { value: "0" } });
  expect(
    (screen.getByRole("button", { name: "Start evaluation" }) as HTMLButtonElement).disabled
  ).toBe(true);
  expect(mocks.create).not.toHaveBeenCalled();
});

it("keeps the selected judge when launch fails", async () => {
  mocks.create.mockRejectedValue(new Error("Launch unavailable"));
  setup();
  fireEvent.change(screen.getByLabelText("Judge model"), { target: { value: "gpt-5.6-luna" } });
  fireEvent.click(screen.getByRole("button", { name: "Start evaluation" }));
  expect(await screen.findByRole("alert")).toBeTruthy();
  expect((screen.getByLabelText("Judge model") as HTMLSelectElement).value).toBe("gpt-5.6-luna");
  expect(mocks.navigate).not.toHaveBeenCalled();
});
