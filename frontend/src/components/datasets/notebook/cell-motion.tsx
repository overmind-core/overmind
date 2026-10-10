import { type ReactNode, useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";

import type { Cell } from "@/openapi";

export type CellMotion = { kind: "arrival" | "update"; revision: number };
export type CompleteCellMotion = (id: string, revision: number) => void;

function contentSignature(cell: Cell) {
  return JSON.stringify([
    cell.title,
    cell.script,
    cell.note,
    cell.state,
    cell.error,
    cell.rows,
    cell.columns,
    cell.fingerprint,
    cell.review,
    cell.qualityReport,
    cell.readiness,
  ]);
}

export function useCellMotion(cells: Cell[] | undefined, visibleIds: Set<string>) {
  const previous = useRef<Map<string, string> | null>(null);
  const revision = useRef(0);
  const [motions, setMotions] = useState<Map<string, CellMotion>>(new Map());
  const complete = useCallback<CompleteCellMotion>((id, completedRevision) => {
    setMotions((current) => {
      if (current.get(id)?.revision !== completedRevision) return current;
      const next = new Map(current);
      next.delete(id);
      return next;
    });
  }, []);

  useLayoutEffect(() => {
    if (!cells) return;
    const current = new Map(cells.map((cell) => [cell.id, contentSignature(cell)]));
    const changes = new Map<string, CellMotion>();
    if (previous.current) {
      for (const [id, signature] of current) {
        if (visibleIds.has(id) && previous.current.get(id) !== signature) {
          changes.set(id, {
            kind: previous.current.has(id) ? "update" : "arrival",
            revision: ++revision.current,
          });
        }
      }
    }
    previous.current = current;
    setMotions((existing) => {
      const retained = [...existing].filter(([id]) => current.has(id) && visibleIds.has(id));
      if (!changes.size && retained.length === existing.size) return existing;
      return new Map([...retained, ...changes]);
    });
  }, [cells, visibleIds]);

  useEffect(() => {
    if (!motions.size) return;
    // Unmeasured or filtered-out nodes must not replay stale arrivals later.
    const timeout = window.setTimeout(() => {
      for (const [id, motion] of motions) complete(id, motion.revision);
    }, 2000);
    return () => window.clearTimeout(timeout);
  }, [motions, complete]);

  return { complete, motions };
}

export function CellMotionFrame({
  id,
  motion,
  ready,
  complete,
  children,
}: {
  id: string;
  motion?: CellMotion;
  ready: boolean;
  complete: CompleteCellMotion;
  children: ReactNode;
}) {
  const ref = useRef<HTMLDivElement>(null);
  useLayoutEffect(() => {
    const element = ref.current;
    if (!element || !motion || !ready) return;
    const bounds = element.getBoundingClientRect();
    if (
      document.visibilityState !== "visible" ||
      bounds.bottom <= 0 ||
      bounds.top >= window.innerHeight ||
      bounds.right <= 0 ||
      bounds.left >= window.innerWidth ||
      !element.animate
    ) {
      complete(id, motion.revision);
      return;
    }
    const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    const surface = element.querySelector<HTMLElement>("[data-cell-frame]") ?? element;
    const tokens = getComputedStyle(surface);
    const target = motion.kind === "arrival" ? element : surface;
    const animation = target.animate(
      motion.kind === "arrival"
        ? [{ opacity: reduced ? 0.8 : 0.35 }, { opacity: 1 }]
        : [
            { backgroundColor: tokens.getPropertyValue("--wash-raised").trim() },
            { backgroundColor: tokens.getPropertyValue("--card").trim() },
          ],
      { duration: reduced ? 120 : 500, easing: "cubic-bezier(0.16, 1, 0.3, 1)" }
    );
    void animation.finished.then(
      () => complete(id, motion.revision),
      () => {}
    );
    return () => animation.cancel();
  }, [id, motion, ready, complete]);

  return (
    <div
      className="workshop-cell-node nopan"
      data-cell-motion={ready ? motion?.kind : undefined}
      ref={ref}
      style={{ opacity: motion?.kind === "arrival" && !ready ? 0 : undefined }}
    >
      {children}
    </div>
  );
}
