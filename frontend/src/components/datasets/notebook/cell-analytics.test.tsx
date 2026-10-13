// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import type { DatasetAnalytics } from "@/openapi";
import { AnalyticsAlerts, AnalyticsDetails } from "./cell-analytics";

afterEach(cleanup);

const report: DatasetAnalytics = {
  cell: "cell",
  checks: [
    {
      id: "targets.class_balance",
      metrics: { families: ["fraud"] },
      status: "warning",
      summary: "Accuracy can conceal missed classes.",
      title: "Class imbalance",
    },
    {
      id: "leakage",
      metrics: {},
      status: "not_checked",
      summary: "Requires related splits.",
      title: "Split leakage",
    },
    {
      id: "targets.validity",
      metrics: {},
      status: "pass",
      summary: "All decision rows have valid targets.",
      title: "Target validity",
    },
  ],
  dataset: "dataset",
  families: [
    {
      counts: [1000, 1],
      id: "fraud",
      labels: ["No", "Yes"],
      labelsTruncated: false,
      majorityBaseline: 1000 / 1001,
      question: "Fraud?",
      rows: 1001,
      semantics: "categorical_gold",
      targetMass: [1000, 1],
      weightSum: 1001,
    },
  ],
  fingerprint: "abc",
  rowsScanned: 1001,
  status: "completed",
};

describe("Workshop analytics", () => {
  it("keeps warnings and unchecked coverage visible without opening the report", () => {
    render(<AnalyticsAlerts report={report} />);
    expect(screen.getByText("Class imbalance")).toBeTruthy();
    expect(screen.getByText("1 not checked")).toBeTruthy();
  });
  it("shows exact class counts and a baseline, not model accuracy", () => {
    render(<AnalyticsDetails report={report} />);
    const finding = screen.getByRole("region", { name: "Class imbalance" });
    expect(within(finding).getByText("Accuracy alone is misleading here")).toBeTruthy();
    expect(finding.textContent).toContain("99.90% accuracy while missing all 1 “Yes” example.");
    expect(finding.textContent).toContain("track precision and recall for “Yes”");
    expect(finding.textContent).toContain("not a trained model result");
    expect(finding.compareDocumentPosition(screen.getByRole("table")) & 4).toBe(4);
    expect(screen.getByText("0.10%")).toBeTruthy();
    expect(screen.getByRole("button", { name: "1 checks not assessed" })).toBeTruthy();
    expect(screen.queryByText("Evidence")).toBeNull();
    expect(screen.queryByRole("link")).toBeNull();
  });
  it("collapses completed checks while keeping coverage gaps named", () => {
    render(<AnalyticsDetails report={report} />);
    const button = screen.getByRole("button", { name: "1 completed checks" });
    const details = document.getElementById(button.getAttribute("aria-controls")!);
    expect(button.getAttribute("aria-expanded")).toBe("false");
    expect(details?.hidden).toBe(true);
    expect(screen.getAllByText("Split leakage").some((el) => !el.closest("[hidden]"))).toBe(true);
    fireEvent.click(button);
    expect(button.getAttribute("aria-expanded")).toBe("true");
    expect(details?.hidden).toBe(false);
    fireEvent.click(button);
    expect(details?.hidden).toBe(true);
  });
  it("puts errors before warnings", () => {
    render(
      <AnalyticsDetails
        report={{
          ...report,
          checks: [
            ...report.checks,
            {
              id: "invalid",
              metrics: {},
              status: "error",
              summary: "Invalid targets.",
              title: "Invalid targets",
            },
          ],
        }}
      />
    );
    expect(screen.getAllByRole("region")[0].getAttribute("aria-label")).toBe("Invalid targets");
  });
  it.each([
    "unknown",
    "soft_distribution",
  ])("never infers a missed-class claim for %s targets", (semantics) => {
    render(
      <AnalyticsDetails
        report={{
          ...report,
          families: [{ ...report.families[0], counts: null, majorityBaseline: null, semantics }],
        }}
      />
    );
    expect(screen.queryByText("Accuracy alone is misleading here")).toBeNull();
    expect(screen.getByText("Accuracy can conceal missed classes.")).toBeTruthy();
  });
  it("does not claim zero examples were missed for an absent class", () => {
    render(
      <AnalyticsDetails
        report={{
          ...report,
          families: [{ ...report.families[0], counts: [1001, 0], majorityBaseline: 1 }],
        }}
      />
    );
    expect(screen.queryByText(/while missing all/)).toBeNull();
    expect(screen.getByText("Accuracy can conceal missed classes.")).toBeTruthy();
  });
  it("does not generate a warning that Overmind did not report", () => {
    render(
      <AnalyticsDetails
        report={{ ...report, checks: report.checks.filter((check) => check.status !== "warning") }}
      />
    );
    expect(screen.queryByText("Accuracy alone is misleading here")).toBeNull();
    expect(screen.getByText(/Always predicting/)).toBeTruthy();
  });
  it("does not turn absent historical analytics into a clean result", () => {
    render(<AnalyticsAlerts report={undefined} />);
    expect(screen.getByText("Metrics not measured")).toBeTruthy();
  });
});
