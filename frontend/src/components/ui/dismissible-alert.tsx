import { useEffect, useRef, useState } from "react";

import type { VariantProps } from "class-variance-authority";

import { alertVariants } from "@/components/ui/alert";
import { Icon } from "@/components/ui/icons";
import { cn } from "@/lib/utils";

const AUTO_DISMISS_MS = 5_000;

interface DismissibleAlertProps extends VariantProps<typeof alertVariants> {
  /** A new Error reference on each failure resets the timer and re-shows the alert. */
  error?: Error | null;
  message?: string | null;
  /** Increment to re-trigger the alert when `message` itself hasn't changed. */
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

  // `messageKey === 0` means "not yet triggered", so a caller's initial mount doesn't
  // show the alert before any action.
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
  }, [message, messageKey, error]);

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
        className="shrink-0 rounded-sm opacity-60 transition-opacity hover:opacity-100 focus:outline-none focus:ring-1 focus:ring-current"
        onClick={() => setVisible(false)}
        type="button"
      >
        <Icon.close className="size-4" />
      </button>
    </div>
  );
}
