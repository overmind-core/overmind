export type WinnerCandidate = {
  id: string;
  candidateIndex: number;
  targetModel: string;
  isBaseline: boolean;
  status: string;
  score: number;
};

export type WinnerIteration = { id: string; order: number; candidates: WinnerCandidate[] };

type WinnerOptions = {
  comparison: boolean;
  /** Backend `state.model_comparison.selected_winner`, once the run reports. */
  winnerModel: string | null;
  best: number | null;
};

function isScored(candidate: WinnerCandidate): boolean {
  return (
    !candidate.isBaseline &&
    candidate.status === "evaluated" &&
    typeof candidate.score === "number" &&
    Number.isFinite(candidate.score)
  );
}

/** Tie-breaks mirror the backend: comparison takes the earliest candidate
 * (`max()`), optimize/hybrid the latest (`-score, -iteration__order`). */
export function deriveWinner(
  iterations: WinnerIteration[],
  { comparison, winnerModel, best }: WinnerOptions
): WinnerCandidate | null {
  const orderOf = new Map<string, number>();
  for (const iteration of iterations) {
    for (const candidate of iteration.candidates ?? []) orderOf.set(candidate.id, iteration.order);
  }

  const evaluated = iterations.flatMap((it) => it.candidates ?? []).filter(isScored);
  if (winnerModel != null) {
    return evaluated.find((c) => c.targetModel === winnerModel) ?? null;
  }
  if (best == null) return null;

  const ties = evaluated.filter((c) => c.score === best);
  ties.sort(
    comparison
      ? (a, b) =>
          (orderOf.get(a.id) ?? -1) - (orderOf.get(b.id) ?? -1) ||
          a.candidateIndex - b.candidateIndex
      : (a, b) =>
          (orderOf.get(b.id) ?? -1) - (orderOf.get(a.id) ?? -1) ||
          a.candidateIndex - b.candidateIndex
  );
  return ties[0] ?? null;
}
