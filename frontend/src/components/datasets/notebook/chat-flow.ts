import type { AgentActivityPart } from "@/components/agent-activity/activity-timeline";
import type { ChatCellRef } from "@/hooks/use-datasets";
import type { Cell } from "@/openapi";

export function notebookFlow(cells: Cell[], turns: { role?: string; cells?: { id: string }[] }[]) {
  const chain = cells.filter((cell) => cell.state !== "proposed");
  const positions = new Map(chain.map((cell, index) => [cell.id, index]));
  const flow: ({ kind: "cell"; cell: Cell } | { kind: "turn"; index: number })[] = [];
  let next = 0;
  const appendThrough = (end: number) => {
    while (next <= end && next < chain.length) flow.push({ cell: chain[next++], kind: "cell" });
  };
  appendThrough(0);
  turns.forEach((turn, index) => {
    if (turn.role !== "user") {
      // A revision can refer backwards; the notebook itself must never do so.
      const end =
        index === turns.length - 1
          ? chain.length - 1
          : Math.max(next - 1, ...(turn.cells ?? []).map((ref) => positions.get(ref.id) ?? -1));
      appendThrough(end);
    }
    flow.push({ index, kind: "turn" });
  });
  appendThrough(chain.length - 1);
  return flow;
}

export interface ChatSection {
  offset: number;
  text: string;
  steps: AgentActivityPart[];
  cells: ChatCellRef[];
}

export function chatSections(
  text: string,
  steps: AgentActivityPart[],
  cells: ChatCellRef[],
  live = false
) {
  const groups = new Map<number, ChatSection>();
  const group = (position: number) => {
    const offset = Math.max(0, Math.min(text.length, position));
    let section = groups.get(offset);
    if (!section) {
      section = { cells: [], offset, steps: [], text: "" };
      groups.set(offset, section);
    }
    return section;
  };
  const stepOffsets = new Map<string, number>();
  group(0);
  if (live) group(text.length);
  for (const part of steps) {
    const offset = stepOffsets.get(part.id) ?? part.text_offset ?? 0;
    stepOffsets.set(part.id, offset);
    group(offset).steps.push(part);
  }
  for (const cell of new Map(cells.map((ref) => [ref.id, ref])).values()) {
    group(cell.text_offset ?? text.length).cells.push(cell);
  }
  const sections = [...groups.values()].sort((a, b) => a.offset - b.offset);
  for (const [index, section] of sections.entries()) {
    section.text = text.slice(section.offset, sections[index + 1]?.offset ?? text.length);
  }
  return sections;
}
