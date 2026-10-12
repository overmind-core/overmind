// @vitest-environment jsdom

import { act, cleanup, render, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { Cell } from "@/openapi";
import { useVersionMotion, VersionMotionFrame } from "./cell-motion";

const version = (id: string, fields: Partial<Cell> = {}) =>
  ({
    capabilityReport: null,
    id,
    impact: null,
    intentReport: { train: { ok: false } },
    stats: null,
    ...fields,
  }) as Cell;

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("version motion", () => {
  it("stays quiet on the first load and when only usage changes", () => {
    const source = version("v0");
    const view = renderHook(({ cells }) => useVersionMotion(cells), {
      initialProps: { cells: undefined as Cell[] | undefined },
    });
    view.rerender({ cells: [source] });
    view.rerender({ cells: [{ ...source, frozen: true, usedAt: new Date() }] });
    expect(view.result.current.motions.size).toBe(0);
  });

  it("marks a new version as an arrival and re-measured reports as a refresh", () => {
    const source = version("v0");
    const view = renderHook(({ cells }) => useVersionMotion(cells), {
      initialProps: { cells: [source] },
    });
    view.rerender({ cells: [source, version("v1")] });
    const arrival = view.result.current.motions.get("v1");
    expect(arrival?.kind).toBe("arrival");
    view.rerender({ cells: [source, version("v1", { intentReport: { train: { ok: true } } })] });
    const refresh = view.result.current.motions.get("v1");
    expect(refresh?.kind).toBe("refresh");
    act(() => view.result.current.settle("v1", arrival!.id));
    expect(view.result.current.motions.get("v1")).toBe(refresh);
    act(() => view.result.current.settle("v1", refresh!.id));
    expect(view.result.current.motions.size).toBe(0);
  });

  it("drops motions nobody played after two seconds", () => {
    vi.useFakeTimers();
    const view = renderHook(({ cells }) => useVersionMotion(cells), {
      initialProps: { cells: [version("v0")] },
    });
    view.rerender({ cells: [version("v0"), version("v1")] });
    expect(view.result.current.motions.size).toBe(1);
    act(() => vi.advanceTimersByTime(2000));
    expect(view.result.current.motions.size).toBe(0);
  });
});

describe("version motion frame", () => {
  const cancel = vi.fn();
  const animate = vi.fn();
  let top = 10;

  beforeEach(() => {
    top = 10;
    cancel.mockReset();
    animate.mockReset().mockReturnValue({ cancel, finished: new Promise(() => {}) });
    vi.stubGlobal("matchMedia", () => ({ matches: false }));
    vi.spyOn(document, "visibilityState", "get").mockReturnValue("visible");
    vi.spyOn(Element.prototype, "getBoundingClientRect").mockImplementation(
      () => ({ bottom: top + 100, left: 10, right: 110, top }) as DOMRect
    );
    Object.defineProperty(Element.prototype, "animate", { configurable: true, value: animate });
  });

  it("waits until the version is placed, animates opacity only and cancels on unmount", () => {
    const settle = vi.fn();
    const motion = { id: 1, kind: "arrival" as const };
    const view = render(
      <VersionMotionFrame cellId="v1" motion={motion} placed={false} settle={settle}>
        <div data-cell-frame />
      </VersionMotionFrame>
    );
    expect(animate).not.toHaveBeenCalled();
    view.rerender(
      <VersionMotionFrame cellId="v1" motion={motion} placed settle={settle}>
        <div data-cell-frame />
      </VersionMotionFrame>
    );
    expect(animate).toHaveBeenCalledTimes(1);
    const frames = animate.mock.calls[0][0] as Keyframe[];
    expect(frames.every((frame) => !("transform" in frame) && !("scale" in frame))).toBe(true);
    view.unmount();
    expect(cancel).toHaveBeenCalledTimes(1);
  });

  it("settles a version off screen without animating it", () => {
    top = 5000;
    const settle = vi.fn();
    render(
      <VersionMotionFrame cellId="v2" motion={{ id: 4, kind: "refresh" }} placed settle={settle}>
        <div data-cell-frame />
      </VersionMotionFrame>
    );
    expect(animate).not.toHaveBeenCalled();
    expect(settle).toHaveBeenCalledWith("v2", 4);
  });
});
