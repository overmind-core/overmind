import type { Edge } from "@xyflow/react";

import type { Cell, PipelineRun } from "@/openapi";

export function cellConnections(cells: Cell[], runs: PipelineRun[]): Edge[] {
  const ids = new Set(cells.map((cell) => cell.id));
  const parents = new Map<string, string[]>();
  for (const run of runs) {
    let parent = run.sourceCell;
    const steps = Array.isArray(run.result.steps) ? run.result.steps : [];
    for (const step of steps) {
      if (typeof step.output_cell !== "string") continue;
      parents.set(step.output_cell, [parent]);
      parent = step.output_cell;
    }
    if (run.outputCell && !parents.has(run.outputCell)) parents.set(run.outputCell, [parent]);
  }
  return cells.flatMap((cell) => {
    const review = cell.review ?? {};
    let inputs: string[] | undefined = Array.isArray(review.input_cells)
      ? review.input_cells.filter((id: unknown): id is string => typeof id === "string")
      : parents.get(cell.id);
    if (!inputs && typeof review.source_cell === "string" && !review.step) {
      inputs = [review.source_cell];
    }
    if (!inputs && cell.inputFingerprint) {
      const matches = cells.filter(
        (candidate) =>
          candidate.position < cell.position && candidate.fingerprint === cell.inputFingerprint
      );
      if (matches.length === 1) inputs = [matches[0].id];
    }
    return [...new Set(inputs ?? [])]
      .filter((source) => source !== cell.id && ids.has(source))
      .map((source) => ({
        ariaLabel:
          typeof review.condition === "string"
            ? `${review.condition}${review.condition_evidence?.evidence === "agent_declared" ? " (agent-declared condition)" : ""}`
            : undefined,
        id: `${source}:${cell.id}`,
        label:
          typeof review.condition === "string" && review.condition ? review.condition : undefined,
        source,
        style: { stroke: "var(--muted-foreground)", strokeWidth: 1.5 },
        target: cell.id,
        type: "cell",
      }));
  });
}

export function iterationCells(cells: Cell[], edges: Edge[], iteration?: string): Cell[] {
  if (!iteration || !cells.some((cell) => cell.id === iteration)) return cells;
  const visible = new Set<string>();
  const pending = [iteration];
  while (pending.length) {
    const id = pending.pop() as string;
    if (visible.has(id)) continue;
    visible.add(id);
    pending.push(...edges.filter((edge) => edge.target === id).map((edge) => edge.source));
  }
  return cells.filter((cell) => visible.has(cell.id));
}
