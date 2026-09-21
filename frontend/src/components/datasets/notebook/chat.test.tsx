// @vitest-environment jsdom

import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { DatasetChat } from "@/components/datasets/notebook/chat";
import { Icon } from "@/components/ui/icons";
import type { ChatTurn } from "@/hooks/use-datasets";
import type { Cell } from "@/openapi";

const source = { fingerprint: "source-hash", id: "source", state: "ok", title: "Source" } as Cell;
const proposal = {
  id: "proposal",
  note: "Exclude invalid rows",
  review: {
    input_fingerprint: "source-hash",
    kind: "semantic",
    rows_after: 250,
    rows_before: 270,
    rows_removed: 20,
  },
  state: "proposed",
  title: "Exclude invalid rows",
} as unknown as Cell;
const generated = {
  fingerprint: "generated-hash",
  id: "generated",
  review: { generated_rows: 50, kind: "synthetic", target_rows: 500 },
  rows: 320,
  state: "ok",
  title: "Synthetic examples",
  version: "1.1",
} as unknown as Cell;
const callbacks = () => ({
  onAccept: vi.fn(),
  onDiscard: vi.fn(),
  onSelect: vi.fn(),
  onSend: vi.fn(),
  state: "idle",
});

beforeEach(() => {
  vi.stubGlobal(
    "ResizeObserver",
    class {
      observe() {}
      disconnect() {}
    }
  );
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe("Workshop chat", () => {
  it("shows a pending approval without a failure or a running spinner after reload", () => {
    const turns: ChatTurn[] = [
      {
        at: new Date().toISOString(),
        cells: [{ action: "proposed", id: proposal.id }],
        role: "agent",
        status: "awaiting_approval",
        text: "Review the proposed changes.",
      },
    ];
    const { rerender } = render(
      <DatasetChat
        busy={false}
        cells={[source, proposal]}
        live={null}
        turns={turns}
        {...callbacks()}
      />
    );
    expect(screen.getByText("Awaiting approval")).toBeTruthy();
    expect(screen.queryByText("Incomplete")).toBeNull();
    expect(screen.queryByText("Thinking…")).toBeNull();
    rerender(
      <DatasetChat
        busy
        cells={[source, { ...proposal, rows: 250, state: "ok" }]}
        live={null}
        turns={[{ ...turns[0], status: "resolved" }]}
        {...callbacks()}
      />
    );
    expect(screen.queryByText("Awaiting approval")).toBeNull();
    expect(screen.queryByText("Incomplete")).toBeNull();
    expect(screen.getByRole("button", { name: /Exclude invalid rows.*250 rows/ })).toBeTruthy();
  });

  it("shows unreferenced proposals and applies one complete result", () => {
    const actions = callbacks();
    render(
      <DatasetChat busy={false} cells={[source, proposal]} live={null} turns={[]} {...actions} />
    );
    expect(screen.getByText("Needs review")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Approve" }));
    expect(actions.onAccept).toHaveBeenCalledWith("proposal");
    expect(screen.queryByRole("button", { name: "Generate examples" })).toBeNull();
  });

  it("shows before and after examples and denies a recommendation without applying it", () => {
    const actions = callbacks();
    const recommendation = {
      ...proposal,
      review: {
        ...proposal.review,
        input_examples: [{ expected_output: "yes", source_row: 1 }],
        output_examples: [{ expected_output: "abstain", source_row: 1 }],
        rows_after: 270,
        rows_removed: 0,
      },
    } as Cell;
    render(
      <DatasetChat
        busy={false}
        cells={[source, recommendation]}
        live={null}
        turns={[]}
        {...actions}
      />
    );
    expect(screen.getByText("Input examples")).toBeTruthy();
    expect(screen.getByText("Output examples")).toBeTruthy();
    expect(screen.getByText(/"expected_output": "yes"/)).toBeTruthy();
    expect(screen.getByText(/"expected_output": "abstain"/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Deny" }));
    expect(actions.onDiscard).toHaveBeenCalledWith("proposal");
    expect(actions.onAccept).not.toHaveBeenCalled();
  });

  it("restores running progress after a reload without a live stream", () => {
    const turns: ChatTurn[] = [
      {
        at: new Date().toISOString(),
        cells: [{ action: "ran", id: "generated" }],
        progress: {
          cell_id: "generated",
          detail: "Cover rare cases",
          generated_rows: 50,
          label: "Generating examples",
          rows_before: 270,
          stage: "generating",
          target_rows: 500,
        },
        role: "agent",
        status: "running",
        text: "",
      },
    ];
    render(
      <DatasetChat busy cells={[source, generated]} live={null} turns={turns} {...callbacks()} />
    );
    expect(screen.getByText("50 of 230 rows added")).toBeTruthy();
    expect(
      screen.getByRole("button", { name: /Synthetic examples.*320 rows.*1.1.*ran/ })
    ).toBeTruthy();
    expect(screen.queryByText("Draft")).toBeNull();
    expect(screen.queryByText("Not applied")).toBeNull();
    expect(screen.queryByRole("region", { name: "Proposed changes" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Approve" })).toBeNull();
    expect(
      screen.getByRole("textbox", { name: "Message the agent" }).hasAttribute("disabled")
    ).toBe(false);
  });

  it("keeps partial generation as an active result and accepts a follow-up prompt", () => {
    const actions = callbacks();
    const partial = {
      ...generated,
      review: { ...generated.review, generated_rows: 5, rows_after: 275 },
      rows: 275,
    } as Cell;
    render(
      <DatasetChat
        busy={false}
        cells={[source, partial]}
        live={null}
        turns={[
          {
            at: "now",
            cells: [{ action: "ran", id: "generated" }],
            error: "Provider interrupted",
            role: "agent",
            status: "error",
            text: "5 rows added.",
          },
        ]}
        {...actions}
      />
    );
    expect(screen.getByRole("button", { name: /Synthetic examples.*275 rows.*ran/ })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Approve" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Continue in chat" })).toBeNull();
    const input = screen.getByRole("textbox", { name: "Message the agent" });
    fireEvent.change(input, { target: { value: "Continue to 500 rows" } });
    fireEvent.keyDown(input, { key: "Enter" });
    expect(actions.onSend).toHaveBeenCalledWith("Continue to 500 rows");
  });

  it("blocks stale proposal approval", () => {
    render(
      <DatasetChat
        busy={false}
        cells={[{ ...source, fingerprint: "changed" }, proposal]}
        live={null}
        turns={[]}
        {...callbacks()}
      />
    );
    expect(screen.getByText("Out of date")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Approve" }).hasAttribute("disabled")).toBe(true);
  });

  it("reports silent periods without inventing progress", () => {
    vi.useFakeTimers();
    const at = new Date().toISOString();
    render(
      <DatasetChat
        busy
        cells={[source]}
        live={{
          cells: [],
          progress: {
            detail: "Cover rare cases",
            generated_rows: 5,
            label: "Generating examples",
            rows_before: 270,
            stage: "generating",
            target_rows: 500,
            updated_at: at,
          },
          steps: [],
          text: "",
        }}
        turns={[]}
        {...callbacks()}
      />
    );
    act(() => vi.advanceTimersByTime(31_000));
    expect(screen.getByText(/No new activity for 31s/)).toBeTruthy();
    expect(screen.getByText("5 of 230 rows added")).toBeTruthy();
  });

  it("does not show a worker-lost turn as still running", () => {
    render(
      <DatasetChat
        busy={false}
        cells={[source]}
        live={null}
        turns={[{ at: "now", role: "agent", status: "running", text: "" }]}
        {...callbacks()}
      />
    );
    expect(screen.getByText("Incomplete")).toBeTruthy();
    expect(screen.getByRole("alert").textContent).toContain("stopped before completion");
    expect(screen.queryByLabelText("Workshop activity")).toBeNull();
  });

  it("opens thinking prose above the answer while working and collapses it on completion", () => {
    const explanation =
      "The source is missing rare cases. I will check label coverage before generating variants.";
    const turn: ChatTurn = {
      at: "now",
      role: "agent",
      status: "running",
      steps: [
        {
          id: "thought",
          phase: "thinking",
          status: "running",
          text: explanation,
          type: "activity",
        },
        {
          id: "query",
          phase: "tool_start",
          summary: "SELECT label, count(*) FROM t GROUP BY label",
          title: "Check label coverage",
          tool: "query",
          type: "activity",
        },
        {
          id: "query",
          ok: true,
          phase: "tool_done",
          preview: '{"rare": 3}',
          tool: "query",
          type: "activity",
        },
      ],
      text: "",
    };
    const { rerender } = render(
      <DatasetChat busy cells={[source]} live={null} turns={[turn]} {...callbacks()} />
    );
    const thinking = screen.getByRole("region", { name: "Thinking and steps" });
    const toggle = within(thinking).getByRole("button", { name: "Thinking…" });
    expect(toggle.getAttribute("aria-expanded")).toBe("true");
    expect(within(thinking).getByText(explanation).closest("pre")).toBeNull();
    expect(within(thinking).getByText("Check label coverage")).toBeTruthy();
    expect(thinking.querySelectorAll(".sidebar-elbow")).toHaveLength(2);
    rerender(
      <DatasetChat
        busy={false}
        cells={[source]}
        live={null}
        turns={[
          {
            ...turn,
            status: "complete",
            steps: [
              ...turn.steps!,
              {
                duration_ms: 12_000,
                id: "thought",
                phase: "thinking",
                status: "done",
                text: explanation,
                type: "activity",
              },
            ],
            text: "Three rare cases are present.",
          },
        ]}
        {...callbacks()}
      />
    );
    expect(toggle.getAttribute("aria-expanded")).toBe("false");
    expect(toggle.textContent).toContain("Thought for 12s");
    fireEvent.click(toggle);
    expect(toggle.getAttribute("aria-expanded")).toBe("true");
    expect(within(thinking).getByText(explanation)).toBeTruthy();
  });

  it("respects collapsing the live thinking section as more text arrives", () => {
    const live = { cells: [], steps: [], text: "" };
    const { rerender } = render(
      <DatasetChat busy cells={[source]} live={live} turns={[]} {...callbacks()} />
    );
    const toggle = screen.getByRole("button", { name: "Thinking…" });
    fireEvent.click(toggle);
    rerender(
      <DatasetChat
        busy
        cells={[source]}
        live={{
          ...live,
          steps: [
            {
              id: "first",
              phase: "thinking",
              status: "running",
              text: "Checking the source.",
              type: "activity",
            },
          ],
        }}
        turns={[]}
        {...callbacks()}
      />
    );
    expect(toggle.getAttribute("aria-expanded")).toBe("false");
  });

  it("interleaves explanations, thinking and cell results in live and saved turns", () => {
    const actions = callbacks();
    const cell = {
      ...source,
      id: "shape",
      rows: 891,
      title: "Build messages",
      version: "1.1",
    } as Cell;
    const turn: ChatTurn = {
      at: "now",
      cells: [{ action: "ran", id: cell.id, text_offset: 31 }],
      role: "agent",
      status: "running",
      steps: [
        {
          duration_ms: 2000,
          id: "plan",
          phase: "thinking",
          status: "done",
          text: "Reading the source.",
          text_offset: 0,
          type: "activity",
        },
        {
          id: "shape",
          phase: "tool_start",
          text_offset: 31,
          title: "Build messages",
          tool: "add_cell",
          type: "activity",
        },
        {
          id: "shape",
          ok: true,
          phase: "tool_done",
          text_offset: 31,
          tool: "add_cell",
          type: "activity",
        },
      ],
      text: "I will build the conversations.\n\n891 rows are ready.",
    };
    const { rerender } = render(
      <DatasetChat busy cells={[source, cell]} live={null} turns={[turn]} {...actions} />
    );
    const plan = screen.getByText("I will build the conversations.");
    const result = screen.getByRole("button", { name: /Build messages.*891 rows.*1.1.*ran/ });
    const answer = screen.getByText("891 rows are ready.");
    expect(plan.compareDocumentPosition(result) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(result.compareDocumentPosition(answer) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(
      screen.getByRole("button", { name: "Thought for 2s" }).getAttribute("aria-expanded")
    ).toBe("false");
    fireEvent.click(result);
    expect(actions.onSelect).toHaveBeenCalledWith("shape");
    rerender(
      <DatasetChat
        busy={false}
        cells={[source, cell]}
        live={null}
        turns={[{ ...turn, status: "complete" }]}
        {...actions}
      />
    );
    expect(screen.getByRole("button", { name: /Build messages.*891 rows.*1.1.*ran/ })).toBe(result);
    expect(screen.queryByRole("button", { name: "Thinking…" })).toBeNull();
  });

  it("omits agent-name labels and composer helper text", () => {
    const turns: ChatTurn[] = [
      { at: "empty", role: "agent", text: "" },
      { at: "reply", role: "agent", text: "Three rows." },
    ];
    const { rerender } = render(
      <DatasetChat busy={false} cells={[source]} live={null} turns={turns} {...callbacks()} />
    );
    expect(screen.queryByText("Overmind")).toBeNull();
    expect(screen.queryByText(/Enter to send/)).toBeNull();
    rerender(<DatasetChat busy cells={[source]} live={null} turns={turns} {...callbacks()} />);
    expect(screen.queryByText("Send when this request finishes")).toBeNull();
    expect(screen.getByRole("button", { name: "Send" })).toBeTruthy();
  });

  it("omits chat dividers while keeping tables, thinking connectors and the input border", async () => {
    const { container } = render(
      <DatasetChat
        busy={false}
        cells={[source]}
        live={null}
        turns={[
          {
            at: "reply",
            role: "agent",
            steps: [
              {
                duration_ms: 1000,
                id: "thought",
                phase: "thinking",
                status: "done",
                text: "# Evidence\n\nRead the source.\n\n---\n\nChecked the policy.",
                type: "activity",
              },
            ],
            text: "# Result\n\n---\n\n| Check | Result |\n| --- | --- |\n| Evidence | Pass |",
          },
        ]}
        {...callbacks()}
      />
    );
    fireEvent.click(screen.getByRole("button", { name: "Thought for 1s" }));
    await screen.findByRole("table");
    await screen.findByRole("heading", { name: "Evidence" });
    expect(container.querySelector("hr")).toBeNull();
    expect(screen.getByRole("heading", { name: "Result" }).className).not.toContain("border-b");
    expect(container.querySelector(".sidebar-elbow")).toBeTruthy();
    expect(screen.getByRole("columnheader", { name: "Check" }).className).toContain("border");
    const composer = screen.getByRole("textbox", { name: "Message the agent" }).parentElement!;
    expect(composer.className).toContain("border");
    expect(composer.parentElement!.className).not.toContain("border-t");
  });

  it("uses the vendored send icon and sends the prompt", () => {
    const actions = callbacks();
    render(<DatasetChat busy={false} cells={[source]} live={null} turns={[]} {...actions} />);
    const { container: icon } = render(<Icon.send />);
    const button = screen.getByRole("button", { name: "Send" });
    expect(button.querySelector("svg")?.innerHTML).toBe(icon.querySelector("svg")?.innerHTML);
    fireEvent.change(screen.getByRole("textbox", { name: "Message the agent" }), {
      target: { value: "Repair the selected task" },
    });
    fireEvent.click(button);
    expect(actions.onSend).toHaveBeenCalledWith("Repair the selected task");
  });
});
