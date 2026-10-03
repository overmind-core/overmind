// @vitest-environment jsdom
import { mkdirSync, readdirSync, readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen } from "@testing-library/react";
import { expect, it } from "vitest";

import { PilotRequest } from "@/components/datasets/notebook/pilot-request";
import type { FinetuningJobList } from "@/openapi";
import { NativeEvaluationPanel } from "./native-evaluation-panel";

it("renders paired coverage, uncertainty and scheduled work using real components", () => {
  const suite = {
    calibration: { cell: "calibration-fixture", dataset: "calibration-dataset", rows: 120 },
    final: { cell: "final-fixture", dataset: "final-dataset", rows: 480 },
  };
  const plan = {
    calibration: {},
    calls: {},
    config: { suites: suite },
    error: "",
    id: "plan-fixture",
    results: {
      raw: {
        benchmarks: {
          Banking77: {
            baseline_only: 0,
            candidate_only: 0,
            expected: 240,
            metrics: {
              brier: {
                candidate_minus_baseline: -0.052,
                decisions: 239,
                interval_95: [-0.08, -0.01],
              },
              cross_entropy: {
                candidate_minus_baseline: -0.124,
                decisions: 239,
                interval_95: [-0.16, -0.04],
              },
            },
            paired_decisions: 239,
          },
          "Document NLI": {
            baseline_only: 0,
            candidate_only: 0,
            expected: 240,
            metrics: {
              cross_entropy: {
                candidate_minus_baseline: 0.024,
                decisions: 240,
                interval_95: [-0.02, 0.07],
              },
            },
            paired_decisions: 240,
          },
        },
      },
    },
    state: "completed",
  };
  const calibrated = {
    ...plan.results.raw.benchmarks.Banking77,
    metrics: { cross_entropy: { candidate_minus_baseline: -0.2, decisions: 239 } },
  };
  const completed = {
    baseModel: "fixture",
    id: "completed-fixture",
    name: "Pilot · synthetic fixture",
    nativeEvaluation: {
      ...plan,
      results: { ...plan.results, calibrated: { benchmarks: { Banking77: calibrated } } },
    },
  } as unknown as FinetuningJobList;
  const waiting = {
    ...completed,
    id: "scheduled-fixture",
    name: "Full corpus · synthetic fixture",
    nativeEvaluation: { ...plan, results: {}, state: "waiting_for_checkpoint" },
  } as unknown as FinetuningJobList;
  const view = render(
    <QueryClientProvider client={new QueryClient()}>
      <main className="mx-auto flex max-w-5xl flex-col gap-6 p-6">
        <header>
          <h1 className="text-2xl font-semibold">Training workflow qualification</h1>
          <p className="mt-2 text-sm text-muted-foreground">
            Synthetic fixtures · visual proof of the isolated implementation · not live benchmark
            results
          </p>
        </header>
        <NativeEvaluationPanel job={completed} projectId="fixture" />
        <NativeEvaluationPanel job={waiting} projectId="fixture" />
        <PilotRequest
          cell={{ fingerprint: "fixture", id: "source-fixture", rows: 500000, version: "1.0" }}
          disabled={false}
          onRequest={() => {}}
        />
      </main>
    </QueryClientProvider>
  );
  expect(screen.getByText("239 / 240")).toBeTruthy();
  expect(screen.getByText("Scheduled after checkpoint verification")).toBeTruthy();
  expect(screen.getByText("0.0240")).toBeTruthy();
  expect(
    screen.getAllByRole("link", { name: "Open final dataset" })[0].getAttribute("href")
  ).toContain("cell=final-fixture");
  fireEvent.click(screen.getByRole("button", { name: "Calibrated metrics" }));
  expect(screen.getByText("-0.2000")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Raw metrics" }));
  if (process.env.OVERMIND_UI_PROOF) {
    const css = readdirSync("dist/assets")
      .filter((name) => name.endsWith(".css"))
      .map((name) => readFileSync(join("dist/assets", name), "utf8"))
      .join("\n");
    mkdirSync(process.env.OVERMIND_UI_PROOF, { recursive: true });
    writeFileSync(
      join(process.env.OVERMIND_UI_PROOF, "platform-preview.html"),
      `<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>Overmind qualification</title><style>${css}</style></head><body class="bg-background text-foreground">${view.container.innerHTML}</body></html>`
    );
  }
});
