import { getNodesBounds } from "@xyflow/react";

import type { TrajectoryNode } from "./buildTrajectoryGraph";
import { CONDITION_HEIGHT, CONDITION_WIDTH } from "./trajectoryRouting";

export function trajectoryBounds(nodes: TrajectoryNode[]) {
  return getNodesBounds(nodes);
}

export function trajectoryOverviewBounds(nodes: TrajectoryNode[]) {
  const bounds = trajectoryBounds(nodes);
  const points = nodes.flatMap((node) =>
    Object.values(node.data.routes ?? {}).flatMap((route) => [
      ...route.points,
      ...(route.label
        ? [route.label, { x: route.label.x + CONDITION_WIDTH, y: route.label.y + CONDITION_HEIGHT }]
        : []),
    ])
  );
  const x = Math.min(bounds.x, ...points.map((p) => p.x));
  const y = Math.min(bounds.y, ...points.map((p) => p.y));
  return {
    height: Math.max(bounds.y + bounds.height, ...points.map((p) => p.y)) - y,
    width: Math.max(bounds.x + bounds.width, ...points.map((p) => p.x)) - x,
    x,
    y,
  };
}
