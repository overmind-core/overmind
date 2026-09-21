import { renderToStaticMarkup } from "react-dom/server";

import { describe, expect, it } from "vitest";

import { DecisionEvidence, scoreState } from "./score-reasoning";

describe("scoreState", () => {
  it("treats a null value as ungraded rather than a 0% fail", () => {
    expect(scoreState({ passed: false, value: null })).toBe("ungraded");
    expect(scoreState({ passed: false, value: 0 })).toBe("failed");
    expect(scoreState({ passed: true, value: 1 })).toBe("passed");
    expect(scoreState({ passed: null, value: 0.6 })).toBe("scored");
  });
});

describe("decision evidence", () => {
  it("does not add a decision label to ordinary scores", () => {
    expect(
      renderToStaticMarkup(<DecisionEvidence subScores={[{ id: "check", verdict: true }]} />)
    ).toBe("");
  });
  it("shows actual answers and distinguishes fallback from confidence", () => {
    const html = renderToStaticMarkup(
      <DecisionEvidence
        subScores={[
          {
            _decision: {
              answers: { answer: { choice: "pass", confidence: 0.4 } },
              fallback_reason: "uncertain",
              served_model: "typesafe/jev-test",
              source: "generative_fallback",
            },
          },
        ]}
      />
    );
    expect(html).toContain("Generative fallback");
    expect(html).toContain("not measured accuracy");
    expect(html).toContain("typesafe/jev-test");
    expect(html).toContain("0.4");
  });
  it("shows selective resolution without presenting generated choices as Jev confidence", () => {
    const html = renderToStaticMarkup(
      <DecisionEvidence
        subScores={[
          {
            _decision: {
              accepted_questions: ["a"],
              answers: { a: { choice: "pass", confidence: 0.99 } },
              fallback_questions: ["b"],
              resolved_answers: { b: { choice: "fail", reasoning: "Source disagrees" } },
              source: "mixed",
            },
          },
        ]}
      />
    );
    expect(html).toContain("Jev + generative review");
    expect(html).toContain("Source disagrees");
    expect(html).toContain("accepted_questions");
  });
  it("shows the batch evidence for a cascade", () => {
    const html = renderToStaticMarkup(
      <DecisionEvidence
        subScores={[
          {
            _decision: {
              batches: [
                { served_model: "typesafe/jev-test" },
                { fallback_reason: "provider_timeout" },
              ],
              source: "mixed",
            },
          },
        ]}
      />
    );
    expect(html).toContain("typesafe/jev-test");
    expect(html).toContain("provider_timeout");
  });
});
