/**
 * Display labels are sentence case. Nothing uppercases them in CSS any more, so
 * anything derived from a wire value has to be cased here at runtime.
 */

/**
 * Matched case-insensitively on whole words; the entry itself is the rendered
 * form. A word here wins even in first position, so "ID" stays "ID", not "Id".
 */
const PRESERVED = [
  "AI",
  "API",
  "ARN",
  "AUC",
  "CLI",
  "CORS",
  "CPU",
  "CSV",
  "DB",
  "DNS",
  "DPO",
  "ETA",
  "F1",
  "FT",
  "GPT",
  "GPU",
  "GRPO",
  "HTTP",
  "HTTPS",
  "ID",
  "IDs",
  "IO",
  "IP",
  "JSON",
  "JSONL",
  "JWT",
  "KL",
  "KV",
  "LLM",
  "MCP",
  "ML",
  "OK",
  "OS",
  "OTEL",
  "P50",
  "P95",
  "P99",
  "PII",
  "PPO",
  "PR",
  "PRs",
  "QPS",
  "RAG",
  "RAM",
  "ROC",
  "RPS",
  "SDK",
  "SFT",
  "SLA",
  "SQL",
  "SSH",
  "TLS",
  "TOML",
  "TSV",
  "TTL",
  "UI",
  "URI",
  "URL",
  "UUID",
  "UX",
  "VM",
  "YAML",
  "Anthropic",
  "AWS",
  "Azure",
  "Bedrock",
  "Claude",
  "Discord",
  "Docker",
  "GCP",
  "Gemini",
  "GitHub",
  "GitLab",
  "Kubernetes",
  "Llama",
  "LoRA",
  "Mistral",
  "OpenAI",
  "Overmind",
  "Postgres",
  "QLoRA",
  "Redis",
  "Slack",
  "Vertex",
] as const;

const PRESERVED_BY_LOWER = new Map(PRESERVED.map((word) => [word.toLowerCase(), word]));

const capitalize = (word: string): string =>
  word.length ? word[0].toUpperCase() + word.slice(1) : word;

/**
 * Sentence-case a display string: first word capitalised, the rest lowercased,
 * with `PRESERVED` words rendered in their canonical form wherever they land.
 *
 * "EXPECTED OUTPUT" → "Expected output" · "trace id" → "Trace ID"
 * "json payload"    → "JSON payload"    · "ok"       → "OK"
 */
export function sentenceCase(raw: string): string {
  const words = raw.trim().split(/\s+/).filter(Boolean);
  if (!words.length) return "";
  return words
    .map((word, i) => {
      // Split off surrounding punctuation so "(json)" still matches "json".
      const [, lead = "", core = "", trail = ""] = word.match(/^(\W*)(.*?)(\W*)$/s) ?? [];
      const preserved = PRESERVED_BY_LOWER.get(core.toLowerCase());
      if (preserved) return `${lead}${preserved}${trail}`;
      const cased = i === 0 ? capitalize(core.toLowerCase()) : core.toLowerCase();
      return `${lead}${cased}${trail}`;
    })
    .join(" ");
}

/**
 * Turn a wire identifier into a display label: `snake_case`, `kebab-case`,
 * `camelCase` and slash/space-separated names all split into words, then
 * sentence-cased.
 *
 * "expected_output" → "Expected output" · "traceId" → "Trace ID"
 * "ready_with_warnings" → "Ready with warnings" · "pass/fail" → "Pass fail"
 */
export function humanizeKey(key: string): string {
  return sentenceCase(
    key
      .replace(/[_\-/\s]+/g, " ")
      .replace(/([a-z0-9])([A-Z])/g, "$1 $2")
      .replace(/([A-Z]+)([A-Z][a-z])/g, "$1 $2")
  );
}
