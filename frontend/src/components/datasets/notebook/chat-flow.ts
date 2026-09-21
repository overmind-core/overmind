import type { AgentActivityPart } from "@/components/agent-activity/activity-timeline";
import type { ChatCellRef } from "@/hooks/use-datasets";

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
