// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import {
  EvidencePanel,
  EvidenceTable,
  ExcludedSummary,
  fieldSize,
  MatchScore,
  SkillChart,
  skillBars,
  sortEvidence,
} from "@/components/finetuning/train/evidence";
import type { FinetuningEvidence, FinetuningExcludedModel, FinetuningSkillScore } from "@/openapi";

afterEach(cleanup);

function evidence(overrides: Partial<FinetuningEvidence> = {}): FinetuningEvidence {
  return {
    benchmark: "IFBench",
    cohortN: 450,
    percentile: 88.2,
    provenance: "measured",
    skill: "Instruction Following",
    source: "arXiv",
    url: "https://arxiv.org/abs/2507.02833",
    ...overrides,
  };
}

function skillScore(overrides: Partial<FinetuningSkillScore> = {}): FinetuningSkillScore {
  return {
    fieldN: 30,
    percentileGlobal: 74.4,
    percentileInField: 98.3,
    rankInField: 1,
    skill: "Instruction Following",
    weight: 0.5,
    ...overrides,
  };
}

const WEIGHTS = { Faithfulness: 0.3, "Instruction Following": 0.5, Reasoning: 0.2 };

// Deliberately not in weight order: the chart is what puts the deciding skill first.
const SKILL_SCORES: FinetuningSkillScore[] = [
  skillScore({
    percentileGlobal: 54,
    percentileInField: 88.3,
    rankInField: 4,
    skill: "Faithfulness",
    weight: 0.3,
  }),
  skillScore({
    percentileGlobal: 64,
    percentileInField: 95,
    rankInField: 2,
    skill: "Reasoning",
    weight: 0.2,
  }),
  skillScore(),
];

const GRADED: FinetuningEvidence[] = [
  evidence(),
  evidence({ benchmark: "CritPt", cohortN: 439, percentile: 71, skill: "Faithfulness" }),
  evidence({
    benchmark: "MMLU-Pro",
    cohortN: 346,
    percentile: 64.4,
    skill: "Reasoning",
    source: "GitHub",
    url: "https://github.com/TIGER-AI-Lab/MMLU-Pro",
  }),
];

function rowLabels(): string[] {
  return screen.getAllByRole("row").slice(1).map(cellText);
}

function cellText(row: HTMLElement): string {
  return row.querySelector("td")?.textContent ?? "";
}

/** Every bar's rendered fill width, in source order. */
function barWidths(): string[] {
  return screen.getAllByRole("img").map((track) => {
    const fill = track.firstElementChild as HTMLElement | null;
    return fill?.style.width ?? "";
  });
}

/** Where each bar puts its reference tick, in source order. */
function medianTicks(): string[] {
  return screen
    .getAllByRole("img")
    .flatMap((track) =>
      [...track.children].slice(1).map((tick) => (tick as HTMLElement).style.left)
    );
}

describe("MatchScore", () => {
  it("carries the population the number stands in", () => {
    render(<MatchScore match={98} matchPool={30} matchRank={1} />);

    expect(screen.getByText("Match 98.00/100")).toBeTruthy();
    expect(
      screen.getByLabelText("Match 98.00 out of 100, 1st of 30 models you can train")
    ).toBeTruthy();
  });

  it("falls back to the bare match when the pool is unknown", () => {
    render(<MatchScore match={72} matchPool={0} matchRank={null} />);

    expect(screen.getByLabelText("Match 72.00 out of 100")).toBeTruthy();
  });

  it("marks an ungraded model rather than inventing a match", () => {
    render(<MatchScore match={null} matchPool={30} matchRank={null} />);

    expect(screen.getByLabelText("Not graded")).toBeTruthy();
  });
});

describe("skillBars", () => {
  it("orders one bar per weighted skill, heaviest first", () => {
    expect(skillBars(SKILL_SCORES, WEIGHTS)).toEqual([
      {
        fieldN: 30,
        percentile: 98.3,
        rank: 1,
        skill: "Instruction Following",
        weight: 0.5,
      },
      { fieldN: 30, percentile: 88.3, rank: 4, skill: "Faithfulness", weight: 0.3 },
      { fieldN: 30, percentile: 95, rank: 2, skill: "Reasoning", weight: 0.2 },
    ]);
  });

  it("leaves out a skill this task does not weight", () => {
    const scores = [...SKILL_SCORES, skillScore({ skill: "Coding", weight: 0 })];

    expect(skillBars(scores, WEIGHTS).map((bar) => bar.skill)).not.toContain("Coding");
  });

  it("keeps a weighted skill the model has no benchmark for", () => {
    const bars = skillBars([skillScore()], WEIGHTS);

    expect(bars.map((bar) => [bar.skill, bar.percentile, bar.rank])).toEqual([
      ["Instruction Following", 98.3, 1],
      ["Faithfulness", null, null],
      ["Reasoning", null, null],
    ]);
  });
});

