// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { CreateEvalSetDialog } from "./create-eval-set-dialog";

const mocks = vi.hoisted(() => ({ create: vi.fn() }));
vi.mock("@/hooks/use-evaluations", () => ({
  useCreateEvalSetMutation: () => ({ isPending: false, mutateAsync: mocks.create }),
  useEvaluatorCatalogQuery: () => ({
    data: [
      { applicableRoles: ["generative"], id: "generic", kind: "deterministic", name: "Accuracy" },
      {
        applicableRoles: ["generative"],
        capabilityName: "Support",
        id: "mapped",
        kind: "llm_judge",
        name: "Tone",
      },
      { applicableRoles: [], id: "blocked", kind: "agentic", name: "Unavailable" },
    ],
  }),
  useProjectCapabilitiesQuery: () => ({ data: { results: [{ id: "cap", name: "Support" }] } }),
}));
vi.mock("@/components/capability-combobox", () => ({
  CapabilityCombobox: ({
    onChange,
    value,
  }: {
    onChange: (value: string) => void;
    value: string;
  }) => (
    <select aria-label="Capability" onChange={(e) => onChange(e.target.value)} value={value}>
      <option value="">Select a capability</option>
      <option value="cap">Support</option>
    </select>
  ),
}));

beforeEach(() => {
  mocks.create.mockReset().mockResolvedValue({ id: "created" });
});
afterEach(cleanup);

function open() {
  render(<CreateEvalSetDialog projectId="project" />);
  fireEvent.click(screen.getByRole("button", { name: "New eval set" }));
}

describe("Create eval set", () => {
  it("searches all evaluators by name, type and mapped capability", () => {
    open();
    const search = screen.getByRole("searchbox", { name: "Search evaluators" });
    for (const term of ["Tone", "LLM judge", "Support"]) {
      fireEvent.change(search, { target: { value: term } });
      expect(screen.getByRole("checkbox", { name: "Select Tone" })).toBeTruthy();
      expect(screen.queryByRole("checkbox", { name: "Select Accuracy" })).toBeNull();
    }
    fireEvent.change(search, { target: { value: "missing" } });
    expect(screen.getByText("No evaluators found")).toBeTruthy();
  });

  it("retains selected rows across searches and lets users remove them", () => {
    open();
    fireEvent.click(screen.getByRole("checkbox", { name: "Select Tone" }));
    fireEvent.click(screen.getByRole("checkbox", { name: "Select Accuracy" }));
    fireEvent.change(screen.getByRole("searchbox"), { target: { value: "missing" } });
    const selected = within(screen.getByRole("region", { name: "Selected evaluators" }));
    expect(selected.getAllByRole("listitem")).toHaveLength(2);
    expect(selected.getByText("LLM judge")).toBeTruthy();
    expect(selected.getByText("Support")).toBeTruthy();
    fireEvent.click(selected.getByRole("button", { name: "Remove Tone" }));
    expect(selected.getAllByRole("listitem")).toHaveLength(1);
    expect(selected.queryByText("Tone")).toBeNull();
  });

  it("requires a name and evaluator, then submits all selections without a capability", async () => {
    open();
    const submit = screen.getByRole("button", { name: "Create eval set" }) as HTMLButtonElement;
    expect(submit.disabled).toBe(true);
    fireEvent.change(screen.getByLabelText("Name"), { target: { value: " Quality " } });
    expect(submit.disabled).toBe(true);
    fireEvent.click(screen.getByRole("checkbox", { name: "Select Accuracy" }));
    fireEvent.click(screen.getByRole("checkbox", { name: "Select Tone" }));
    expect(submit.disabled).toBe(false);
    expect(
      (screen.getByRole("checkbox", { name: "Select Unavailable" }) as HTMLButtonElement).disabled
    ).toBe(true);
    fireEvent.click(submit);
    await waitFor(() =>
      expect(mocks.create).toHaveBeenCalledWith({
        capability: null,
        evaluatorIds: ["generic", "mapped"],
        name: "Quality",
        project: "project",
      })
    );
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  });

  it("preserves selections when creation fails", async () => {
    mocks.create.mockRejectedValue(new Error("Name already exists"));
    open();
    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "Quality" } });
    fireEvent.change(screen.getByRole("combobox"), { target: { value: "cap" } });
    fireEvent.click(screen.getByRole("checkbox", { name: "Select Accuracy" }));
    fireEvent.click(screen.getByRole("button", { name: "Create eval set" }));
    await screen.findByRole("alert");
    expect(screen.getByRole("button", { name: "Remove Accuracy" })).toBeTruthy();
  });
});
