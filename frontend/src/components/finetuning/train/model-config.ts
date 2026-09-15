import type { ModelEntry } from "@/hooks/use-finetuning";
import { plural } from "@/lib/utils";
import type {
  ConfidenceEnum,
  FinetuningEvidence,
  FinetuningExperiment,
  FinetuningSkillScore,
} from "@/openapi";

/** models.json ``finetuning.training_type.{lora,full}.enabled`` from the catalog. */
function catalogTrainingEnabled(
  m: Pick<ModelEntry, "trainingType"> | null | undefined,
  kind: "lora" | "full"
): boolean {
  return m?.trainingType?.[kind]?.enabled === true;
}

function experimentTrainingEnabled(
  r: Pick<FinetuningExperiment, "trainingType"> | null | undefined,
  kind: "lora" | "full"
): boolean {
  return r?.trainingType?.[kind]?.enabled === true;
}

// Tiers are categorical, not status: they take cat-1…cat-4 in stable TIER_ORDER
// slots, never semantic status tokens.
export const TIER_META: Record<string, { label: string; color: string; description: string }> = {
  compact: {
    color: "border-cat-1/40 bg-cat-1/10 text-cat-1",
    description: "< 3B params",
    label: "Compact",
  },
  large: {
    color: "border-cat-4/40 bg-cat-4/10 text-cat-4",
    description: "70B+ params",
    label: "Large",
  },
  mid: {
    color: "border-cat-3/40 bg-cat-3/10 text-cat-3",
    description: "12–32B params",
    label: "Mid",
  },
  small: {
    color: "border-cat-2/40 bg-cat-2/10 text-cat-2",
    description: "7–9B params",
    label: "Small",
  },
};

export const TIER_ORDER = ["compact", "small", "mid", "large"] as const;
export const MAX_MODELS = 4;

const TIER_DEFAULTS: Record<string, { learning_rate: number; lora_r: number; lora_alpha: number }> =
  {
    compact: { learning_rate: 2e-4, lora_alpha: 32, lora_r: 16 },
    large: { learning_rate: 2e-5, lora_alpha: 16, lora_r: 8 },
    mid: { learning_rate: 5e-5, lora_alpha: 32, lora_r: 16 },
    small: { learning_rate: 1e-4, lora_alpha: 16, lora_r: 8 },
  };

export function clampBatchSize(
  value: number | string | undefined,
  min?: number,
  max?: number
): number {
  const floor = min ?? 1;
  const ceiling = max ?? 64;
  if (value === "max" || value === undefined) return Math.max(floor, ceiling);
  const n = typeof value === "number" ? value : Number.parseInt(String(value), 10);
  if (!Number.isFinite(n) || n <= 0) return floor;
  return Math.max(floor, Math.min(ceiling, n));
}

/** The benchmark standing behind one model, for the task type the response was graded
 *  against. `grade` is already shrunk toward the cohort prior by the backend. */
export interface ModelGrade {
  /** Standing among the `matchPool` graded candidates for this dataset. `grade` is the
   *  same evidence read against every model the artifact tracks, most untrainable. */
  match: number | null;
  matchRank: number | null;
  matchPool: number;
  grade: number | null;
  confidence: ConfidenceEnum;
  nBenchmarks: number;
  labClaimedOnly: boolean;
  evidence: FinetuningEvidence[];
  skillScores: FinetuningSkillScore[];
}

function ungraded(): ModelGrade {
  return {
    confidence: "none",
    evidence: [],
    grade: null,
    labClaimedOnly: false,
    match: null,
    matchPool: 0,
    matchRank: null,
    nBenchmarks: 0,
    skillScores: [],
  };
}

export function isLabClaimedOnly(evidence: FinetuningEvidence[]): boolean {
  return evidence.length > 0 && evidence.every((row) => row.provenance === "lab_claimed");
}

export function gradeFromCandidate(c: FinetuningExperiment): ModelGrade {
  const evidence = c.evidence ?? [];
  return {
    confidence: c.confidence ?? "none",
    evidence,
    grade: c.grade ?? null,
    labClaimedOnly: isLabClaimedOnly(evidence),
    match: c.match ?? null,
    matchPool: c.matchPool ?? 0,
    matchRank: c.matchRank ?? null,
    nBenchmarks: c.nBenchmarks ?? 0,
    skillScores: c.skillScores ?? [],
  };
}

