// @vitest-environment jsdom
// covers: measured-facts
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, it } from "vitest";

import type { Cell } from "@/openapi";
import { CellLabels } from "./cell-labels";

afterEach(cleanup);

it("shows each label's share of the rows", () => {
  const cell = {
    analytics: {
      families: [
        {
          counts: [1000, 1],
          id: "fraud",
          labels: ["No", "Yes"],
          question: "Fraud?",
          rows: 1001,
          targetMass: [1000, 1],
        },
      ],
      status: "completed",
    },
    rows: 1001,
  } as unknown as Cell;
  render(<CellLabels cell={cell} />);
  expect(screen.getByText("0.10%")).toBeTruthy();
  expect(screen.getByText("99.90%")).toBeTruthy();
});
