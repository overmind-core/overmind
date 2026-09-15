import type { BehaviourCoverageEntry } from "@/hooks/use-behaviours";
import type { BehaviourEvaluator, EvaluatorCatalog } from "@/openapi";

/** Camelized `config["behaviour"]` binding as served by the evaluators action. */
export type BehaviourBinding = { role?: string; anchorSegment?: string[] };

export function anchorShortName(qualname: string): string {
  const parts = qualname.split(".");
  return parts[parts.length - 1] || qualname;
}

export function segmentLabel(segment: string[]): string {
  return segment.map(anchorShortName).join(" → ");
}

export function suiteSize(entry: BehaviourCoverageEntry): number {
  return entry.outcomeEvaluators.length + entry.steps.reduce((n, s) => n + s.evaluators.length, 0);
}

export function unscoredStepLabels(entry: BehaviourCoverageEntry): string[] {
  return entry.steps.filter((s) => !s.covered).map((s) => segmentLabel(s.segment));
}

export function splitSuite(evaluators: BehaviourEvaluator[]): {
  step: BehaviourEvaluator[];
  outcome: BehaviourEvaluator[];
} {
  const step: BehaviourEvaluator[] = [];
  const outcome: BehaviourEvaluator[] = [];
  for (const ev of evaluators) {
    if ((ev.behaviourBinding as BehaviourBinding).role === "step") {
      step.push(ev);
    } else {
      outcome.push(ev);
    }
  }
  return { outcome, step };
}

/** Generic (capability-less) evaluators are always unassigned. */
export function unassignedCatalog(
  catalog: EvaluatorCatalog[],
  entriesByCapability: Map<string, BehaviourCoverageEntry[]>
): EvaluatorCatalog[] {
  const boundByCapability = new Map<string, Set<string>>();
  for (const [capabilityId, entries] of entriesByCapability) {
    const names = new Set<string>();
    for (const entry of entries) {
      for (const name of entry.outcomeEvaluators) names.add(name);
      for (const step of entry.steps) {
        for (const name of step.evaluators) names.add(name);
      }
    }
    boundByCapability.set(capabilityId, names);
  }
  return catalog.filter(
    (ev) => !(ev.capability && boundByCapability.get(ev.capability)?.has(ev.name))
  );
}
