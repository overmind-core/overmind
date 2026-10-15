import type { EvalMatrix } from "@/openapi";

export function bindTrajectoryEvaluations(
  rows: EvalMatrix[],
  entrypoint: string,
  targets: { id: string; anchors: string[] }[]
) {
  const byNode = new Map<string, EvalMatrix[]>();
  const broad: EvalMatrix[] = [];
  const unmapped: EvalMatrix[] = [];
  for (const row of rows) {
    if (row.enabled === false) continue;
    let nodeId: string | undefined;
    if (!row.spanName) {
      if (row.type === "llm" && !row.spanType) nodeId = "entry";
      else {
        broad.push(row);
        continue;
      }
    } else if (entrypoint && row.spanName === entrypoint) {
      nodeId = "entry";
    } else {
      nodeId = matchEvaluationAnchor(row.spanName, targets);
    }
    if (nodeId) byNode.set(nodeId, [...(byNode.get(nodeId) ?? []), row]);
    else unmapped.push(row);
  }
  return { broad, byNode, unmapped };
}

export function matchEvaluationAnchor(
  spanName: string,
  targets: { id: string; anchors: string[] }[]
) {
  // Qualified SDK names may match a unique source anchor; leaf names must match exactly.
  const matches = targets.flatMap((target) =>
    target.anchors
      .filter(
        (anchor) =>
          anchor === spanName || (spanName.includes(".") && anchor.endsWith(`.${spanName}`))
      )
      .map((anchor) => ({ anchor, id: target.id }))
  );
  if (
    new Set(matches.map((m) => m.anchor)).size === 1 &&
    new Set(matches.map((m) => m.id)).size === 1
  )
    return matches[0].id;
  return undefined;
}
