import { describe, expect, it } from "vitest";

import {
  buildTaskExecutionsListRequest,
  capabilityHandoffChain,
  conversationGroupCaption,
  deliveryLabel,
  executionIntent,
  executionRouteFlags,
  executionSessionScore,
  executionStepResults,
  gatedStep,
  groupExecutionsByTrace,
  handoffTraceIds,
  hasCapabilityHandoff,
  isUnboundExecution,
  outcomeDelivery,
  scoredStepResults,
  scoreRationale,
  stepActionLabel,
  stepRoleResults,
  stepVerdictLabel,
  taskStateLabel,
  toolActionLabel,
  turnFailureLine,
  unboundReason,
} from "@/hooks/use-task-executions";
import type { TaskExecutionList } from "@/openapi";

function execution(overrides: Partial<TaskExecutionList>): TaskExecutionList {
  return {
    behaviourKey: "checkout",
    id: "e1",
    model: null,
    scoringPending: false,
    totalCost: null,
    totalTokens: null,
    traceId: "t1",
    unitSpanId: "s1",
    ...overrides,
  };
}

describe("executionStepResults", () => {
  it("returns object entries and drops malformed values", () => {
    const raw = [{ evaluator: "retrieval", score: 0.8 }, null, "junk", 3];
    expect(executionStepResults(raw)).toEqual([{ evaluator: "retrieval", score: 0.8 }]);
  });

  it("returns [] for non-array payloads", () => {
    expect(executionStepResults(undefined)).toEqual([]);
    expect(executionStepResults({ evaluator: "x" })).toEqual([]);
  });
});

describe("executionRouteFlags", () => {
  it("keeps only strings", () => {
    expect(executionRouteFlags(["off_contract_route", 1, null])).toEqual(["off_contract_route"]);
    expect(executionRouteFlags("not-a-list")).toEqual([]);
  });
});

describe("unboundReason", () => {
  it("names a grain mismatch, the one cause the author can fix", () => {
    expect(unboundReason(execution({ routeFlags: ["declared_grain_mismatch"] }))).toBe(
      "Grain mismatch"
    );
    expect(unboundReason(execution({ routeFlags: ["grain_mismatch"] }))).toBe("Grain mismatch");
  });

  it("falls back to Unbound for every other cause", () => {
    expect(unboundReason(execution({ routeFlags: ["ambiguous_anchor_overlap"] }))).toBe("Unbound");
    expect(unboundReason(execution({}))).toBe("Unbound");
  });
});

describe("isUnboundExecution", () => {
  it("flags unbound binding source or missing behaviour", () => {
    expect(isUnboundExecution(execution({ bindingSource: "unbound" }))).toBe(true);
    expect(isUnboundExecution(execution({ behaviour: null }))).toBe(true);
    expect(isUnboundExecution(execution({ behaviour: "b1", bindingSource: "anchor_join" }))).toBe(
      false
    );
  });
});

describe("conversationGroupCaption", () => {
  it("omits session score when missing", () => {
    expect(conversationGroupCaption(3, null)).toBe("3 turns");
    expect(conversationGroupCaption(1, null)).toBe("1 turn");
  });

  it("appends the session score as a percent", () => {
    expect(conversationGroupCaption(8, 0)).toBe("8 turns · session 0%");
    expect(conversationGroupCaption(8, 0.8)).toBe("8 turns · session 80%");
  });
});

describe("taskStateLabel", () => {
  it("labels the server task-state statuses", () => {
    expect(taskStateLabel("outstanding")).toBe("Outstanding");
    expect(taskStateLabel("delivered")).toBe("Complete");
  });
});

describe("outcomeDelivery", () => {
  it("reads the scored outcome delivery field", () => {
    expect(
      outcomeDelivery([
        { delivery: "delivered", outcome: "scored", role: "step" },
        { delivery: "delivered_wrong", outcome: "scored", role: "outcome" },
      ])
    ).toBe("delivered_wrong");
    expect(deliveryLabel("delivered_wrong")).toBe("Wrong artifact");
    expect(deliveryLabel("first_park")).toBe("First park");
  });
});

describe("executionSessionScore", () => {
  it("reads a numeric session score and ignores blanks", () => {
    expect(executionSessionScore({ sessionScore: 0 })).toBe(0);
    expect(executionSessionScore({ sessionScore: null })).toBeNull();
    expect(executionSessionScore(undefined)).toBeNull();
  });
});

