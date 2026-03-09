/** Human-readable labels for job types. Used in breadcrumbs, job lists, and job detail. */
export const JOB_TYPE_LABELS: Record<string, string> = {
  agent_discovery: "Agent Discovery",
  judge_scoring: "LLM Judge Scoring",
  model_backtesting: "Model Backtesting",
  prompt_tuning: "Prompt Tuning",
  scoring: "LLM Judge Scoring", // legacy alias for judge_scoring (create-job-dialog uses "scoring")
  template_extraction: "Template Extraction",
};
