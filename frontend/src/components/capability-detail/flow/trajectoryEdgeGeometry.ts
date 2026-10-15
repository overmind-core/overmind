import { type EdgeProps, getSmoothStepPath } from "@xyflow/react";

import type { TrajectoryRoute } from "./buildTrajectoryGraph";
import {
  CONDITION_HEIGHT,
  CONDITION_WIDTH,
  CORNER_RADIUS,
  roundedRoute,
} from "./trajectoryRouting";

export function trajectoryEdgeGeometry(props: EdgeProps, route?: TrajectoryRoute) {
  const [path, x, y] = getSmoothStepPath({ ...props, borderRadius: CORNER_RADIUS });
  const start = route?.points[0];
  const end = route?.points.at(-1);
  // Tool returns must remain complete independent edges, not cached shared trunks.
  // Layout paths are valid only at the exact handles for which they were built.
  // React Flow owns routing and label placement as soon as either handle moves.
  const current =
    !(props.data?.relation === "continuation" && props.data?.groupId) &&
    start &&
    end &&
    Math.abs(start.x - props.sourceX) < 0.01 &&
    Math.abs(start.y - props.sourceY) < 0.01 &&
    Math.abs(end.x - props.targetX) < 0.01 &&
    Math.abs(end.y - props.targetY) < 0.01;
  return {
    label:
      current && route.label
        ? { x: route.label.x + CONDITION_WIDTH / 2, y: route.label.y + CONDITION_HEIGHT / 2 }
        : { x, y },
    path: current ? roundedRoute(route.points) : path,
  };
}
