// @vitest-environment jsdom
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, it } from "vitest";

import { TraceLoadingState } from "@/components/traces/TraceLoadingState";
import { LoadingState } from "./spinner";

afterEach(cleanup);

it("announces page loading with an accessible spinner", () => {
  const { container, rerender } = render(<LoadingState label="Loading evaluation" />);
  expect(screen.getByRole("status", { name: "Loading evaluation" }).getAttribute("aria-busy")).toBe(
    "true"
  );
  expect(container.querySelector('[data-slot="spinner"]')).toBeTruthy();
  expect(container.querySelector('[data-slot="skeleton"]')).toBeNull();
  rerender(<div>Evaluation results</div>);
  expect(screen.queryByRole("status")).toBeNull();
  expect(screen.getByText("Evaluation results")).toBeTruthy();
});

it("shows one accessible trace loading state without empty-result claims", () => {
  const { container } = render(<TraceLoadingState />);
  expect(screen.getAllByRole("status")).toHaveLength(1);
  expect(screen.getByRole("status", { name: "Loading trace" })).toBeTruthy();
  expect(container.querySelector('[data-slot="spinner"]')).toBeTruthy();
  expect(screen.queryByText("0 spans")).toBeNull();
});
