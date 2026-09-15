// @vitest-environment jsdom
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("@/components/ui/tooltip", () => ({
  Tooltip: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  TooltipContent: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  TooltipProvider: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  TooltipTrigger: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
}));

import { extractExecutionConflict, extractSkippedMembers } from "@/hooks/use-traces";
import {
  conflictSummary,
  ScoreConflictMarker,
  ScoreReasonChip,
  TraceExecutionScore,
  traceScoreChipLabel,
} from "./trace-score-chips";

afterEach(cleanup);

const CONFLICT_FEEDBACK = {
  trace_scoring: {
    _execution: {
      conflict: {
        lane: "output",
        members: { "delivery-judge": 0.0, "success-judge": 1.0 },
        spread: 1.0,
      },
      score: 0.5,
    },
    _skipped_members: ["ghost-outcome", "traj-a"],
  },
};

describe("ScoreReasonChip", () => {
  it("is a focusable control that exposes the rationale to AT", () => {
    render(<ScoreReasonChip label="80%" rationale="delivered the asked dataset" tone="success" />);

    const chip = screen.getByRole("button", { name: "80%" });
    expect(chip.getAttribute("title")).toBe("delivered the asked dataset");
    const describedBy = chip.getAttribute("aria-describedby");
    expect(describedBy).toBeTruthy();
    expect(document.getElementById(describedBy ?? "")?.textContent).toBe(
      "delivered the asked dataset"
    );
  });

  it("stays a static chip when there is no rationale", () => {
    render(<ScoreReasonChip label="80%" tone="success" />);
    expect(screen.queryByRole("button")).toBeNull();
    expect(screen.getByText("80%")).toBeTruthy();
  });

  it("truncates a long rationale on the trigger", () => {
    const long = "x".repeat(300);
    render(<ScoreReasonChip label="0%" rationale={long} tone="error" />);
    expect(screen.getByRole("button").getAttribute("title")?.endsWith("...")).toBe(true);
    expect((screen.getByRole("button").getAttribute("title") ?? "").length).toBeLessThan(300);
  });
});

describe("execution conflict extraction", () => {
  it("parses the composer's conflict payload", () => {
    expect(extractExecutionConflict(CONFLICT_FEEDBACK)).toEqual({
      lane: "output",
      members: { "delivery-judge": 0.0, "success-judge": 1.0 },
      spread: 1.0,
    });
  });

  it("is null without a conflict or on a malformed payload", () => {
    expect(extractExecutionConflict({ trace_scoring: { _execution: { score: 1.0 } } })).toBeNull();
    expect(
      extractExecutionConflict({
        trace_scoring: { _execution: { conflict: { members: { a: "high" }, spread: 0.8 } } },
      })
    ).toBeNull();
    expect(extractExecutionConflict(null)).toBeNull();
  });

  it("reads skipped member names", () => {
    expect(extractSkippedMembers(CONFLICT_FEEDBACK)).toEqual(["ghost-outcome", "traj-a"]);
    expect(extractSkippedMembers({ trace_scoring: {} })).toEqual([]);
  });
});

describe("traceScoreChipLabel", () => {
  const entry = {
    lane: "step",
    outcome: "not_applicable",
    passed: null,
    rationale: "",
    score: null,
  };

  it("humanizes a snake_case outcome from a run-surface row", () => {
    expect(traceScoreChipLabel("behaviour-lead-tool-loop-step-x", entry)).toBe("Not applicable");
  });
});

describe("ScoreConflictMarker", () => {
  const conflict = {
    lane: "output",
    members: { "delivery-judge": 0.0, "success-judge": 1.0 },
    spread: 1.0,
  };

  it("states the disagreement and the member scores", () => {
    expect(conflictSummary(conflict)).toBe("Outcome judges disagree: 0% vs 100%");
    render(<ScoreConflictMarker conflict={conflict} />);
    expect(screen.getByLabelText("Outcome judges disagree: 0% vs 100%")).toBeTruthy();
    expect(screen.getByText("delivery-judge")).toBeTruthy();
    expect(screen.getByText("success-judge")).toBeTruthy();
  });

  it("rides the execution score chip when a conflict is present", () => {
    render(<TraceExecutionScore conflict={conflict} score={0.5} />);
    expect(screen.getByText("50%")).toBeTruthy();
    expect(screen.getByLabelText("Outcome judges disagree: 0% vs 100%")).toBeTruthy();
  });

  it("is absent without a conflict", () => {
    render(<TraceExecutionScore score={0.5} />);
    expect(screen.queryByLabelText(/judges disagree/)).toBeNull();
  });
});
