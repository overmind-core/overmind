import { describe, expect, it } from "vitest";

import type { ConnectorShapeCandidate } from "@/openapi";
import {
  ancestorsOfSelected,
  buildShapeTree,
  descendantNames,
  namesMatching,
} from "./setup-wizard";

function shape(
  name: string,
  parentName: string | null,
  extra: Partial<ConnectorShapeCandidate> = {}
): ConnectorShapeCandidate {
  return {
    isRoot: parentName === null,
    maxPerTrace: 1,
    modelCalls: 0,
    name,
    occurrences: 1,
    parentName,
    reasons: [],
    score: 0,
    traces: 1,
    type: "SPAN",
    ...extra,
  };
}

describe("buildShapeTree", () => {
  it("nests each shape under its most common parent", () => {
    const tree = buildShapeTree([
      shape("classify-invoice", "analyze-email"),
      shape("scan-inbox", null),
      shape("analyze-email", "triage-invoices"),
      shape("triage-invoices", "scan-inbox"),
    ]);

    expect(tree.map((n) => n.shape.name)).toEqual(["scan-inbox"]);
    expect(descendantNames(tree[0])).toEqual(
      new Set(["triage-invoices", "analyze-email", "classify-invoice"])
    );
  });

  it("roots a shape whose parent left the sample", () => {
    const tree = buildShapeTree([shape("plan-payments", "scan-inbox")]);

    expect(tree.map((n) => n.shape.name)).toEqual(["plan-payments"]);
  });

  it("breaks a parent cycle instead of hanging or losing a shape", () => {
    const tree = buildShapeTree([shape("a", "b"), shape("b", "a")]);

    const names = new Set<string>();
    for (const root of tree) {
      names.add(root.shape.name);
      for (const child of descendantNames(root)) names.add(child);
    }
    expect(names).toEqual(new Set(["a", "b"]));
  });

  it("orders siblings by score, then name", () => {
    const tree = buildShapeTree([
      shape("root", null),
      shape("weak", "root", { score: 1 }),
      shape("strong", "root", { score: 5 }),
      shape("also-weak", "root", { score: 1 }),
    ]);

    expect(tree[0].children.map((n) => n.shape.name)).toEqual(["strong", "also-weak", "weak"]);
  });

  it("keeps the best-scoring shape when one name has several types", () => {
    const tree = buildShapeTree([
      shape("plan", null, { score: 1, type: "SPAN" }),
      shape("plan", null, { score: 4, type: "CAPABILITY" }),
    ]);

    expect(tree).toHaveLength(1);
    expect(tree[0].shape.type).toBe("CAPABILITY");
  });
});

describe("ancestorsOfSelected", () => {
  const tree = buildShapeTree([
    shape("scan-inbox", null),
    shape("triage-invoices", "scan-inbox"),
    shape("plan-payments", "triage-invoices"),
    shape("analyze-email", "scan-inbox"),
  ]);

  it("opens the trail to a deep pick without opening the pick itself", () => {
    expect(ancestorsOfSelected(tree, ["plan-payments"])).toEqual(
      new Set(["scan-inbox", "triage-invoices"])
    );
  });

  it("opens every branch holding a pick", () => {
    expect(ancestorsOfSelected(tree, ["plan-payments", "analyze-email"])).toEqual(
      new Set(["scan-inbox", "triage-invoices"])
    );
  });

  it("stays shut when nothing is picked", () => {
    expect(ancestorsOfSelected(tree, [])).toEqual(new Set());
  });
});

describe("namesMatching", () => {
  const tree = buildShapeTree([
    shape("scan-inbox", null),
    shape("triage-invoices", "scan-inbox"),
    shape("plan-payments", "triage-invoices"),
    shape("analyze-email", "scan-inbox"),
  ]);

  it("keeps the ancestors that place a match, so nothing renders detached", () => {
    expect(namesMatching(tree, "plan")).toEqual(
      new Set(["plan-payments", "triage-invoices", "scan-inbox"])
    );
  });

  it("ignores case and surrounding whitespace", () => {
    expect(namesMatching(tree, "  INVOICE ")).toEqual(new Set(["triage-invoices", "scan-inbox"]));
  });

  it("keeps a matching ancestor without pulling in its subtree", () => {
    expect(namesMatching(tree, "scan")).toEqual(new Set(["scan-inbox"]));
  });

  it("returns nothing for a query no name contains", () => {
    expect(namesMatching(tree, "refund")).toEqual(new Set());
  });

  it("treats a blank query as unfiltered", () => {
    expect(namesMatching(tree, "   ")).toEqual(new Set());
  });
});
