import type { Capability, CapabilityFlowToolArgument } from "@/openapi";

type Flow = Capability["flow"];

export type ComponentKind = "task" | "tool" | "utility";

type EntryFact = { label: string; value: string };

/** `signal` is the most identifying attribute for the kind — a tool's side effect,
 * a task or utility's model. */
export type ComponentEntry = {
  id: string;
  kind: ComponentKind;
  name: string;
  purpose: string;
  signal: string;
  traits: string[];
  facts: EntryFact[];
  args: CapabilityFlowToolArgument[];
  prompt: string;
  /** `path#Lstart-Lend` spans. */
  provenance: string[];
};

export const KIND_META: Record<
  ComponentKind,
  { label: string; plural: string; description: string }
> = {
  task: {
    description: "The distinct ways this capability runs; each maps to a scored task.",
    label: "Task",
    plural: "Tasks",
  },
  tool: {
    description: "Functions the capability can call to act or fetch data.",
    label: "Tool",
    plural: "Tools",
  },
  utility: {
    description: "Single-shot LLM helpers invoked inside the capability.",
    label: "Utility",
    plural: "Utilities",
  },
};

export const KIND_ORDER: ComponentKind[] = ["task", "tool", "utility"];

const SIDE_EFFECT_LABELS: Record<string, string> = {
  external: "external call",
  none: "no side effect",
  read: "reads",
  write: "writes",
};

const CARDINALITY_LABELS: Record<string, string> = {
  per_candidate: "per candidate",
  per_row: "per row",
  per_run: "per run",
  unknown: "cardinality unknown",
};

const clean = (value: string | null | undefined): string => (value ?? "").trim();

/** Split a utility's free-form `in: … -> out: …` contract into a readable pair. */
function ioFacts(raw: string): EntryFact[] {
  const match = raw.match(/^([\s\S]*?)(?:->|→)([\s\S]*)$/);
  const input = clean(match?.[1]).replace(/^\s*(?:input|in)\s*:\s*/i, "");
  const output = clean(match?.[2]).replace(/^\s*(?:output|out)\s*:\s*/i, "");
  if (!input || !output) return [{ label: "I/O", value: raw }];
  return [
    { label: "Takes", value: input },
    { label: "Returns", value: output },
  ];
}

function buildTasks(flow: Flow): ComponentEntry[] {
  return (flow.modes ?? [])
    .filter((mode) => clean(mode.name))
    .map((mode) => {
      const facts: EntryFact[] = [];
      if (clean(mode.routing)) facts.push({ label: "Runs when", value: clean(mode.routing) });
      if (clean(mode.output)) facts.push({ label: "Returns", value: clean(mode.output) });
      if (clean(mode.entrypointFn))
        facts.push({ label: "Entrypoint", value: clean(mode.entrypointFn) });
      if (clean(mode.promptBuilder))
        facts.push({ label: "Prompt builder", value: clean(mode.promptBuilder) });

      return {
        args: [],
        facts,
        id: `task:${mode.name}`,
        kind: "task" as const,
        name: mode.name,
        prompt: clean(mode.prompt) || clean(mode.promptExcerpt),
        provenance: clean(mode.sourcePath) ? [clean(mode.sourcePath)] : [],
        purpose: clean(mode.purpose),
        signal: clean(mode.model),
        traits: [],
      };
    });
}

function buildTools(flow: Flow): ComponentEntry[] {
  return (flow.toolSpec ?? [])
    .filter((tool) => clean(tool.name))
    .map((tool) => {
      const args = (tool.arguments ?? []).filter((arg) => clean(arg.name));
      const facts: EntryFact[] = [];
      if (clean(tool.returns)) facts.push({ label: "Returns", value: clean(tool.returns) });
      // The legacy free-form `args` blob would duplicate the structured table.
      if (args.length === 0 && clean(tool.args))
        facts.push({ label: "Args", value: clean(tool.args) });
      if (clean(tool.integration))
        facts.push({ label: "Integration", value: clean(tool.integration) });

      const sideEffect = clean(tool.sideEffect).toLowerCase();
      const required = args.filter((arg) => arg.required).length;

      return {
        args,
        facts,
        id: `tool:${tool.name}`,
        kind: "tool" as const,
        name: tool.name,
        prompt: "",
        provenance: (tool.provenance ?? []).filter((span) => clean(span)),
        purpose: clean(tool.purpose),
        signal: SIDE_EFFECT_LABELS[sideEffect] ?? sideEffect,
        traits: args.length
          ? [`${args.length} arg${args.length === 1 ? "" : "s"}`, `${required} required`]
          : [],
      };
    });
}

function buildUtilities(flow: Flow): ComponentEntry[] {
  return (flow.llmUtilities ?? [])
    .filter((util) => clean(util.name))
    .map((util) => {
      const facts: EntryFact[] = clean(util.ioContract) ? ioFacts(clean(util.ioContract)) : [];
      if (clean(util.calledBy)) facts.push({ label: "Called by", value: clean(util.calledBy) });
      if (clean(util.entrypointFn))
        facts.push({ label: "Entrypoint", value: clean(util.entrypointFn) });

      const traits: string[] = [];
      const cardinality = clean(util.cardinality).toLowerCase();
      if (cardinality)
        traits.push(CARDINALITY_LABELS[cardinality] ?? cardinality.replace(/_/g, " "));
      if (util.structuredOutput != null)
        traits.push(util.structuredOutput ? "structured output" : "freeform output");

      return {
        args: [],
        facts,
        id: `utility:${util.name}`,
        kind: "utility" as const,
        name: util.name,
        prompt: clean(util.promptExcerpt),
        provenance: clean(util.sourcePath) ? [clean(util.sourcePath)] : [],
        purpose: clean(util.purpose),
        signal: clean(util.model),
        traits,
      };
    });
}

export function buildComponentEntries(flow: Flow): ComponentEntry[] {
  return [...buildTasks(flow), ...buildTools(flow), ...buildUtilities(flow)];
}