describe("handoffTraceIds", () => {
  it("keeps only traces spanning 2+ capability identities", () => {
    const rows = [
      execution({ capability: "cap-a", id: "a1", traceId: "t1" }),
      execution({ capability: "cap-b", id: "b1", traceId: "t1" }),
      execution({ capability: "cap-a", id: "a2", traceId: "t2" }),
      execution({ capability: "cap-a", id: "a3", traceId: "t2" }),
      execution({ capability: "cap-a", id: "a4", traceId: "t3" }),
    ];
    expect(handoffTraceIds(rows)).toEqual(new Set(["t1"]));
  });

  it("counts an unbound execution as its own identity", () => {
    const rows = [
      execution({ capability: "cap-a", id: "a1", traceId: "t1" }),
      execution({ capability: null, id: "u1", traceId: "t1" }),
      execution({ capability: null, id: "u2", traceId: "t2" }),
      execution({ capability: null, id: "u3", traceId: "t2" }),
    ];
    expect(handoffTraceIds(rows)).toEqual(new Set(["t1"]));
  });
});

describe("groupExecutionsByTrace", () => {
  const at = (iso: string) => new Date(iso);

  it("pulls listed traces together chronologically and leaves the rest in place", () => {
    const rows = [
      execution({ id: "h-late", startedAt: at("2026-08-13T12:00:00Z"), traceId: "t1" }),
      execution({ id: "solo", startedAt: at("2026-08-13T11:30:00Z"), traceId: "t2" }),
      execution({ id: "h-early", startedAt: at("2026-08-13T11:00:00Z"), traceId: "t1" }),
      execution({ id: "tail", startedAt: at("2026-08-13T10:00:00Z"), traceId: "t3" }),
    ];
    expect(groupExecutionsByTrace(rows, new Set(["t1"])).map((r) => r.id)).toEqual([
      "h-early",
      "h-late",
      "solo",
      "tail",
    ]);
  });

  it("returns rows untouched when no trace is listed", () => {
    const rows = [execution({ id: "a", traceId: "t1" }), execution({ id: "b", traceId: "t2" })];
    expect(groupExecutionsByTrace(rows, new Set()).map((r) => r.id)).toEqual(["a", "b"]);
  });
});

describe("capabilityHandoffChain", () => {
  const at = (iso: string) => new Date(iso);
  const names = new Map([
    ["cap-a", "Planner"],
    ["cap-b", "Writer"],
  ]);

  it("orders by start time and collapses consecutive repeats", () => {
    const rows = [
      execution({ capability: "cap-b", id: "b1", startedAt: at("2026-08-13T12:00:00Z") }),
      execution({ capability: "cap-a", id: "a1", startedAt: at("2026-08-13T11:00:00Z") }),
      execution({ capability: "cap-a", id: "a2", startedAt: at("2026-08-13T11:30:00Z") }),
    ];
    expect(capabilityHandoffChain(rows, names)).toEqual(["Planner", "Writer"]);
  });

  it("labels unbound executions and falls back to a shortened id", () => {
    const rows = [
      execution({ capability: null, id: "u1", startedAt: at("2026-08-13T11:00:00Z") }),
      execution({
        capability: "12345678-aaaa-bbbb-cccc-000000000000",
        id: "x1",
        startedAt: at("2026-08-13T12:00:00Z"),
      }),
    ];
    expect(capabilityHandoffChain(rows, names)).toEqual(["Unbound", "12345678…"]);
  });
});

describe("hasCapabilityHandoff", () => {
  it("requires 2+ capability identities", () => {
    expect(
      hasCapabilityHandoff([
        execution({ capability: "cap-a", id: "a1" }),
        execution({ capability: "cap-a", id: "a2" }),
      ])
    ).toBe(false);
    expect(
      hasCapabilityHandoff([
        execution({ capability: "cap-a", id: "a1" }),
        execution({ capability: null, id: "u1" }),
      ])
    ).toBe(true);
  });
});

describe("executionIntent", () => {
  it("parses text and source", () => {
    expect(executionIntent({ source: "declared", text: "Triage emails" })).toEqual({
      source: "declared",
      text: "Triage emails",
    });
  });

  it("prefers running intent when it differs from this turn", () => {
    expect(
      executionIntent({
        running: "fine-tune — then: now show the loss curves",
        source: "declared",
        text: "now show the loss curves",
      })
    ).toEqual({
      current: "now show the loss curves",
      source: "declared",
      text: "fine-tune — then: now show the loss curves",
    });
  });

  it("rejects blank or malformed payloads", () => {
    expect(executionIntent(null)).toBeNull();
    expect(executionIntent({ text: "  " })).toBeNull();
    expect(executionIntent("triage")).toBeNull();
  });
});

