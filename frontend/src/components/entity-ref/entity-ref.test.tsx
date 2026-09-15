// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { EntityLink } from "@/components/entity-ref";

// TanStack's <Link> needs a router in context; a plain <a> stands in for it.
vi.mock("@tanstack/react-router", () => ({
  Link: ({ children, to, params, search, ...rest }: Record<string, unknown>) => (
    <a data-params={JSON.stringify(params ?? null)} data-to={String(to)} {...rest}>
      {children as React.ReactNode}
    </a>
  ),
}));

afterEach(cleanup);

// Radix `HoverCardTrigger asChild` clones EntityLink and hands it the handlers
// that open the card; dropping unknown props still typechecks and renders.
describe("EntityLink prop forwarding", () => {
  it("passes unknown props through to the anchor", () => {
    render(
      <EntityLink
        className="chip"
        data-state="closed"
        data-testid="ref"
        id="abc123"
        kind="capability"
        onClick={() => {}}
      >
        Capability
      </EntityLink>
    );
    // Radix marks its trigger with data-state; if the spread is missing, so is this.
    expect(screen.getByTestId("ref").getAttribute("data-state")).toBe("closed");
  });

  it("forwards handlers, not just attributes", () => {
    const onPointerEnter = vi.fn();
    render(
      <EntityLink
        className="chip"
        data-testid="ref"
        id="abc123"
        kind="capability"
        onClick={() => {}}
        onPointerEnter={onPointerEnter}
      >
        Capability
      </EntityLink>
    );
    // React routes enter/leave through its synthetic system — a raw dispatched
    // Event would not reach the handler.
    fireEvent.pointerEnter(screen.getByTestId("ref"));
    expect(onPointerEnter).toHaveBeenCalled();
  });

  it("routes every kind to its own path", () => {
    const cases = [
      ["capability", "/capabilities/$capabilityId"],
      ["dataset", "/datasets/$datasetId"],
      ["evalRun", "/evaluations/runs/$runId"],
      ["experiment", "/optimiser/$experimentId"],
      ["model", "/inference/$modelId"],
      ["project", "/projects/$projectId"],
      ["trace", "/observability/$traceId"],
      ["job", "/training"],
    ] as const;
    for (const [kind, path] of cases) {
      cleanup();
      render(
        <EntityLink className="chip" data-testid="ref" id="abc123" kind={kind} onClick={() => {}}>
          x
        </EntityLink>
      );
      expect(screen.getByTestId("ref").getAttribute("data-to")).toBe(path);
    }
  });
});
