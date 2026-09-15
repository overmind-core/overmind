import { getModelProviderInfo } from "@/components/model-provider";

const normModelKey = (s: string) => s.toLowerCase().replace(/[\s\-_/.:]+/g, "");

/** True when `candidate` just restates the base model. */
function refersToBase(candidate: string, baseModelId: string, modelLabel: string): boolean {
  const c = normModelKey(candidate);
  if (!c) return false;
  const idTail = baseModelId.includes("/")
    ? baseModelId.slice(baseModelId.indexOf("/") + 1)
    : baseModelId;
  return [modelLabel, baseModelId, idTail]
    .map(normModelKey)
    .filter(Boolean)
    .some((k) => k === c || k.includes(c) || c.includes(k));
}

/** Model names are `base · dataset · capability`, blank parts dropped; the legacy
    shape puts the base last. Returns "" when stripping consumes the whole name,
    so callers can substitute a label of their own. */
export function primaryNameWithoutBase(
  displayName: string | null | undefined,
  baseModelId: string,
  modelLabel?: string,
  capabilityName?: string | null
): string {
  const raw = displayName?.trim() || "";
  if (!raw) return "Fine-tuned model";
  const label = modelLabel ?? getModelProviderInfo(baseModelId).modelLabel;

  let parts = raw
    .split(" · ")
    .map((p) => p.trim())
    .filter(Boolean);

  parts = parts.filter((p) => !refersToBase(p, baseModelId, label));

  if (capabilityName) {
    const capabilityKey = normModelKey(capabilityName);
    if (capabilityKey) parts = parts.filter((p) => normModelKey(p) !== capabilityKey);
    return parts.join(" · ");
  }

  return parts.join(" · ") || raw;
}