/** Re-seeds a draft's standing after the model changed; the hyperparameter endpoints
 *  return ungraded rows, so the grade only ever comes from the ranked candidates.
 *  A ranked candidate also carries this dataset's narrowed training_type, so the
 *  LoRA/Full toggle is refreshed alongside the grade rather than left dataset-blind. */
export function applyCandidateGrade(
  draft: ModelDraft,
  candidate: FinetuningExperiment | undefined
): ModelDraft {
  if (!candidate) return { ...draft, ...ungraded() };
  const supportsLora = experimentTrainingEnabled(candidate, "lora");
  const supportsFull = experimentTrainingEnabled(candidate, "full");
  const useLora = supportsLora && (!supportsFull || draft.useLora);
  return { ...draft, ...gradeFromCandidate(candidate), supportsFull, supportsLora, useLora };
}

/** Two decimals throughout: the match is the sum of the per-skill points shown beside it,
 *  and a rounded total would not add up against them. */
export function formatScore(value: number | null): string {
  return value == null ? "—" : value.toFixed(2);
}

/** Ordinal alone — the caller owns the unit ("88th" → "88th pct"). */
export function ordinal(value: number): string {
  const n = Math.round(value);
  const teens = n % 100;
  const suffix = teens >= 11 && teens <= 13 ? "th" : (["th", "st", "nd", "rd"][n % 10] ?? "th");
  return `${n}${suffix}`;
}

/** The population a match number stands in. Naming it is the whole point: a bare score
 *  reads as a percentage. */
export function matchStanding(matchRank: number | null, matchPool: number): string | null {
  if (matchRank == null || matchPool <= 0) return null;
  return `${ordinal(matchRank)} of ${plural(matchPool, "model")} you can train`;
}

export function confidenceLabel(confidence: ConfidenceEnum, nBenchmarks: number): string {
  if (confidence === "none" || nBenchmarks === 0) return "not graded";
  return `${confidence} confidence`;
}

export interface ModelDraft extends ModelGrade {
  id: string;
  tier: string;
  model: string;
  displayName: string;
  params: string;
  useLora: boolean;
  supportsLora: boolean;
  /** Catalog ``training_type.full.enabled`` — false means Full FT is unavailable. */
  supportsFull: boolean;
  learningRateLora: number;
  learningRateFull: number;
  minBatchSize?: number;
  maxBatchSize?: number;
  contextLengthSft?: number;
  hyperparams: {
    /** null ⇒ not yet derived; the backend policy gap-fills at submission. */
    n_epochs: number | null;
    learning_rate: number;
    batch_size: number | null;
    warmup_ratio: number;
    context_length?: number;
  };
  loraParams: { lora_r: number; lora_alpha: number; lora_dropout: number };
  /** The recommender's originals, for marking a field user-modified and resetting it. */
  recommendedValues?: Partial<
    Record<"n_epochs" | "learning_rate" | "batch_size" | "lora_r", number>
  >;
}

