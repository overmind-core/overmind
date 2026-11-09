// covers: console-logic
import { describe, expect, it } from "vitest";

import type { PreparationNode } from "@/openapi";
import { outputNodes, partitionLabels } from "./output";

const node = (id: string, role = "") =>
  ({ cell: { id }, dataset: `${id}-dataset`, role }) as PreparationNode;

describe("Workshop output selection", () => {
  it("highlights the training branch and retains its owning dataset for handoff", () => {
    const nodes = [
      node("prepared"),
      node("held-out", "final"),
      node("training", "train"),
      node("validation", "development"),
    ];
    expect(outputNodes(nodes, "prepared")).toEqual([nodes[2]]);
    expect(outputNodes(nodes, "training")[0].dataset).toBe("training-dataset");
    expect(partitionLabels[nodes[2].role]).toBe("Training data");
  });

  it("keeps the selected version as output when there is no training partition", () => {
    const nodes = [node("source"), node("prepared")];
    expect(outputNodes(nodes, "prepared")).toEqual([nodes[1]]);
    expect(outputNodes([], "prepared")).toEqual([]);
  });
});
