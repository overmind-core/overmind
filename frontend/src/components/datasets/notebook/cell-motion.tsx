import { type ReactNode, useCallback, useEffect, useRef, useState } from "react";

import { cn } from "@/lib/utils";
import type { Cell } from "@/openapi";

export interface Motion {
  id: number;
  kind: "arrival" | "refresh";
}

const UNPLAYED_MS = 2000;

const signature = (cell: Cell) =>
  JSON.stringify([cell.impact, cell.intentReport, cell.capabilityReport, cell.stats]);

export function useVersionMotion(cells: Cell[] | undefined) {
  const seen = useRef<Map<string, string> | null>(null);
  const counter = useRef(0);
  const timers = useRef(new Set<ReturnType<typeof setTimeout>>());
  const [motions, setMotions] = useState<Map<string, Motion>>(() => new Map());

  const settle = useCallback((cellId: string, motionId: number) => {
    setMotions((current) => {
      if (current.get(cellId)?.id !== motionId) return current;
      const next = new Map(current);
      next.delete(cellId);
      return next;
    });
  }, []);

  useEffect(() => {
    if (!cells) return;
    const now = new Map(cells.map((cell) => [cell.id, signature(cell)]));
    const before = seen.current;
    seen.current = now;
    if (!before) return;
    const fresh: Array<[string, Motion]> = [];
    for (const [id, sig] of now) {
      const previous = before.get(id);
      if (previous === undefined) fresh.push([id, { id: ++counter.current, kind: "arrival" }]);
      else if (previous !== sig) fresh.push([id, { id: ++counter.current, kind: "refresh" }]);
    }
    if (!fresh.length) return;
    setMotions((current) => {
      const next = new Map(current);
      for (const [id, motion] of fresh) next.set(id, motion);
      return next;
    });
    const timer = setTimeout(() => {
      timers.current.delete(timer);
      for (const [id, motion] of fresh) settle(id, motion.id);
    }, UNPLAYED_MS);
    timers.current.add(timer);
  }, [cells, settle]);

  useEffect(() => {
    const pending = timers.current;
    return () => {
      for (const timer of pending) clearTimeout(timer);
    };
  }, []);

  return { motions, settle };
}

const token = (name: string) =>
  getComputedStyle(document.documentElement).getPropertyValue(name).trim();

function onScreen(element: Element) {
  const box = element.getBoundingClientRect();
  return (
    document.visibilityState === "visible" &&
    box.bottom > 0 &&
    box.top < window.innerHeight &&
    box.right > 0 &&
    box.left < window.innerWidth
  );
}

export function VersionMotionFrame({
  cellId,
  motion,
  placed,
  settle,
  children,
}: {
  cellId: string;
  motion: Motion | undefined;
  placed: boolean;
  settle: (cellId: string, motionId: number) => void;
  children: ReactNode;
}) {
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const frame = ref.current;
    if (!motion || !placed || !frame) return;
    if (!onScreen(frame)) {
      settle(cellId, motion.id);
      return;
    }
    const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    const surface = frame.querySelector<HTMLElement>("[data-cell-frame]") ?? frame;
    const animation =
      motion.kind === "arrival"
        ? frame.animate([{ opacity: reduced ? 0.8 : 0.35 }, { opacity: 1 }], {
            duration: reduced ? 120 : 500,
            easing: "cubic-bezier(0.16, 1, 0.3, 1)",
          })
        : surface.animate(
            [{ backgroundColor: token("--wash-raised") }, { backgroundColor: token("--card") }],
            { duration: reduced ? 120 : 500, easing: "cubic-bezier(0.16, 1, 0.3, 1)" }
          );
    let finished = false;
    animation.finished
      .then(() => {
        finished = true;
        settle(cellId, motion.id);
      })
      .catch(() => {});
    return () => {
      if (!finished) animation.cancel();
    };
  }, [cellId, motion, placed, settle]);

  return (
    <div className={cn(motion?.kind === "arrival" && !placed && "opacity-0")} ref={ref}>
      {children}
    </div>
  );
}
