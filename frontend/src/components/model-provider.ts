// Keep free of React and icon imports: the @lobehub subpaths only resolve
// through Vite, so importing one here breaks this module under vitest.

export type ProviderId =
  | "ai2"
  | "ai21"
  | "amazon"
  | "anthropic"
  | "baichuan"
  | "baidu"
  | "bytedance"
  | "cohere"
  | "deepseek"
  | "google"
  | "inception"
  | "inflection"
  | "kimi"
  | "liquid"
  | "meta"
  | "microsoft"
  | "minimax"
  | "mistral"
  | "nous"
  | "nvidia"
  | "openai"
  | "openrouter"
  | "perplexity"
  | "qwen"
  | "stepfun"
  | "tencent"
  | "upstage"
  | "xai"
  | "xiaomi"
  | "yi"
  | "zhipu"
  | "unknown"
  | "cursor"
  | "claude";

export type ModelProviderInfo = {
  id: ProviderId;
  providerLabel: string;
  modelLabel: string;
  providerSlug?: string;
};

type ProviderBundle = Pick<ModelProviderInfo, "id" | "providerLabel" | "providerSlug">;

/** Segments whose vendor-authored casing can't be derived from a rule. */
const MODEL_SEGMENT: Record<string, string> = {
  ai: "AI",
  gpt: "GPT",
  llm: "LLM",
  moe: "MoE",
  o1: "o1",
  o3: "o3",
  o4: "o4",
  vl: "VL",
};

/** A model id is product notation, not prose: "gpt-4o" is "GPT 4o", not "GPT 4O". */
const friendlyModelSegment = (part: string): string => {
  const known = MODEL_SEGMENT[part.toLowerCase()];
  if (known) return known;
  if (/^\d/.test(part)) return part.replace(/([bmk])$/i, (unit) => unit.toUpperCase());
  return part.charAt(0).toUpperCase() + part.slice(1).toLowerCase();
};

const friendlyModelName = (model: string): string =>
  model
    .split(/[\s\-_/]+/)
    .filter(Boolean)
    .map(friendlyModelSegment)
    .join(" ");

/** Strips punctuation so `x-ai`, `meta-llama` and `~openai` collapse cleanly. */
const normalizeProviderKey = (providerSegment: string): string =>
  providerSegment
    .trim()
    .toLowerCase()
    .replace(/^~+/, "")
    .replace(/[\s_.-]+/g, "");

const PROVIDER_BY_SLUG: Record<Exclude<ProviderId, "unknown">, ProviderBundle> = {
  ai2: { id: "ai2", providerLabel: "AI2", providerSlug: "ai2" },
  ai21: { id: "ai21", providerLabel: "AI21", providerSlug: "ai21" },
  amazon: { id: "amazon", providerLabel: "Amazon", providerSlug: "amazon" },
  anthropic: { id: "anthropic", providerLabel: "Anthropic", providerSlug: "anthropic" },
  baichuan: { id: "baichuan", providerLabel: "Baichuan", providerSlug: "baichuan" },
  baidu: { id: "baidu", providerLabel: "Baidu", providerSlug: "baidu" },
  bytedance: { id: "bytedance", providerLabel: "ByteDance", providerSlug: "bytedance" },
  claude: { id: "claude", providerLabel: "Claude", providerSlug: "claude" },
  cohere: { id: "cohere", providerLabel: "Cohere", providerSlug: "cohere" },
  cursor: { id: "cursor", providerLabel: "Cursor", providerSlug: "cursor" },
  deepseek: { id: "deepseek", providerLabel: "DeepSeek", providerSlug: "deepseek" },
  google: { id: "google", providerLabel: "Gemini", providerSlug: "google" },
  inception: { id: "inception", providerLabel: "Inception", providerSlug: "inception" },
  inflection: { id: "inflection", providerLabel: "Inflection", providerSlug: "inflection" },
  kimi: { id: "kimi", providerLabel: "Kimi", providerSlug: "moonshotai" },
  liquid: { id: "liquid", providerLabel: "Liquid", providerSlug: "liquid" },
  meta: { id: "meta", providerLabel: "Meta", providerSlug: "meta" },
  microsoft: { id: "microsoft", providerLabel: "Microsoft", providerSlug: "microsoft" },
  minimax: { id: "minimax", providerLabel: "MiniMax", providerSlug: "minimax" },
  mistral: { id: "mistral", providerLabel: "Mistral", providerSlug: "mistral" },
  nous: { id: "nous", providerLabel: "Nous", providerSlug: "nousresearch" },
  nvidia: { id: "nvidia", providerLabel: "NVIDIA", providerSlug: "nvidia" },
  openai: { id: "openai", providerLabel: "OpenAI", providerSlug: "openai" },
  openrouter: { id: "openrouter", providerLabel: "OpenRouter", providerSlug: "openrouter" },
  perplexity: { id: "perplexity", providerLabel: "Perplexity", providerSlug: "perplexity" },
  qwen: { id: "qwen", providerLabel: "Qwen", providerSlug: "qwen" },
  stepfun: { id: "stepfun", providerLabel: "StepFun", providerSlug: "stepfun" },
  tencent: { id: "tencent", providerLabel: "Tencent", providerSlug: "tencent" },
  upstage: { id: "upstage", providerLabel: "Upstage", providerSlug: "upstage" },
  xai: { id: "xai", providerLabel: "xAI", providerSlug: "xai" },
  xiaomi: { id: "xiaomi", providerLabel: "Xiaomi", providerSlug: "xiaomi" },
  yi: { id: "yi", providerLabel: "Yi", providerSlug: "yi" },
  zhipu: { id: "zhipu", providerLabel: "Zhipu", providerSlug: "zhipu" },
};

