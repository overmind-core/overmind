import type { Capability } from "@/openapi";

export function flowModels(flow: Capability["flow"]): string[] {
  const seen = new Set<string>();
  const models: string[] = [];
  for (const raw of [flow.model, ...(flow.llmUtilities ?? []).map((u) => u.model)]) {
    const model = (raw ?? "").trim();
    if (model && !seen.has(model)) {
      seen.add(model);
      models.push(model);
    }
  }
  return models;
}
