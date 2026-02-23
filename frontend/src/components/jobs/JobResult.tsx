import type { JobOut } from "@/api";
import { AgentDiscoveryResult } from "./AgentDiscoveryResult";
import { BacktestingResult } from "./BacktestingResult";
import { PromptTuningResult } from "./PromptTuningResult";
import { RawResultAccordion } from "./RawResultAccordion";

interface JobResultProps {
  job: JobOut;
}

export function JobResult({ job }: JobResultProps) {
  const result = job.result as Record<string, unknown> | null;
  if (!result || Object.keys(result).length === 0) return null;

  switch (job.jobType) {
    case "prompt_tuning":
      return <PromptTuningResult promptSlug={job.promptSlug} result={result} />;
    case "model_backtesting":
      return <BacktestingResult promptSlug={job.promptSlug} result={result} />;
    case "agent_discovery":
      return <AgentDiscoveryResult result={result} />;
    default:
      return <RawResultAccordion result={result} />;
  }
}
