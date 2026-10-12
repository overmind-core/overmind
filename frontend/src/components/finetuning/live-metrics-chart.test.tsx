// @vitest-environment jsdom

import { cloneElement, type ReactElement } from "react";

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";

import { seriesColor } from "@/lib/colors";
import { LiveMetricsChart } from "./live-metrics-chart";

vi.mock("recharts", async (importOriginal) => ({
  ...(await importOriginal<typeof import("recharts")>()),
  ResponsiveContainer: ({ children, height }: { children: ReactElement; height: number }) =>
    cloneElement(children as ReactElement<{ width: number; height: number }>, {
      height,
      width: 640,
    }),
}));

afterEach(cleanup);

it("retains every live metric, epoch chart, colour and checkpoint marker", () => {
  const { container, rerender } = render(<LiveMetricsChart modelName="Model" progress={{}} />);
  expect(container.innerHTML).toBe("");
  const eval_history = [
    { epoch: 1, eval_loss: 0.5, eval_token_accuracy: 0.8, step: 2 },
    { epoch: 2, eval_loss: 0.4, eval_token_accuracy: 0.9, step: 4 },
  ];
  rerender(<LiveMetricsChart modelName="Model" progress={{ eval_history }} />);
  expect(container.querySelectorAll(".recharts-surface")).toHaveLength(4);
  expect(screen.queryByText("Train loss")).toBeNull();
  rerender(
    <LiveMetricsChart
      modelName="Model"
      progress={{
        eval_history,
        metrics_history: [1, 2, 3, 4].map((step) => ({
          lr: 0.0002,
          step,
          token_accuracy: step / 4,
          train_loss: 1 - step / 4,
        })),
      }}
    />
  );
  const charts = container.querySelectorAll(".recharts-surface");
  expect(charts).toHaveLength(9);
  expect(screen.getByText("Train loss (epoch)")).toBeTruthy();
  expect(screen.getByText("Val accuracy (epoch)")).toBeTruthy();
  expect(charts[4].getAttribute("height")).toBe("90");
  expect(container.querySelectorAll(".recharts-reference-line")).toHaveLength(4);
  expect(
    [...container.querySelectorAll(".recharts-line-curve")].map((line) =>
      line.getAttribute("stroke")
    )
  ).toEqual([0, 1, 2, 3, 4, 0, 1, 2, 3].map(seriesColor));
});
