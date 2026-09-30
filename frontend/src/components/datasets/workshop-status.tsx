import { useEffect, useRef, useState } from "react";

import { Icon } from "@/components/ui/icons";
import { cn } from "@/lib/utils";

export type WorkshopStatus = "working" | "complete" | "error" | "review" | "idle";

export function WorkshopStatusIcon({
  state,
  className,
}: {
  state: WorkshopStatus;
  className?: string;
}) {
  const previous = useRef(state);
  const [finished, setFinished] = useState(false);
  useEffect(() => {
    const completed = previous.current === "working" && state === "complete";
    previous.current = state;
    setFinished(completed);
    if (!completed) return;
    const timer = setTimeout(() => setFinished(false), 700);
    return () => clearTimeout(timer);
  }, [state]);
  if (state === "idle") return null;
  const Glyph =
    state === "working" ? Icon.loader : state === "complete" ? Icon.success : Icon.warning;
  return (
    <Glyph
      aria-label={
        {
          complete: "Completed",
          error: "Failed",
          review: "Review needed",
          working: "Working",
        }[state]
      }
      className={cn(
        "size-4 shrink-0",
        state === "working" && "workshop-working text-muted-foreground",
        state === "complete" && "text-success",
        state === "error" && "text-destructive",
        state === "review" && "text-warning",
        finished && "workshop-finished",
        className
      )}
      role="img"
    />
  );
}