describe("fieldSize", () => {
  it("names the field when every skill was read against the same one", () => {
    expect(fieldSize(skillBars(SKILL_SCORES, WEIGHTS))).toBe(30);
  });

  it("names no field when the skills were scored against different pools", () => {
    const scores = [SKILL_SCORES[0], skillScore({ fieldN: 12 })];

    expect(fieldSize(skillBars(scores, WEIGHTS))).toBeNull();
  });

  it("names no field without a single graded skill", () => {
    expect(fieldSize(skillBars([], WEIGHTS))).toBeNull();
  });
});

describe("SkillChart", () => {
  const bars = () => skillBars(SKILL_SCORES, WEIGHTS);

  it("shows the working for each skill, in weight order", () => {
    render(<SkillChart bars={bars()} />);

    expect(screen.getAllByRole("listitem").map((item) => item.textContent)).toEqual([
      "Instruction Following98\u00d750%49.15",
      "Faithfulness88\u00d730%26.49",
      "Reasoning95\u00d720%19.00",
    ]);
  });

  it("scores an uncovered skill at nothing rather than renormalising it away", () => {
    render(<SkillChart bars={skillBars([], { Reasoning: 1 })} />);

    expect(screen.getAllByRole("listitem")[0].textContent).toBe("Reasoning\u2014\u00d7100%0.00");
  });

  it("sizes each bar by the model's standing among the candidates", () => {
    render(<SkillChart bars={bars()} />);

    expect(barWidths()).toEqual(["98%", "88%", "95%"]);
    expect(screen.getByLabelText("98th percentile of 30")).toBeTruthy();
  });

  it("marks the median candidate on every bar and names the field once", () => {
    render(<SkillChart bars={bars()} />);

    expect(medianTicks()).toEqual(["50%", "50%", "50%"]);
    expect(screen.getByText("median of 30 models")).toBeTruthy();
  });

  it("names no field size when the skills sit in different pools", () => {
    render(<SkillChart bars={skillBars([SKILL_SCORES[0], skillScore({ fieldN: 12 })], WEIGHTS)} />);

    expect(screen.getByText("median candidate")).toBeTruthy();
  });

  it("draws no bar and no mark for a skill the model has no benchmark for", () => {
    render(<SkillChart bars={skillBars([], { Reasoning: 1 })} />);

    expect(screen.getByLabelText("No benchmark data")).toBeTruthy();
    expect(barWidths()).toEqual([""]);
    expect(medianTicks()).toEqual([]);
    expect(screen.queryByText(/^median/)).toBeNull();
  });

  it("renders nothing when the task weights no skills", () => {
    const { container } = render(<SkillChart bars={[]} />);

    expect(container.textContent).toBe("");
  });
});

describe("EvidencePanel", () => {
  function panel() {
    return render(
      <EvidencePanel
        confidence="high"
        evidence={GRADED}
        nBenchmarks={13}
        skillScores={SKILL_SCORES}
        weights={WEIGHTS}
      />
    );
  }

  it("opens on the chart, not on the benchmark table", () => {
    panel();

    expect(screen.getAllByRole("listitem")).toHaveLength(3);
    expect(screen.queryByRole("table")).toBeNull();
  });

  it("counts the benchmarks on the toggle that reveals them", () => {
    panel();

    const toggle = screen.getByRole("button", { name: "Show 3 benchmarks" });
    expect(toggle.getAttribute("title")).toBe("high confidence");
  });

  it("keeps the global percentiles behind the benchmark toggle", () => {
    panel();

    fireEvent.click(screen.getByRole("button", { name: "Show 3 benchmarks" }));

    expect(rowLabels()).toEqual(["IFBench", "CritPt", "MMLU-Pro"]);
    expect(screen.getByText(/Standing among every model the benchmark tracks/)).toBeTruthy();
    expect(screen.getByRole("button", { name: "Hide 3 benchmarks" })).toBeTruthy();
  });

  it("offers no benchmark toggle for an ungraded model", () => {
    render(
      <EvidencePanel
        confidence="none"
        evidence={[]}
        nBenchmarks={0}
        skillScores={[]}
        weights={WEIGHTS}
      />
    );

    expect(screen.queryByRole("button")).toBeNull();
    expect(screen.getAllByLabelText("No benchmark data")).toHaveLength(3);
  });
});

