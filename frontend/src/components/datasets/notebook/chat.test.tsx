// @vitest-environment jsdom

import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { DatasetChat } from "@/components/datasets/notebook/chat";
import { type ChatTurn, chatOf } from "@/hooks/use-datasets";
import { type Cell, DatasetFromJSON } from "@/openapi";

vi.mock("@/hooks/use-workshop-funding", () => ({
  useWorkshopFunding: () => ({ data: undefined }),
}));

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
  onChooseIntent: vi.fn(),
  onSelect: vi.fn(),
  onSend: vi.fn(),
  renderCell: (cell: Cell) => <div data-testid={`notebook-cell-${cell.id}`}>{cell.title}</div>,
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
  it("keeps a streamed sentence intact across tool activity and reload", async () => {
    const prefix = 'Treating "Extraing training data" as an';
    const text = `${prefix} explicit training request.`;
    const steps = [
      {
        id: "inspect",
        phase: "tool_start" as const,
        text_offset: prefix.length,
        title: "Inspect rows",
        type: "activity" as const,
      },
    ];
    const props = { ...callbacks(), cells: [source], turns: [] };
    const { rerender } = render(
      <DatasetChat {...props} busy live={{ cells: [], steps, text: prefix }} />
    );
    rerender(<DatasetChat {...props} busy live={{ cells: [], steps, text }} />);
    const paragraph = await screen.findByText(text, { selector: "p" });
    expect(paragraph.contains(screen.getByRole("region", { name: "Thinking and steps" }))).toBe(
      false
    );
    rerender(
      <DatasetChat
        {...props}
        busy={false}
        live={null}
        turns={[{ at: new Date().toISOString(), role: "agent", status: "complete", steps, text }]}
      />
    );
    expect(await screen.findByText(text, { selector: "p" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "View steps" })).toBeTruthy();
  });

  it("submits an intent chip directly and allows retry after a rejected submission", async () => {
    const onChooseIntent = vi.fn().mockResolvedValueOnce(false).mockResolvedValueOnce(true);
    const turns = chatOf(
      DatasetFromJSON({
        cells: [],
        chat: [
          {
            at: new Date().toISOString(),
            id: "intent-question",
            role: "agent",
            status: "awaiting_intent",
            text: "What will you use this data for?",
          },
        ],
      })
    );
    render(
      <DatasetChat
        {...callbacks()}
        busy={false}
        cells={[source]}
        live={null}
        onChooseIntent={onChooseIntent}
        turns={turns}
      />
    );
    expect(screen.queryByText("Incomplete")).toBeNull();
    const choices = within(screen.getByRole("group", { name: "What will you use this data for?" }));
    expect(choices.getAllByRole("button").map((button) => button.textContent)).toEqual([
      "Training",
      "Eval",
      "Data exploration",
    ]);
    expect(onChooseIntent).not.toHaveBeenCalled();
    expect(screen.queryByRole("button", { name: "Continue" })).toBeNull();
    await act(async () =>
      fireEvent.click(choices.getByRole("button", { name: "Data exploration" }))
    );
    expect(onChooseIntent).toHaveBeenCalledWith("explore", "intent-question");
    expect(screen.getByRole("alert").textContent).toContain("Couldn't save your choice");
    await act(async () =>
      fireEvent.click(choices.getByRole("button", { name: "Data exploration" }))
    );
    expect(onChooseIntent).toHaveBeenCalledTimes(2);
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("prevents repeated intent submissions while saving and recovers from a network error", async () => {
    let reject!: (error: Error) => void;
    const onChooseIntent = vi.fn().mockImplementation(
      () =>
        new Promise<boolean>((_, fail) => {
          reject = fail;
        })
    );
    const props = {
      ...callbacks(),
      busy: false,
      cells: [source],
      live: null,
      onChooseIntent,
      turns: [
        { at: "", id: "intent-question", role: "agent", status: "awaiting_intent", text: "" },
      ] as ChatTurn[],
    };
    const { rerender } = render(<DatasetChat {...props} />);
    const group = () =>
      within(screen.getByRole("group", { name: "What will you use this data for?" }));
    fireEvent.click(group().getByRole("button", { name: "Training" }));
    fireEvent.click(group().getByRole("button", { name: "Training" }));
    fireEvent.click(group().getByRole("button", { name: "Eval" }));
    expect(onChooseIntent).toHaveBeenCalledTimes(1);
    expect(onChooseIntent).toHaveBeenCalledWith("train", "intent-question");
    expect(
      group()
        .getAllByRole("button")
        .every((button) => (button as HTMLButtonElement).disabled)
    ).toBe(true);
    await act(async () => reject(new Error("Connection lost")));
    expect(screen.getByRole("alert").textContent).toContain("Connection lost");
    expect(
      group()
        .getAllByRole("button")
        .every((button) => !(button as HTMLButtonElement).disabled)
    ).toBe(true);
    rerender(<DatasetChat {...props} busy />);
    expect(
      group()
        .getAllByRole("button")
        .every((button) => (button as HTMLButtonElement).disabled)
    ).toBe(true);
  });

  it.each([
    "running",
    "awaiting_approval",
    "resolved",
    "error",
    "complete",
  ] as const)("restores %s turns through the generated dataset decoder", (status) => {
    const saved: ChatTurn = {
      at: "2026-09-20T10:00:00Z",
      cells: [{ action: "ran", id: source.id, text_offset: 0 }],
      error: status === "error" ? "Check failed" : "",
      id: "turn-1",
      ms: 4200,
      progress: {
        detail: "Adding examples",
        generated_rows: 7,
        label: "Generating",
        stage: "generating",
        target_rows: 20,
      },
      role: "agent",
      status,
      steps: [
        {
          duration_ms: 4200,
          id: "thought",
          phase: "thinking",
          status: "done",
          text: "Checking the source.",
          text_offset: 0,
          type: "activity",
        },
      ],
      text: "Checking the examples.",
    };
    const refetched = chatOf(DatasetFromJSON({ cells: [], chat: [saved] }));
    expect(refetched).toEqual([saved]);
    const props = { ...callbacks(), busy: status === "running", cells: [source], live: null };
    const { rerender } = render(<DatasetChat {...props} turns={[]} />);
    rerender(<DatasetChat {...props} turns={refetched} />);
    expect(screen.getByRole("button", { name: "Thought for 4s" })).toBeTruthy();
    expect(screen.getByText("Checking the examples.")).toBeTruthy();
    expect(screen.queryByText("Awaiting approval")).toBeNull();
    expect(!!screen.queryByText("Incomplete")).toBe(status === "error");
    if (status === "running") expect(screen.getByText("7 of 20 rows saved")).toBeTruthy();
  });

  it("retains saved turn history without restoring approval controls", () => {
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
    expect(screen.queryByText("Awaiting approval")).toBeNull();
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

  it("keeps the composer available without proposal decisions", () => {
    const actions = callbacks();
    render(
      <DatasetChat
        busy={false}
        cells={[source, proposal, { ...proposal, id: "second" }]}
        live={null}
        turns={[]}
        {...actions}
      />
    );
    expect(screen.queryByRole("region", { name: "Proposed changes" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Approve" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Reject" })).toBeNull();
    const input = screen.getByRole("textbox", { name: "Message the agent" });
    fireEvent.change(input, { target: { value: "Continue preparing the data" } });
    fireEvent.keyDown(input, { key: "Enter" });
    expect(actions.onSend).toHaveBeenCalledWith("Continue preparing the data");
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
    expect(screen.getByText("50 of 230 rows saved")).toBeTruthy();
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
    expect(screen.getByText("5 of 230 rows saved")).toBeTruthy();
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

  it("keeps thinking collapsed during work and completion until opened", () => {
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
    expect(toggle.getAttribute("aria-expanded")).toBe("false");
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
    expect(toggle.getAttribute("aria-expanded")).toBe("false");
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

  it("collapses the previous response when a new prompt starts and expands the new response", () => {
    const props = { ...callbacks(), busy: false, cells: [source, generated], live: null };
    const turn: ChatTurn = {
      at: "first",
      id: "first",
      role: "agent",
      status: "complete",
      text: "The dataset is ready.",
    };
    const { rerender } = render(<DatasetChat {...props} turns={[turn]} />);
    const response = screen.getByRole("button", { name: "Agent response" });
    expect(response.getAttribute("aria-expanded")).toBe("true");
    fireEvent.change(screen.getByRole("textbox", { name: "Message the agent" }), {
      target: { value: "Check duplicates" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));
    expect(response.getAttribute("aria-expanded")).toBe("false");
    rerender(
      <DatasetChat
        {...props}
        turns={[
          turn,
          { at: "next", role: "user", text: "Check duplicates" },
          { at: "done", id: "done", role: "agent", status: "complete", text: "No duplicate rows." },
        ]}
      />
    );
    expect(
      screen.getByRole("button", { name: "Agent response" }).getAttribute("aria-expanded")
    ).toBe("true");
    expect(screen.getByText("No duplicate rows.")).toBeTruthy();
    expect(screen.getAllByTestId("notebook-cell-generated")).toHaveLength(1);
  });

  it("sends the prompt", () => {
    const actions = callbacks();
    render(<DatasetChat busy={false} cells={[source]} live={null} turns={[]} {...actions} />);
    const button = screen.getByRole("button", { name: "Send" });
    fireEvent.change(screen.getByRole("textbox", { name: "Message the agent" }), {
      target: { value: "Repair the selected task" },
    });
    fireEvent.click(button);
    expect(actions.onSend).toHaveBeenCalledWith("Repair the selected task");
  });
});