export function draftFromRecommendation(
  r: FinetuningExperiment,
  catalogModel: ModelEntry | undefined
): ModelDraft {
  const hp = (r.hyperparams ?? {}) as Record<string, unknown>;
  const tt = (hp.training_type ?? {}) as Record<string, unknown>;
  // r.trainingType is narrowed to this dataset's longest row (models.json's per-kind
  // context_length, not just catalog enablement) — trust it over the dataset-blind
  // catalog, so a kind that would truncate targets never reaches the toggle.
  const supportsLora = experimentTrainingEnabled(r, "lora");
  const supportsFull = experimentTrainingEnabled(r, "full");
  const useLora = supportsLora && (!supportsFull || r.useLora || tt.type === "Lora");
  const minBs = catalogModel?.minBatchSize ?? r.minBatchSize ?? 1;
  const maxBs = catalogModel?.maxBatchSize ?? r.maxBatchSize ?? 64;
  const rank = Number(tt.lora_r) || TIER_DEFAULTS[r.tier]?.lora_r || 16;
  const contextLength =
    typeof hp.context_length === "number" && hp.context_length > 0 ? hp.context_length : undefined;
  const batch = clampBatchSize(hp.batch_size as number | string | undefined, minBs, maxBs);
  const lr = Number(hp.learning_rate) || (useLora ? r.learningRateLora : r.learningRateFull);
  const epochs = Number(hp.n_epochs) || 3;
  return {
    ...gradeFromCandidate(r),
    contextLengthSft: catalogModel?.contextLengthSft ?? r.contextLengthSft ?? undefined,
    displayName: catalogModel?.display ?? r.displayName,
    hyperparams: {
      batch_size: batch,
      ...(contextLength != null ? { context_length: contextLength } : {}),
      learning_rate: lr,
      n_epochs: epochs,
      warmup_ratio: typeof hp.warmup_ratio === "number" ? hp.warmup_ratio : 0.05,
    },
    id: crypto.randomUUID(),
    learningRateFull: r.learningRateFull,
    learningRateLora: r.learningRateLora,
    loraParams: {
      lora_alpha: Number(tt.lora_alpha) || rank * 2,
      lora_dropout: Number(tt.lora_dropout) || 0,
      lora_r: rank,
    },
    maxBatchSize: maxBs,
    minBatchSize: minBs,
    model: catalogModel?.id ?? r.model,
    params: catalogModel?.params ?? r.params,
    recommendedValues: {
      batch_size: batch,
      learning_rate: lr,
      n_epochs: epochs,
      ...(useLora ? { lora_r: rank } : {}),
    },
    supportsFull,
    supportsLora,
    tier: r.tier,
    useLora,
  };
}

export function draftFromCatalogModel(tier: string, m: ModelEntry): ModelDraft {
  const supportsLora = catalogTrainingEnabled(m, "lora");
  const supportsFull = catalogTrainingEnabled(m, "full");
  const td = TIER_DEFAULTS[tier];
  const loraR = td?.lora_r ?? 16;
  return {
    ...ungraded(),
    contextLengthSft: m.contextLengthSft,
    displayName: m.display,
    hyperparams: {
      // Never fabricate batch/epochs client-side — batch 1 × 3 epochs yields
      // 59k-step runs. Null until the model-defaults API derives them.
      batch_size: null,
      learning_rate: td?.learning_rate ?? 1e-4,
      n_epochs: null,
      warmup_ratio: 0.05,
    },
    id: crypto.randomUUID(),
    learningRateFull: td?.learning_rate ? td.learning_rate / 10 : 1e-5,
    learningRateLora: td?.learning_rate ?? 1e-4,
    loraParams: { lora_alpha: td?.lora_alpha ?? loraR * 2, lora_dropout: 0, lora_r: loraR },
    maxBatchSize: m.maxBatchSize ?? 64,
    minBatchSize: m.minBatchSize ?? 1,
    model: m.id,
    params: m.params,
    supportsFull,
    supportsLora,
    tier,
    // Default to LoRA whenever the catalog allows it; Full only when LoRA is off.
    useLora: supportsLora,
  };
}

/** Re-seeds a draft onto a different catalog model, keeping user overrides. */
export function draftWithModel(draft: ModelDraft, tier: string, m: ModelEntry): ModelDraft {
  const td = TIER_DEFAULTS[tier];
  const supportsLora = catalogTrainingEnabled(m, "lora");
  const supportsFull = catalogTrainingEnabled(m, "full");
  const useLora = supportsLora && (!supportsFull || draft.useLora);
  const minBs = m.minBatchSize ?? 1;
  const maxBs = m.maxBatchSize ?? 64;
  return {
    ...draft,
    ...ungraded(),
    contextLengthSft: m.contextLengthSft,
    displayName: m.display,
    hyperparams: {
      ...draft.hyperparams,
      // Clamp the dataset-sized batch into the new model's bounds; resetting to
      // the model minimum gives batch 1 and absurd step counts.
      batch_size:
        draft.hyperparams.batch_size == null
          ? null
          : clampBatchSize(draft.hyperparams.batch_size, minBs, maxBs),
      learning_rate: useLora
        ? (td?.learning_rate ?? draft.learningRateLora)
        : td?.learning_rate
          ? td.learning_rate / 10
          : draft.learningRateFull,
    },
    learningRateFull: td?.learning_rate ? td.learning_rate / 10 : draft.learningRateFull,
    learningRateLora: td?.learning_rate ?? draft.learningRateLora,
    loraParams: {
      lora_alpha: td?.lora_alpha ?? draft.loraParams.lora_alpha,
      lora_dropout: draft.loraParams.lora_dropout,
      lora_r: td?.lora_r ?? draft.loraParams.lora_r,
    },
    maxBatchSize: maxBs,
    minBatchSize: minBs,
    model: m.id,
    params: m.params,
    recommendedValues: undefined,
    supportsFull,
    supportsLora,
    tier,
    useLora,
  };
}

