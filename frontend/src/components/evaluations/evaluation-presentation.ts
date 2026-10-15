import { matchEvaluationAnchor } from "@/components/capability-detail/flow/bindTrajectoryEvaluations";
import type { Capability, EvalMatrix } from "@/openapi";

const STOCK_NAME: Record<string, string> = {
  contains: "Required text",
  cost_budget: "Model cost limit",
  exact_match: "Exact answer",
  goal_state_match: "Required goal markers",
  json_required_keys: "Required JSON fields",
  latency_budget: "Response time limit",
  llm_call_budget: "Model call limit",
  output_is_valid_json: "Valid JSON",
  regex: "Text pattern",
  schema_field_conformance: "Field types and values",
  tool_args_match: "Tool arguments",
  tool_selection: "Tool selection",
};
export function evaluationName(row: EvalMatrix) {
  return row.definitionSource === "stock" ? (STOCK_NAME[row.name] ?? row.name) : row.name;
}

export type ScopeKind = "whole" | "model" | "step" | "tool" | "other";
type Scope = { label: string; detail: string; kind: ScopeKind; order: number };

export function presentEvaluations(rows: EvalMatrix[], capability?: Capability) {
  const targets: { id: string; anchors: string[]; scope: Scope }[] = [];
  const seen = new Set<string>();
  for (const path of capability?.flow.trajectoryMap ?? []) {
    for (const step of path.steps ?? []) {
      const key = JSON.stringify([step.step, [...(step.anchors ?? [])].sort(), step.kind]);
      if (seen.has(key)) continue;
      seen.add(key);
      targets.push({
        anchors: step.anchors ?? [],
        id: key,
        scope: {
          detail: step.kind === "model_invocation" ? "Model decision" : "Execution step",
          kind: step.kind === "model_invocation" ? "model" : "step",
          label: step.step,
          order: targets.length + 1,
        },
      });
    }
  }
  for (const [i, tool] of (capability?.flow.toolSpec ?? []).entries())
    targets.push({
      anchors: [tool.name],
      id: `tool:${tool.name}`,
      scope: {
        detail: tool.cluster || "Tool call",
        kind: "tool",
        label: tool.name,
        order: 1000 + i,
      },
    });
  return rows
    .map((row) => {
      const rawName = evaluationName(row);
      let scope: Scope;
      if (capability?.entrypointFn && row.spanName === capability.entrypointFn) {
        scope = {
          detail: capability?.name || "Complete run",
          kind: "whole",
          label: "Whole agent",
          order: 0,
        };
      } else if (!row.spanName) {
        const stepName = /^Step:\s*(.+?)(?:\s+·\s+\d+)?$/i.exec(rawName)?.[1];
        const inferredStep = stepName
          ? targets.find(
              (target) =>
                (target.scope.kind === "step" || target.scope.kind === "model") &&
                (target.scope.label === stepName || stepName.startsWith(`${target.scope.label} ·`))
            )
          : undefined;
        if (inferredStep) {
          scope = inferredStep.scope;
        } else if (!row.spanType && row.type === "llm") {
          scope = {
            detail: capability?.name || "Complete run",
            kind: "whole",
            label: "Whole agent",
            order: 0,
          };
        } else {
          const label =
            row.spanType === "llm_call"
              ? "All model calls"
              : row.spanType === "tool_call"
                ? "All tool calls"
                : row.spanType
                  ? `All ${row.spanType} spans`
                  : "All matching spans";
          scope = {
            detail: row.capability ? "This agent" : "Across this project",
            kind:
              row.spanType === "llm_call"
                ? "model"
                : row.spanType === "tool_call"
                  ? "tool"
                  : "other",
            label,
            order: 2000,
          };
        }
      } else {
        const id = matchEvaluationAnchor(row.spanName, targets);
        scope = targets.find((target) => target.id === id)?.scope ?? {
          detail: capability ? row.spanName : "Recorded span",
          kind: "other",
          label: capability ? "Unmapped span" : row.spanName,
          order: 2001,
        };
      }
      const authored =
        row.type === "llm"
          ? row.source.trim()
          : /^\s*r?("""|''')([\s\S]*?)\1/.exec(row.source)?.[2].trim() || "";
      const name =
        scope.kind === "tool" && rawName.startsWith(`${scope.label} · `)
          ? rawName
              .slice(scope.label.length + 3)
              .replace(/^Argument contract$/, "Arguments match the tool contract")
              .replace(/^Result flags$/, "Result flags are consistent")
          : (scope.kind === "step" || scope.kind === "model") && rawName.startsWith("Step: ")
            ? rawName.slice("Step: ".length)
            : rawName;
      return { description: authored === rawName ? "" : authored, name, row, scope };
    })
    .sort((a, b) => a.scope.order - b.scope.order || a.name.localeCompare(b.name));
}