describe("scoreRationale", () => {
  it("prefers the outcome-role rationale over a step rationale", () => {
    expect(
      scoreRationale([
        { outcome: "scored", rationale: "retrieval was thin", role: "step", score: 0.4 },
        { outcome: "scored", rationale: "delivered the asked dataset", role: "outcome", score: 1 },
      ])
    ).toBe("delivered the asked dataset");
  });

  it("falls back to the first scored step rationale", () => {
    expect(
      scoreRationale([
        { outcome: "segment_not_run", rationale: "not run", role: "step" },
        { outcome: "scored", rationale: "  tool missed the park  ", role: "step", score: 0 },
      ])
    ).toBe("tool missed the park");
  });

  it("returns empty when no scored rationale exists", () => {
    expect(scoreRationale([{ outcome: "scored", role: "outcome", score: 1 }])).toBe("");
  });
});

describe("stepRoleResults", () => {
  it("keeps scored step-role rows and drops outcome and not-run", () => {
    const steps = [
      { evaluator: "step-1", outcome: "scored", role: "step", score: 0.9 },
      { evaluator: "success", outcome: "scored", role: "outcome", score: 1 },
      { evaluator: "step-2", outcome: "segment_not_run", role: "step" },
      { evaluator: "step-3", outcome: "unscored", role: "step" },
    ];
    expect(stepRoleResults(steps).map((s) => s.evaluator)).toEqual(["step-1"]);
  });
});

describe("stepActionLabel", () => {
  it("parses a behaviour step slug and does not humanize the evaluator id", () => {
    expect(
      stepActionLabel({
        evaluator: "behaviour-decision-surface-main-loop-step-assemble-transcript",
        role: "step",
      })
    ).toBe("Assemble transcript");
    expect(stepActionLabel({ evaluator: "step-1", role: "step" })).toBe("Step");
    expect(stepActionLabel({ role: "step", segment: ["m.run", "m.analyze_llm"] })).toBe(
      "Analyze LLM"
    );
    expect(toolActionLabel("python-agent.agent.analyze_llm")).toBe("Analyze LLM");
  });
});

describe("stepVerdictLabel", () => {
  it("renders a percent, pass/fail, or a dash — never a missing score as 0%", () => {
    expect(stepVerdictLabel({ score: 1 })).toBe("100%");
    expect(stepVerdictLabel({ passed: true })).toBe("Pass");
    expect(stepVerdictLabel({ passed: false })).toBe("Fail");
    expect(stepVerdictLabel({})).toBe("—");
  });
});

describe("gatedStep", () => {
  it("returns the step that gated, not the outcome", () => {
    const steps = [
      {
        evaluator: "behaviour-main-loop-step-assemble-transcript",
        outcome: "scored",
        passed: false,
        rationale: "Tool returned no spans for the requested id.",
        role: "step",
        score: 0.2,
      },
      {
        evaluator: "success",
        outcome: "scored",
        rationale: "delivered the asked dataset",
        role: "outcome",
        score: 1,
      },
    ];
    expect(gatedStep(steps)?.evaluator).toBe("behaviour-main-loop-step-assemble-transcript");
    expect(turnFailureLine(steps)).toBe(
      "Failed: Assemble transcript. Tool returned no spans for the requested id."
    );
  });

  it("returns empty when no step gated", () => {
    expect(
      turnFailureLine([{ outcome: "scored", rationale: "ok", role: "outcome", score: 1 }])
    ).toBe("");
  });
});

describe("scoredStepResults", () => {
  it("keeps only entries with a real verdict", () => {
    const steps = [
      { evaluator: "a", outcome: "scored", score: 0.9 },
      { evaluator: "b", outcome: "segment_not_run" },
      { evaluator: "c", outcome: "unscored" },
      { evaluator: "d", outcome: "scored", passed: true },
      { evaluator: "e", outcome: "skipped" },
    ];
    expect(scoredStepResults(steps).map((s) => s.evaluator)).toEqual(["a", "d"]);
  });
});

describe("buildTaskExecutionsListRequest", () => {
  it("maps the shared traces filter params onto the executions request", () => {
    const request = buildTaskExecutionsListRequest({
      filters: {
        has_error: "true",
        has_model: "true",
        min_duration_ms: "5000",
        model: "gpt-5-nano",
        operation__icontains: "chat",
        received_at__gte: "2026-01-01T00:00:00.000Z",
        service_name__icontains: "worker",
        span_type: "entry_point",
        status_code: "2",
        total_cost__gte: "0.01",
        total_tokens__gte: "5000",
      },
      page: 1,
      project: "proj-1",
      search: "conv-hit",
    });

    expect(request).toMatchObject({
      hasError: true,
      hasModel: true,
      minDurationMs: 5000,
      model: "gpt-5-nano",
      operation: "chat",
      project: "proj-1",
      search: "conv-hit",
      serviceName: "worker",
      spanType: "entry_point",
      statusCode: 2,
      totalCostGte: 0.01,
      totalTokensGte: 5000,
    });
    expect(request.receivedAtGte?.toISOString()).toBe("2026-01-01T00:00:00.000Z");
  });
});