/** Normalised provider keys whose simpleicons slug differs — each value
    verified to return an icon from cdn.simpleicons.org (the key 404s). */
const SIMPLEICONS_SLUG_FIXES: Record<string, string> = {
  fdtnai: "cisco",
};

/** Vendor labels for prefixes with no lobehub icon and no matching provider
    id, so the fallback badge shows the vendor name rather than the raw slug. */
const UNKNOWN_PROVIDER_LABELS: Record<string, string> = {
  fdtnai: "Cisco",
};

const PROVIDER_ALIASES: Record<string, Exclude<ProviderId, "unknown">> = {
  "01ai": "yi",
  alibaba: "qwen",
  allenai: "ai2",
  aws: "amazon",
  bedrock: "amazon",
  bytedanceseed: "bytedance",
  chatglm: "zhipu",
  deepseekai: "deepseek",
  doubao: "bytedance",
  gemini: "google",
  glm: "zhipu",
  googleai: "google",
  hunyuan: "tencent",
  liquidai: "liquid",
  metallama: "meta",
  mistralai: "mistral",
  moonshot: "kimi",
  moonshotai: "kimi",
  nousresearch: "nous",
  palm: "google",
  seed: "bytedance",
  zai: "zhipu",
};

const resolveExplicitProvider = (providerSegment: string): ProviderBundle | null => {
  const key = normalizeProviderKey(providerSegment);
  if (!key) return null;
  const providerId = (key in PROVIDER_BY_SLUG ? key : PROVIDER_ALIASES[key]) as
    | Exclude<ProviderId, "unknown">
    | undefined;
  if (providerId) return PROVIDER_BY_SLUG[providerId];
  if (key.startsWith("meta")) return PROVIDER_BY_SLUG.meta;
  return null;
};

