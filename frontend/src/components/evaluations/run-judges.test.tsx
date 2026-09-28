// @vitest-environment jsdom
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";

import type { RunEvaluator } from "@/openapi";
import { RunJudges } from "./run-judges";

vi.mock("@/components/model-provider-chip", () => ({
  ModelProviderChip: ({ model }: { model: string }) => <span>{model}</span>,
}));
afterEach(cleanup);

it("shows frozen judge choices, deduplicating models and excluding disabled or deterministic checks", () => {
  render(
    <RunJudges
      evaluators={
        [
          { enabled: true, snapshot: { judge_model: "selected", kind: "llm_judge" } },
          { enabled: true, snapshot: { judge_model: "selected", kind: "agentic" } },
          {
            enabled: true,
            snapshot: { config: { mode: "judge" }, judge_model: "second", kind: "trajectory" },
          },
          { enabled: false, snapshot: { judge_model: "disabled", kind: "llm_judge" } },
          { enabled: true, snapshot: { judge_model: "unused", kind: "deterministic" } },
        ] as RunEvaluator[]
      }
    />
  );
  expect(screen.getAllByText("selected")).toHaveLength(1);
  expect(screen.getByText("second")).toBeTruthy();
  expect(screen.queryByText("disabled")).toBeNull();
  expect(screen.queryByText("unused")).toBeNull();
});
