// @vitest-environment jsdom

import { act, cleanup, render, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { Cell } from "@/openapi";
import { CellMotionFrame, useCellMotion } from "./cell-motion";

const cell = (id: string, fields: Partial<Cell> = {}) =>
  ({ fingerprint: id, id, rows: 3, state: "ok", title: id, ...fields }) as Cell;
const visible = new Set(["source", "result"]);

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("live cell change detection", () => {
  it("keeps initial loading, unchanged polling and usage metadata quiet", () => {
    const source = cell("source");
    const view = renderHook(({ cells }) => useCellMotion(cells, visible), {
      initialProps: { cells: undefined as Cell[] | undefined },
    });
    view.rerender({ cells: [source] });
    view.rerender({ cells: [{ ...source, frozen: true, updatedAt: new Date() }] });
    expect(view.result.current.motions.size).toBe(0);
  });

  it("reveals the first landed source and acknowledges only the matching revision", () => {
    const view = renderHook(({ cells }) => useCellMotion(cells, visible), {
      initialProps: { cells: [] as Cell[] },
    });
    view.rerender({ cells: [cell("source", { state: "running" })] });
    const arrival = view.result.current.motions.get("source")!;
    expect(arrival.kind).toBe("arrival");
    view.rerender({ cells: [cell("source")] });
    const update = view.result.current.motions.get("source")!;
    expect(update.kind).toBe("update");
    act(() => view.result.current.complete("source", arrival.revision));
    expect(view.result.current.motions.get("source")).toBe(update);
    act(() => view.result.current.complete("source", update.revision));
    expect(view.result.current.motions.size).toBe(0);
  });

  it("does not replay hidden updates when browsing historical branches", () => {
    const source = cell("source");
    const view = renderHook(({ cells, ids }) => useCellMotion(cells, ids), {
      initialProps: { cells: [source], ids: new Set(["source"]) },
    });
    const cells = [source, cell("result")];
    view.rerender({ cells, ids: new Set(["source"]) });
    view.rerender({ cells, ids: visible });
    expect(view.result.current.motions.size).toBe(0);
  });
});

describe("cell motion runtime", () => {
  let reduced = false;
  const cancel = vi.fn();
  const animate = vi.fn();

  beforeEach(() => {
    reduced = false;
    cancel.mockReset();
    animate.mockReset().mockReturnValue({ cancel, finished: new Promise(() => {}) });
    vi.stubGlobal("matchMedia", () => ({ matches: reduced }));
    vi.spyOn(document, "visibilityState", "get").mockReturnValue("visible");
    vi.spyOn(Element.prototype, "getBoundingClientRect").mockReturnValue({
      bottom: 110,
      height: 100,
      left: 10,
      right: 110,
      top: 10,
      width: 100,
    } as DOMRect);
    vi.stubGlobal("Animation", class {});
    Object.defineProperty(Element.prototype, "animate", { configurable: true, value: animate });
  });

  it("waits for layout, preserves mounted controls and cancels superseded motion", () => {
    const complete = vi.fn();
    const props = { complete, id: "source", motion: { kind: "arrival" as const, revision: 1 } };
    const content = (
      <div data-cell-frame>
        <input aria-label="Cell input" defaultValue="kept" />
      </div>
    );
    const view = render(
      <CellMotionFrame {...props} ready={false}>
        {content}
      </CellMotionFrame>
    );
    expect(animate).not.toHaveBeenCalled();
    const input = view.getByLabelText("Cell input");
    input.focus();
    view.rerender(
      <CellMotionFrame {...props} ready>
        {content}
      </CellMotionFrame>
    );
    expect(animate).toHaveBeenCalledTimes(1);
    view.rerender(
      <CellMotionFrame {...props} motion={{ kind: "update", revision: 2 }} ready>
        {content}
      </CellMotionFrame>
    );
    expect(cancel).toHaveBeenCalledTimes(1);
    expect(view.getByLabelText("Cell input")).toBe(input);
    expect(document.activeElement).toBe(input);
    view.unmount();
    expect(cancel).toHaveBeenCalledTimes(2);
  });

  it("uses gentler reduced motion and skips background updates", () => {
    reduced = true;
    const complete = vi.fn();
    const view = render(
      <CellMotionFrame
        complete={complete}
        id="source"
        motion={{ kind: "arrival", revision: 1 }}
        ready
      >
        <div data-cell-frame />
      </CellMotionFrame>
    );
    expect(animate.mock.calls[0][0][0].opacity).toBeGreaterThanOrEqual(0.8);
    expect(animate.mock.calls[0][1].duration).toBeLessThanOrEqual(150);
    vi.spyOn(document, "visibilityState", "get").mockReturnValue("hidden");
    view.rerender(
      <CellMotionFrame
        complete={complete}
        id="source"
        motion={{ kind: "update", revision: 2 }}
        ready
      >
        <div data-cell-frame />
      </CellMotionFrame>
    );
    expect(animate).toHaveBeenCalledTimes(1);
    expect(complete).toHaveBeenCalledWith("source", 2);
  });
});
