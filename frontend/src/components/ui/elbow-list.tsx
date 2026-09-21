import { type CSSProperties, type ReactNode, useLayoutEffect, useRef, useState } from "react";

import { Icon, type IconName } from "@/components/ui/icons";
import { cn } from "@/lib/utils";

/** Shared start sits this far above the first row center (matches app-sidebar). */
const ELBOW_START_REM = 0.75;
/** Fallback before the first measure — h-7 + gap-1 → 2rem (sidebar sub-items). */
const ELBOW_ROW_PITCH = "2rem";

export type ElbowItem = {
  id: string;
  icon: IconName;
  label: string;
  running?: boolean;
  failed?: boolean;
  heading?: boolean;
  detail?: ReactNode;
  defaultOpen?: boolean;
  trailing?: ReactNode;
};

/** Trunk height from shared start → this row's center. */
export function elbowHeightPx(firstCenter: number, rowCenter: number, startPadPx: number): number {
  return Math.max(startPadPx, rowCenter - firstCenter + startPadPx);
}

function sameHeights(a: number[], b: number[]): boolean {
  return a.length === b.length && a.every((value, i) => Math.abs(value - b[i]) < 0.5);
}

function ElbowRow({
  item,
  index,
  elbowH,
  elbowRef,
  onExpandToggle,
}: {
  item: ElbowItem;
  index: number;
  elbowH?: number;
  elbowRef: (el: HTMLDivElement | null) => void;
  onExpandToggle: () => void;
}) {
  const [userOpen, setUserOpen] = useState<boolean | null>(null);
  const open = userOpen ?? item.defaultOpen ?? false;
  const expandable = Boolean(item.detail);
  const showDetail = expandable && open;
  const Glyph = Icon[item.icon];
  const Chevron = open ? Icon.chevronUp : Icon.chevronDown;

  const handleToggle = () => {
    setUserOpen(!open);
    onExpandToggle();
  };

  return (
    <li className="motion-safe:animate-in motion-safe:fade-in-0 motion-safe:slide-in-from-bottom-1 motion-safe:duration-200">
      <div
        className={cn("sidebar-elbow relative h-7", item.running && "z-10")}
        ref={elbowRef}
        style={
          {
            "--elbow-color": item.running ? "var(--foreground)" : "var(--border)",
            "--elbow-h":
              elbowH != null
                ? `${elbowH}px`
                : `calc(${index} * ${ELBOW_ROW_PITCH} + ${ELBOW_START_REM}rem)`,
          } as CSSProperties
        }
      >
        <div className="flex h-7 w-full items-center gap-2 px-2">
          {expandable ? (
            <button
              aria-expanded={open}
              className="group flex min-w-0 flex-1 items-center gap-2 text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/50"
              onClick={handleToggle}
              type="button"
            >
              <span className="flex size-3.5 shrink-0 items-center justify-center">
                <Glyph
                  aria-hidden="true"
                  className={cn(
                    "size-3.5",
                    item.failed
                      ? "text-destructive"
                      : item.running || item.heading
                        ? "text-foreground"
                        : "text-muted-foreground"
                  )}
                />
              </span>
              <span
                className={cn(
                  "pixel-label min-w-0 truncate text-xs transition-colors group-hover:text-foreground",
                  item.running
                    ? "text-foreground motion-safe:animate-pulse"
                    : item.failed
                      ? "text-destructive"
                      : item.heading
                        ? "text-foreground"
                        : "text-muted-foreground"
                )}
              >
                {item.label}
              </span>
              <Chevron aria-hidden="true" className="size-3 shrink-0 text-muted-foreground/70" />
            </button>
          ) : (
            <>
              <span className="flex size-3.5 shrink-0 items-center justify-center">
                <Glyph
                  aria-hidden="true"
                  className={cn(
                    "size-3.5",
                    item.failed
                      ? "text-destructive"
                      : item.running || item.heading
                        ? "text-foreground"
                        : "text-muted-foreground"
                  )}
                />
              </span>
              <span
                className={cn(
                  "pixel-label min-w-0 flex-1 truncate text-xs",
                  item.running
                    ? "text-foreground motion-safe:animate-pulse"
                    : item.failed
                      ? "text-destructive"
                      : item.heading
                        ? "text-foreground"
                        : "text-muted-foreground"
                )}
              >
                {item.label}
              </span>
            </>
          )}
          {item.trailing}
        </div>
      </div>
      {showDetail ? (
        <div className="pb-1.5 pl-1 motion-safe:animate-in motion-safe:fade-in-0 motion-safe:duration-150">
          {item.detail}
        </div>
      ) : null}
    </li>
  );
}

