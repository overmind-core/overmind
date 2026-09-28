// @vitest-environment jsdom
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, it } from "vitest";

import type { InferenceLiveStats } from "@/openapi";
import { ServingStatusBadge } from "./serving-status";

afterEach(cleanup);

it.each([
  undefined,
  { available: false },
  { available: true, numTotalRunners: null },
])("keeps missing worker measurements unknown", (live) => {
  render(<ServingStatusBadge live={live as InferenceLiveStats | undefined} status="ready" />);
  expect(screen.getByText("Unknown")).toBeTruthy();
  expect(screen.queryByText("Asleep")).toBeNull();
});

it("distinguishes a warm worker from the live routing selection", () => {
  render(
    <ServingStatusBadge live={{ recentlyActive: true } as InferenceLiveStats} status="ready" />
  );
  expect(screen.getByText("Warm")).toBeTruthy();
  expect(screen.queryByText("Live")).toBeNull();
});

it.each([
  [{ available: true, numTotalRunners: 1 }, "Warm"],
  [{ available: true, numTotalRunners: 0 }, "Asleep"],
  [{ available: true, numTotalRunners: 0, warming: true }, "Warming"],
])("shows measured worker state", (live, label) => {
  render(<ServingStatusBadge live={live as InferenceLiveStats} status="ready" />);
  expect(screen.getByText(label)).toBeTruthy();
});
