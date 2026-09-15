import type * as React from "react";
import { useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import { CountChip } from "@/components/ui/count-chip";
import { Icon } from "@/components/ui/icons";
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip";
import { cn } from "@/lib/utils";

export interface SelectionBarProps {
  /** The bar renders nothing at 0. */
  count: number;
  countLabel: string;
  /** Accessible name for the `role="status"` region. */
  regionLabel: string;
  onClear: () => void;
  clearLabel?: string;
  /** "viewport" floats at the bottom of the screen, independent of any scrolling
   *  ancestor; "container" centres within the nearest `relative` ancestor. */
  anchor: "viewport" | "container";
  afterCount?: React.ReactNode;
  children?: React.ReactNode;
  className?: string;
}

const FADE_MS = 200;

export function SelectionBar({
  count,
  countLabel,
  regionLabel,
  onClear,
  clearLabel = "Clear selection",
  anchor,
  afterCount,
  children,
  className,
}: SelectionBarProps) {
  const active = count > 0;
  const [rendered, setRendered] = useState(active);

  useEffect(() => {
    if (active) {
      setRendered(true);
      return;
    }
    const reducedMotion =
      typeof window !== "undefined" &&
      window.matchMedia?.("(prefers-reduced-motion: reduce)").matches === true;
    if (reducedMotion) {
      setRendered(false);
      return;
    }
    const t = setTimeout(() => setRendered(false), FADE_MS);
    return () => clearTimeout(t);
  }, [active]);

  if (!rendered) return null;

  return (
    <div
      className={cn(
        "pointer-events-none flex justify-center px-4",
        anchor === "viewport" ? "fixed inset-x-0 bottom-6 z-30" : "absolute inset-x-0 bottom-3 z-20"
      )}
    >
      <div
        aria-label={regionLabel}
        aria-live="polite"
        className={cn(
          "pointer-events-auto flex items-center gap-1 rounded-md border p-1 duration-200 motion-reduce:animate-none",
          active ? "animate-in fade-in-0" : "animate-out fade-out-0",
          "border-border bg-popover/90",
          className
        )}
        role="status"
      >
        <div className="flex items-center gap-2 py-1 pl-2 pr-1">
          <CountChip count={count} />
          <span className="whitespace-nowrap text-sm text-muted-foreground">{countLabel}</span>
          {afterCount}
        </div>

        <div className="flex items-center gap-1 pr-0.5">
          {children}
          <TooltipProvider>
            <Tooltip>
              <TooltipTrigger asChild>
                <Button aria-label={clearLabel} onClick={onClear} size="icon-sm" variant="ghost">
                  <Icon.close />
                </Button>
              </TooltipTrigger>
              <TooltipContent className="text-xs" side="top">
                {clearLabel}
              </TooltipContent>
            </Tooltip>
          </TooltipProvider>
        </div>
      </div>
    </div>
  );
}
