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

function paragraphEnds(text: string) {
  const ends = [0];
  let fence = "";
  for (const match of text.matchAll(/^.*(?:\n|$)/gm)) {
    const line = match[0];
    const marker = /^ {0,3}(`{3,}|~{3,})/.exec(line)?.[1];
    if (marker) {
      if (!fence) fence = marker;
      else if (marker[0] === fence[0] && marker.length >= fence.length) fence = "";
    }
    if (!fence && !line.trim()) ends.push(match.index + line.length);
  }
  ends.push(text.length);
  return ends;
}

export function chatSections(
  text: string,
  steps: AgentActivityPart[],
  cells: ChatCellRef[],
  live = false
) {
  const boundaries = paragraphEnds(text);
  const groups = new Map<number, ChatSection>();
  const group = (position: number) => {
    // Actions may arrive between tokens, but must not split a paragraph or code fence.
    const offset = boundaries.find((end) => end >= position) ?? text.length;
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
