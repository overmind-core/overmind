import { describe, expect, it } from "vitest";

import type { WinnerCandidate } from "./experiment-winner";
import { deriveWinner } from "./experiment-winner";

function candidate(partial: Partial<WinnerCandidate> = {}): WinnerCandidate {
  return {
    candidateIndex: 0,
    id: "c-1",
    isBaseline: false,
    score: 90,
    status: "evaluated",
    targetModel: "openai/gpt-5",
    ...partial,
  };
}

function iteration(order: number, candidates: WinnerCandidate[]) {
  return { candidates, id: `it-${order}`, order };
}

const tie = [
  iteration(1, [candidate({ id: "early", score: 90, targetModel: "model/a" })]),
  iteration(2, [candidate({ id: "late", score: 90, targetModel: "model/b" })]),
];

describe("deriveWinner", () => {
  it("crowns the backend's recorded comparison winner, even on a tie", () => {
    expect(deriveWinner(tie, { best: 90, comparison: true, winnerModel: "model/b" })?.id).toBe(
      "late"
    );
  });

  it("while running, a comparison tie crowns the earliest model (backend max())", () => {
    expect(deriveWinner(tie, { best: 90, comparison: true, winnerModel: null })?.id).toBe("early");
  });

  it("an optimize tie crowns the latest iteration (backend -iteration__order)", () => {
    expect(deriveWinner(tie, { best: 90, comparison: false, winnerModel: null })?.id).toBe("late");
  });

  it("returns null before anything is scored", () => {
    expect(deriveWinner(tie, { best: null, comparison: true, winnerModel: null })).toBeNull();
  });

  it("never crowns the baseline", () => {
    const baselineTie = [
      iteration(0, [candidate({ id: "base", isBaseline: true, score: 95 })]),
      iteration(1, [candidate({ id: "model", score: 95 })]),
    ];
    expect(deriveWinner(baselineTie, { best: 95, comparison: true, winnerModel: null })?.id).toBe(
      "model"
    );
  });
});
