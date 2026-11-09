import type { PreparationNode } from "@/openapi";

export const partitionLabels: Record<string, string> = {
  calibration: "Calibration data",
  development: "Development data",
  final: "Final evaluation data",
  train: "Training data",
};

export function outputNodes(nodes: PreparationNode[], activeId: string | undefined) {
  const training = nodes.filter((node) => node.role === "train");
  return training.length ? training : nodes.filter((node) => node.cell.id === activeId);
}