/** True while the draft still carries the recommender's batch and epochs. */
export function isUntuned(draft: ModelDraft): boolean {
  const rv = draft.recommendedValues;
  return (
    (rv?.batch_size == null || draft.hyperparams.batch_size === rv.batch_size) &&
    (rv?.n_epochs == null || draft.hyperparams.n_epochs === rv.n_epochs)
  );
}

export function buildHyperparameters(draft: ModelDraft): Record<string, unknown> {
  // Omit underived batch/epochs — the backend policy fills them from dataset size.
  const hp: Record<string, unknown> = {
    ...(draft.hyperparams.batch_size != null ? { batch_size: draft.hyperparams.batch_size } : {}),
    learning_rate: draft.hyperparams.learning_rate,
    ...(draft.hyperparams.n_epochs != null ? { n_epochs: draft.hyperparams.n_epochs } : {}),
    warmup_ratio: draft.hyperparams.warmup_ratio,
  };
  if (draft.hyperparams.context_length != null) {
    hp.context_length = draft.hyperparams.context_length;
  }
  // Catalog may disable Full (gemma-4 26B/31B, gpt-oss); never submit Full then.
  const useLora = !draft.supportsFull || (draft.supportsLora && draft.useLora);
  hp.training_type = useLora
    ? {
        lora_alpha: draft.loraParams.lora_alpha,
        lora_dropout: draft.loraParams.lora_dropout,
        lora_r: draft.loraParams.lora_r,
        lora_trainable_modules: "all-linear",
        type: "Lora",
      }
    : { type: "Full" };
  return hp;
}

/** Re-resolve LoRA/Full flags from the live catalog (fail-closed on Full). */
export function applyCatalogTrainingFlags(
  draft: ModelDraft,
  catalogModel: ModelEntry | undefined
): ModelDraft {
  const supportsLora = catalogTrainingEnabled(catalogModel, "lora");
  const supportsFull = catalogTrainingEnabled(catalogModel, "full");
  const useLora = supportsLora && (!supportsFull || draft.useLora);
  if (
    draft.supportsLora === supportsLora &&
    draft.supportsFull === supportsFull &&
    draft.useLora === useLora
  ) {
    return draft;
  }
  return {
    ...draft,
    hyperparams: {
      ...draft.hyperparams,
      learning_rate:
        useLora === draft.useLora
          ? draft.hyperparams.learning_rate
          : useLora
            ? draft.learningRateLora
            : draft.learningRateFull,
    },
    supportsFull,
    supportsLora,
    useLora,
  };
}

export function formatLr(lr: number): string {
  return lr !== 0 && Math.abs(lr) < 0.01 ? lr.toExponential() : String(lr);
}

/** Console-safe job/deploy failure copy — never surface stack traces or GPU SKUs. */
export function scrubInfraLeak(msg: string): string {
  return msg
    .replace(/\s*\((?:L4|L40S?|A10G?|A100|H100|H200|B200|T4)[^)]*\)/gi, "")
    .replace(/\b(?:L4|L40S?|A10G?|A100|H100|H200|B200|T4)\b/gi, "GPU")
    .trim();
}

export function userFacingJobError(msg: string | null | undefined): string {
  const raw = (msg ?? "").trim();
  if (!raw) return "Training failed";
  if (
    /RuntimeError|Traceback|Exception\b|train\.py|exited with code|File\s+["'/]|\/root\/|CUDA|OutOfMemory|\bOOM\b/i.test(
      raw
    )
  ) {
    if (/deploy|pre-?warm|register|download|inference/i.test(raw)) return "Deployment failed";
    return "Training failed";
  }
  return scrubInfraLeak(raw);
}