describe("EvidenceTable", () => {
  it("names the population its percentiles stand in", () => {
    render(<EvidenceTable rows={GRADED} />);

    expect(screen.getByText(/Standing among every model the benchmark tracks/)).toBeTruthy();
  });

  it("shows standing as a percentile, never a raw score", () => {
    render(<EvidenceTable rows={GRADED} />);

    expect(screen.getByText("88th pct")).toBeTruthy();
    expect(screen.getByText("450")).toBeTruthy();
  });

  it("opens the benchmark's own publication in a new tab", () => {
    render(<EvidenceTable rows={[evidence()]} />);

    const link = screen.getByRole("link", { name: "IFBench on arXiv" });
    expect(link.getAttribute("href")).toBe("https://arxiv.org/abs/2507.02833");
    expect(link.getAttribute("target")).toBe("_blank");
    expect(link.getAttribute("rel")).toBe("noopener noreferrer");
  });

  it("sorts self-reported rows below measured ones and marks them", () => {
    const rows = [
      evidence({ benchmark: "LabClaim", percentile: 99, provenance: "lab_claimed" }),
      evidence({ benchmark: "MMLU-Pro", percentile: 64.4, skill: "Reasoning" }),
      evidence({ benchmark: "IFBench", percentile: 88.2 }),
    ];
    render(<EvidenceTable rows={rows} />);

    expect(rowLabels()).toEqual(["IFBench", "MMLU-Pro", "LabClaimself-reported"]);
    expect(screen.getByText("self-reported")).toBeTruthy();
  });

  it("keeps self-reported ordering out of the raw row order", () => {
    const rows = [
      evidence({ benchmark: "LabClaim", percentile: 99, provenance: "lab_claimed" }),
      evidence({ benchmark: "IFBench", percentile: 88.2 }),
    ];

    expect(sortEvidence(rows).map((row) => row.benchmark)).toEqual(["IFBench", "LabClaim"]);
    expect(rows[0].benchmark).toBe("LabClaim");
  });

  it("renders nothing without evidence", () => {
    const { container } = render(<EvidenceTable rows={[]} />);

    expect(container.textContent).toBe("");
  });
});

describe("ExcludedSummary", () => {
  const excluded: FinetuningExcludedModel[] = [
    { model: "meta/llama-3.2-1b", reason: "SFT context 2,048 < longest row 4,100" },
    { model: "google/gemma-3-4b", reason: "SFT context 1,024 < longest row 4,100" },
    { model: "qwen/qwen3-4b", reason: "SFT context 3,072 < longest row 4,100" },
    { model: "mistral/ministral-3b", reason: "SFT context 2,560 < longest row 4,100" },
    { model: "openai/gpt-oss-20b", reason: "No tool-calling fine-tuning support" },
    { model: "ibm/granite-3-8b", reason: "No tool-calling fine-tuning support" },
  ];

  it("counts the exclusions by reason", () => {
    render(<ExcludedSummary excluded={excluded} />);

    expect(screen.getByRole("button").textContent).toBe(
      "6 models excluded · 4 below context length · 2 without tool-calling support"
    );
  });

  it("keeps the count singular at one model", () => {
    render(<ExcludedSummary excluded={[excluded[4]]} />);

    expect(screen.getByRole("button").textContent).toBe(
      "1 model excluded · 1 without tool-calling support"
    );
  });

  it("names each model and its reason once expanded", () => {
    render(<ExcludedSummary excluded={excluded} />);
    expect(screen.queryByText("meta/llama-3.2-1b")).toBeNull();

    fireEvent.click(screen.getByRole("button"));

    expect(screen.getByText("meta/llama-3.2-1b")).toBeTruthy();
    expect(screen.getByText("SFT context 2,048 < longest row 4,100")).toBeTruthy();
    expect(screen.getAllByText("No tool-calling fine-tuning support")).toHaveLength(2);
  });

  it("renders nothing when every model is eligible", () => {
    const { container } = render(<ExcludedSummary excluded={[]} />);

    expect(container.textContent).toBe("");
  });
});