function inferProviderFromModelId(modelId: string): ProviderBundle | null {
  const normalized = modelId.trim().toLowerCase();

  if (normalized.startsWith("gpt-") || /^o\d/.test(normalized)) {
    return PROVIDER_BY_SLUG.openai;
  }

  if (normalized.startsWith("claude")) {
    return PROVIDER_BY_SLUG.anthropic;
  }

  if (
    normalized.startsWith("gemini-") ||
    normalized.startsWith("gemma") ||
    normalized.startsWith("palm-")
  ) {
    return PROVIDER_BY_SLUG.google;
  }

  if (
    normalized.startsWith("llama-") ||
    normalized.includes("llama") ||
    normalized.startsWith("muse")
  ) {
    return PROVIDER_BY_SLUG.meta;
  }

  if (
    normalized.startsWith("mistral-") ||
    normalized.startsWith("mixtral-") ||
    normalized.startsWith("ministral")
  ) {
    return PROVIDER_BY_SLUG.mistral;
  }

  if (normalized.startsWith("qwen") || normalized.startsWith("qwq")) {
    return PROVIDER_BY_SLUG.qwen;
  }

  if (normalized.startsWith("grok-")) {
    return PROVIDER_BY_SLUG.xai;
  }

  if (normalized.startsWith("command-")) {
    return PROVIDER_BY_SLUG.cohere;
  }

  if (normalized.startsWith("deepseek") || normalized.includes("deepseek")) {
    return PROVIDER_BY_SLUG.deepseek;
  }

  if (normalized.startsWith("nemotron") || normalized.includes("nemotron")) {
    return PROVIDER_BY_SLUG.nvidia;
  }

  if (/^phi[-_]?\d/.test(normalized) || normalized.startsWith("phi-")) {
    return PROVIDER_BY_SLUG.microsoft;
  }

  if (normalized.startsWith("olmo") || normalized.includes("olmo")) {
    return PROVIDER_BY_SLUG.ai2;
  }

  if (normalized.startsWith("kimi")) {
    return PROVIDER_BY_SLUG.kimi;
  }

  if (normalized.startsWith("glm") || normalized.startsWith("chatglm")) {
    return PROVIDER_BY_SLUG.zhipu;
  }

  if (normalized.startsWith("minimax")) {
    return PROVIDER_BY_SLUG.minimax;
  }

  if (normalized.startsWith("sonar")) {
    return PROVIDER_BY_SLUG.perplexity;
  }

  if (normalized.startsWith("nova-") || normalized.startsWith("nova")) {
    return PROVIDER_BY_SLUG.amazon;
  }

  if (normalized.startsWith("hermes")) {
    return PROVIDER_BY_SLUG.nous;
  }

  if (normalized.startsWith("hunyuan") || normalized.startsWith("hy3")) {
    return PROVIDER_BY_SLUG.tencent;
  }

  if (normalized.startsWith("step-") || /^step-?\d/.test(normalized)) {
    return PROVIDER_BY_SLUG.stepfun;
  }

  if (normalized.startsWith("mimo")) {
    return PROVIDER_BY_SLUG.xiaomi;
  }

  if (normalized.startsWith("mercury")) {
    return PROVIDER_BY_SLUG.inception;
  }

  if (normalized.startsWith("solar")) {
    return PROVIDER_BY_SLUG.upstage;
  }

  if (normalized.startsWith("jamba")) {
    return PROVIDER_BY_SLUG.ai21;
  }

  if (normalized.startsWith("ernie")) {
    return PROVIDER_BY_SLUG.baidu;
  }

  if (normalized.startsWith("yi-") || /^yi[-_]?\d/.test(normalized)) {
    return PROVIDER_BY_SLUG.yi;
  }

  if (normalized.startsWith("baichuan")) {
    return PROVIDER_BY_SLUG.baichuan;
  }

  if (
    normalized.startsWith("seed-") ||
    normalized.startsWith("ui-tars") ||
    normalized.startsWith("doubao")
  ) {
    return PROVIDER_BY_SLUG.bytedance;
  }

  if (normalized.startsWith("inflection")) {
    return PROVIDER_BY_SLUG.inflection;
  }

  return null;
}

/** For provider-level UI such as filter chips, where there is no model id yet. */
export const getProviderInfoById = (id: ProviderId): ProviderBundle =>
  id === "unknown"
    ? { id: "unknown", providerLabel: "Unknown", providerSlug: undefined }
    : PROVIDER_BY_SLUG[id];

export const getModelProviderInfo = (model: string): ModelProviderInfo => {
  const trimmed = model.trim();
  // "provider/model" (router style) and "provider:model" (litellm style) both
  // carry an explicit provider segment.
  const separatorIdx = [trimmed.indexOf("/"), trimmed.indexOf(":")]
    .filter((idx) => idx !== -1)
    .reduce((min, idx) => Math.min(min, idx), Number.POSITIVE_INFINITY);
  const slashIdx = Number.isFinite(separatorIdx) ? separatorIdx : -1;

  if (slashIdx !== -1) {
    const providerSegment = trimmed.slice(0, slashIdx).trim();
    const modelSegment = trimmed.slice(slashIdx + 1).trim();
    const modelLabel = modelSegment ? friendlyModelName(modelSegment) : friendlyModelName(trimmed);

    const explicit = resolveExplicitProvider(providerSegment);
    if (explicit) {
      return { ...explicit, modelLabel };
    }

    const inferred = inferProviderFromModelId(modelSegment);
    if (inferred) {
      return { ...inferred, modelLabel };
    }

    const providerKey = normalizeProviderKey(providerSegment);
    return {
      id: "unknown",
      modelLabel,
      providerLabel: UNKNOWN_PROVIDER_LABELS[providerKey] ?? friendlyModelName(providerSegment),
      providerSlug: SIMPLEICONS_SLUG_FIXES[providerKey] ?? providerKey,
    };
  }

  const inferred = inferProviderFromModelId(trimmed);
  if (inferred) {
    return { ...inferred, modelLabel: friendlyModelName(trimmed) };
  }

  return { id: "unknown", modelLabel: friendlyModelName(trimmed), providerLabel: "Model" };
};
