function readFlag(key: string, defaultValue = false): boolean {
  const raw = import.meta.env[key];
  if (raw === undefined || raw === "") return defaultValue;
  return raw === "true" || raw === "1";
}

export const featureFlags = {
  datasets: readFlag("VITE_FEATURE_DATASETS"),
  evaluations: readFlag("VITE_FEATURE_EVALUATIONS"),
  finetuning: readFlag("VITE_FEATURE_FINETUNING"),
  inference: readFlag("VITE_FEATURE_INFERENCE"),
  mcp: readFlag("VITE_FEATURE_MCP"),
} as const;
