import type { ComponentProps } from "react";

import { format, formatDistanceToNowStrict, isYesterday } from "date-fns";

import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip";

type DateValue = string | number | Date | null | undefined;

const DAY_MS = 24 * 60 * 60 * 1000;
const EXACT_FORMAT = "d MMM yyyy, HH:mm:ss";

function toDate(value: DateValue): Date | null {
  if (value == null || value === "") return null;
  const date =
    typeof value === "number" && value > 1e15 ? new Date(value / 1_000_000) : new Date(value);
  return Number.isNaN(date.getTime()) ? null : date;
}

/**
 * Display rule for all timestamps in the UI:
 * - within the last 24h (or in the future): exact time and date, e.g. "14:32, 24 Jul"
 * - older: "yesterday", "4 days ago", "2 months ago", …
 */
export function formatSmart(value: DateValue, fallback = "—"): string {
  const date = toDate(value);
  if (!date) return fallback;
  const age = Date.now() - date.getTime();
  if (age < DAY_MS) return format(date, "HH:mm, d MMM");
  if (isYesterday(date)) return "yesterday";
  return formatDistanceToNowStrict(date, { addSuffix: true });
}

type DateTimeProps = Omit<ComponentProps<"time">, "dateTime"> & {
  value: DateValue;
  fallback?: string;
  /** Skip the relative "N days ago" rule and always show the absolute date/time. */
  exact?: boolean;
};

export function DateTime({ value, fallback = "—", exact = false, ...props }: DateTimeProps) {
  const date = toDate(value);
  if (!date) return <span {...props}>{fallback}</span>;
  return (
    <TooltipProvider delayDuration={150}>
      <Tooltip>
        <TooltipTrigger asChild>
          <time dateTime={date.toISOString()} {...props}>
            {exact ? format(date, EXACT_FORMAT) : formatSmart(date)}
          </time>
        </TooltipTrigger>
        <TooltipContent>{format(date, EXACT_FORMAT)}</TooltipContent>
      </Tooltip>
    </TooltipProvider>
  );
}
