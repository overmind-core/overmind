export interface ScoreLike {
  id?: string;
  name?: string | null;
  value?: number | null;
  passed?: boolean | null;
  reasoning?: string | null;
  scope?: string | null;
  subScores?: unknown;
}

function object(value: unknown): Record<string, unknown> | undefined {
  return value != null && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : undefined;
}

export function DecisionEvidence({ subScores }: { subScores: unknown }) {
  if (!Array.isArray(subScores)) return null;
  const decision = subScores.map((entry) => object(object(entry)?._decision)).find(Boolean);
  if (!decision?.source) return null;
  const label =
    decision.source === "mixed"
      ? "Jev + generative review"
      : decision.source === "generative_fallback"
        ? "Generative fallback"
        : decision.source === "generative"
          ? "Generative review"
          : "Jev decision";
  const evidence = Array.isArray(decision.batches)
    ? decision.batches
    : [
        {
          accepted_questions: decision.accepted_questions,
          answers: decision.answers,
          fallback_questions: decision.fallback_questions,
          resolved_answers: decision.resolved_answers,
        },
      ];
  return (
    <details className="mt-2 text-xs">
      <summary className="cursor-pointer text-muted-foreground">{label}</summary>
      {typeof decision.served_model === "string" && (
        <p className="mt-1 font-mono">{decision.served_model}</p>
      )}
      {typeof decision.fallback_reason === "string" && (
        <p className="mt-1">{decision.fallback_reason.replaceAll("_", " ")}</p>
      )}
      {(decision.answers != null || Array.isArray(decision.batches)) && (
        <>
          <p className="mt-1 text-muted-foreground">
            Confidence describes the answer distribution, not measured accuracy.
          </p>
          <pre className="mt-1 max-h-64 overflow-auto whitespace-pre-wrap break-words font-mono">
            {JSON.stringify(evidence, null, 2)}
          </pre>
        </>
      )}
    </details>
  );
}

export type ScoreState = "passed" | "failed" | "ungraded" | "scored";

// A null value is an abstention, not a 0% fail.
export function scoreState(score: ScoreLike): ScoreState {
  if (score.value == null) return "ungraded";
  if (score.passed === true) return "passed";
  if (score.passed === false) return "failed";
  return "scored";
}
