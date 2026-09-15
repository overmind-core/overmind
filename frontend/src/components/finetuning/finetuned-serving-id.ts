import { getModelProviderInfo } from "@/components/model-provider";

/** Serving ids look like `ft-{jobUuid8}-{base-slug}` (see `_make_model_id`). */
const FT_SERVING_ID_RE = /^ft-[0-9a-f]{8}-(.+)$/i;

export function isFinetunedServingId(model: string): boolean {
  return FT_SERVING_ID_RE.test(model.trim());
}

/** `ft-750caa9f-qwen2-5-7b-instruct` → `Qwen2 5 7B Instruct · FT` */
export function finetunedChipLabel(modelId: string): string {
  const m = FT_SERVING_ID_RE.exec(modelId.trim());
  const baseSlug = m?.[1] ?? modelId.trim();
  return `${getModelProviderInfo(baseSlug).modelLabel} · FT`;
}