export function ElbowGroupHeading({
  icon = "list",
  label,
  running,
  failed,
  trailing,
}: {
  icon?: IconName;
  label: string;
  running?: boolean;
  failed?: boolean;
  trailing?: ReactNode;
}) {
  const Glyph = Icon[icon];
  return (
    <div className="flex h-7 items-center gap-2 px-2">
      <span className="flex size-3.5 shrink-0 items-center justify-center">
        <Glyph
          aria-hidden="true"
          className={cn(
            "size-3.5",
            failed ? "text-destructive" : running ? "text-foreground" : "text-muted-foreground"
          )}
        />
      </span>
      <span
        className={cn(
          "pixel-label min-w-0 flex-1 truncate text-xs",
          running
            ? "text-foreground motion-safe:animate-pulse"
            : failed
              ? "text-destructive"
              : "text-foreground"
        )}
      >
        {label}
      </span>
      {trailing}
    </div>
  );
}

export function ElbowList({
  items,
  isStreaming = false,
  className,
  ariaLabel,
}: {
  items: ElbowItem[];
  isStreaming?: boolean;
  className?: string;
  ariaLabel?: string;
}) {
  const listRef = useRef<HTMLOListElement>(null);
  const elbowRefs = useRef<(HTMLDivElement | null)[]>([]);
  const measureRef = useRef<() => void>(() => {});
  const [elbowHs, setElbowHs] = useState<number[]>([]);
  const [layoutGen, setLayoutGen] = useState(0);

  useLayoutEffect(() => {
    const list = listRef.current;
    if (!list || items.length === 0) return;

    const measure = () => {
      const elbows = elbowRefs.current.slice(0, items.length);
      const first = elbows[0];
      if (!first) return;
      const rem = parseFloat(getComputedStyle(list).fontSize) || 16;
      const startPad = ELBOW_START_REM * rem;
      const firstRect = first.getBoundingClientRect();
      const firstCenter = firstRect.top + firstRect.height / 2;
      const next = elbows.map((el) => {
        if (!el) return startPad;
        const rect = el.getBoundingClientRect();
        return elbowHeightPx(firstCenter, rect.top + rect.height / 2, startPad);
      });
      for (let i = 0; i < next.length; i++) {
        elbows[i]?.style.setProperty("--elbow-h", `${next[i]}px`);
      }
      setElbowHs((prev) => (sameHeights(prev, next) ? prev : next));
    };

    measureRef.current = measure;
    measure();

    let raf = 0;
    const observer = new ResizeObserver(() => {
      cancelAnimationFrame(raf);
      raf = requestAnimationFrame(() => {
        measureRef.current();
      });
    });
    observer.observe(list);
    for (const el of elbowRefs.current.slice(0, items.length)) {
      const item = el?.parentElement;
      if (item) observer.observe(item);
    }
    return () => {
      cancelAnimationFrame(raf);
      observer.disconnect();
    };
  }, [items]);

  useLayoutEffect(() => {
    if (layoutGen === 0) return;
    measureRef.current();
  }, [layoutGen]);

  if (items.length === 0) return null;

  return (
    <ol
      aria-label={ariaLabel}
      // ml-[15px]: elbow ::before sits at left:-10px with a 2px trunk, so the
      // trunk center lands at 6px — the tip of the size-3 header chevron.
      className={cn("ml-[15px] flex list-none flex-col gap-1", className)}
      ref={listRef}
      role={isStreaming ? "status" : undefined}
    >
      {items.map((item, index) => (
        <ElbowRow
          elbowH={elbowHs[index]}
          elbowRef={(el) => {
            elbowRefs.current[index] = el;
          }}
          index={index}
          item={item}
          key={item.id}
          onExpandToggle={() => setLayoutGen((n) => n + 1)}
        />
      ))}
    </ol>
  );
}
