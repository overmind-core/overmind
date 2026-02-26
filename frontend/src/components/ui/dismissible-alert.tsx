import { useEffect, useRef, useState } from "react";

import { X } from "lucide-react";
import type { VariantProps } from "class-variance-authority";

import { cn } from "@/lib/utils";
import { alertVariants } from "@/components/ui/alert";

const AUTO_DISMISS_MS = 5_000;

interface DismissibleAlertProps extends VariantProps<typeof alertVariants> {
  /** Pass the Error object from the mutation. A new object reference on each failure
   *  resets the timer and re-shows the alert automatically. */
  error?: Error | null;
  /** Pass a non-error message (e.g. success/info text).
   *  Pair with an incrementing ``messageKey`` to re-show the same text on
   *  repeated triggers (since the string value itself won't change). */
  message?: string | null;
  /** Increment this value each time you want to re-trigger the alert even
   *  when ``message`` hasn't changed (e.g. the user fires the same action twice). */
  messageKey?: number;
  fallback?: string;
  className?: string;
}

export function DismissibleAlert({
  error,
  message,
  messageKey,
  fallback = "Something went wrong",
  variant,
  className,
}: DismissibleAlertProps) {
  const [visible, setVisible] = useState(false);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Error path — existing behaviour, driven by the Error object reference
  useEffect(() => {
    if (!error) {
      setVisible(false);
      return;
    }
    setVisible(true);
    if (timerRef.current) clearTimeout(timerRef.current);
    timerRef.current = setTimeout(() => setVisible(false), AUTO_DISMISS_MS);
    return () => {
      if (timerRef.current) clearTimeout(timerRef.current);
    };
  }, [error]);

  // Message path — success / info, driven by message text + an optional counter
  // so callers can re-trigger even when the message string stays the same.
  // When messageKey is provided, treat 0 as "not yet triggered" so the alert
  // doesn't appear on initial mount before any action has been taken.
  useEffect(() => {
    if (!message || (messageKey !== undefined && messageKey === 0)) {
      if (!error) setVisible(false);
      return;
    }
    setVisible(true);
    if (timerRef.current) clearTimeout(timerRef.current);
    timerRef.current = setTimeout(() => setVisible(false), AUTO_DISMISS_MS);
    return () => {
      if (timerRef.current) clearTimeout(timerRef.current);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [message, messageKey]);

  const text = error ? error.message || fallback : message;

  if (!visible || !text) return null;

  return (
    <div
      className={cn(
        alertVariants({ variant }),
        "flex items-start justify-between gap-2",
        className
      )}
      data-slot="alert"
      role="alert"
    >
      <span className="flex-1 leading-snug">{text}</span>
      <button
        aria-label="Dismiss"
        className="shrink-0 rounded opacity-60 transition-opacity hover:opacity-100 focus:outline-none focus:ring-1 focus:ring-current"
        onClick={() => setVisible(false)}
        type="button"
      >
        <X className="size-4" />
      </button>
    </div>
  );
}
