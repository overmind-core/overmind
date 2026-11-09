"use client";

import type * as React from "react";

import { resolveSafeNavigationHref } from "../shared/media";
import { Badge, cn, Tooltip, TooltipContent, TooltipTrigger } from "./_adapter";

type Tone = "success" | "warning" | "danger" | "info" | "neutral";

export type FormatConfig =
  | { kind: "text" }
  | {
      kind: "number";
      decimals?: number;
      unit?: string;
      compact?: boolean;
      showSign?: boolean;
    }
  | { kind: "currency"; currency: string; decimals?: number }
  | {
      kind: "percent";
      decimals?: number;
      showSign?: boolean;
      basis?: "fraction" | "unit";
    }
  | { kind: "date"; dateFormat?: "short" | "long" | "relative" }
  | {
      kind: "delta";
      decimals?: number;
      upIsPositive?: boolean;
      showSign?: boolean;
    }
  | {
      kind: "status";
      statusMap: Record<string, { tone: Tone; label?: string }>;
    }
  | { kind: "boolean"; labels?: { true: string; false: string } }
  | { kind: "link"; hrefKey?: string; external?: boolean }
  | { kind: "badge"; colorMap?: Record<string, Tone> }
  | { kind: "array"; maxVisible?: number };

interface DeltaValueProps {
  value: number;
  options?: Extract<FormatConfig, { kind: "delta" }>;
  locale?: string;
}

export function DeltaValue({ value, options, locale }: DeltaValueProps) {
  const decimals = options?.decimals ?? 2;
  const upIsPositive = options?.upIsPositive ?? true;
  const showSign = options?.showSign ?? true;

  const isPositive = value > 0;
  const isNegative = value < 0;
  const isNeutral = value === 0;

  const isGood = upIsPositive ? isPositive : isNegative;
  const isBad = upIsPositive ? isNegative : isPositive;

  const colorClass = isGood ? "text-success" : isBad ? "text-error" : "text-muted-foreground";

  const absValue = Math.abs(value);
  const formatted = new Intl.NumberFormat(locale, {
    maximumFractionDigits: decimals,
    minimumFractionDigits: decimals,
  }).format(absValue);

  const display =
    showSign && !isNeutral ? (isNegative ? `-${formatted}` : `+${formatted}`) : formatted;

  const arrow = isPositive ? "↑" : isNegative ? "↓" : "";

  return (
    <span className={cn("tabular-nums", colorClass)}>
      {display}
      {!isNeutral && <span className="ml-0.5">{arrow}</span>}
    </span>
  );
}

interface StatusBadgeProps {
  value: string;
  options?: Extract<FormatConfig, { kind: "status" }>;
}

export function StatusBadge({ value, options }: StatusBadgeProps) {
  const config = options?.statusMap?.[value] ?? {
    label: value,
    tone: "neutral" as Tone,
  };
  const label = config.label ?? value;

  const variant =
    config.tone === "danger" ? "destructive" : config.tone === "neutral" ? "outline" : "secondary";

  return (
    <Badge
      className={cn(
        "border",
        config.tone === "warning" && "bg-warning/10 text-warning",
        config.tone === "success" && "bg-success/10 text-success",
        config.tone === "info" && "bg-info/10 text-info",
        config.tone === "danger" && "bg-error/10 text-error"
      )}
      variant={variant}
    >
      {label}
    </Badge>
  );
}

interface CurrencyValueProps {
  value: number;
  options?: Extract<FormatConfig, { kind: "currency" }>;
  locale?: string;
}

export function CurrencyValue({ value, options, locale }: CurrencyValueProps) {
  const currency = options?.currency ?? "USD";
  const decimals = options?.decimals ?? 2;

  const formatted = new Intl.NumberFormat(locale, {
    currency,
    maximumFractionDigits: decimals,
    minimumFractionDigits: decimals,
    style: "currency",
  }).format(value);

  return <span className="tabular-nums">{formatted}</span>;
}

interface PercentValueProps {
  value: number;
  options?: Extract<FormatConfig, { kind: "percent" }>;
  locale?: string;
}

export function PercentValue({ value, options, locale }: PercentValueProps) {
  const decimals = options?.decimals ?? 2;
  const showSign = options?.showSign ?? false;
  const basis = options?.basis ?? "fraction";

  const numeric = basis === "fraction" ? value : value / 100;

  const formatted = new Intl.NumberFormat(locale, {
    maximumFractionDigits: decimals,
    minimumFractionDigits: decimals,
    signDisplay: showSign ? "always" : "auto",
    style: "percent",
  }).format(numeric);

  return <span className="tabular-nums">{formatted}</span>;
}

interface DateValueProps {
  value: string;
  options?: Extract<FormatConfig, { kind: "date" }>;
  locale?: string;
}

export function DateValue({ value, options, locale }: DateValueProps) {
  const dateFormat = options?.dateFormat ?? "short";
  const date = new Date(value);

  if (Number.isNaN(date.getTime())) {
    return <span className="text-muted-foreground">{value}</span>;
  }

  let formatted: string;

  if (dateFormat === "relative") {
    formatted = getRelativeTime(date, locale);
  } else if (dateFormat === "long") {
    formatted = new Intl.DateTimeFormat(locale, {
      day: "numeric",
      month: "long",
      year: "numeric",
    }).format(date);
  } else {
    formatted = new Intl.DateTimeFormat(locale, {
      day: "numeric",
      month: "short",
      year: "numeric",
    }).format(date);
  }

  const title = new Intl.DateTimeFormat(locale, {
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    month: "long",
    year: "numeric",
  }).format(date);

  return (
    <span className="tabular-nums" title={title}>
      {formatted}
    </span>
  );
}

function getRelativeTime(date: Date, locale?: string): string {
  const now = new Date();
  const diffInSeconds = Math.trunc((date.getTime() - now.getTime()) / 1000);
  const absDiffInSeconds = Math.abs(diffInSeconds);

  if (absDiffInSeconds < 60) return "just now";

  const rtf = new Intl.RelativeTimeFormat(locale, { numeric: "auto" });

  if (absDiffInSeconds < 3600) {
    const mins = Math.trunc(diffInSeconds / 60);
    return rtf.format(mins, "minute");
  }
  if (absDiffInSeconds < 86400) {
    const hours = Math.trunc(diffInSeconds / 3600);
    return rtf.format(hours, "hour");
  }
  if (absDiffInSeconds < 604800) {
    const days = Math.trunc(diffInSeconds / 86400);
    return rtf.format(days, "day");
  }

  return new Intl.DateTimeFormat(locale, {
    day: "numeric",
    month: "short",
    year: "numeric",
  }).format(date);
}

interface BooleanValueProps {
  value: boolean;
  options?: Extract<FormatConfig, { kind: "boolean" }>;
}

export function BooleanValue({ value, options }: BooleanValueProps) {
  const labels = options?.labels ?? { false: "No", true: "Yes" };
  const label = value ? labels.true : labels.false;
  const variant = value ? "secondary" : "outline";

  return <Badge variant={variant}>{label}</Badge>;
}

interface LinkValueProps {
  value: string;
  options?: Extract<FormatConfig, { kind: "link" }>;
  row?: Record<string, string | number | boolean | null | (string | number | boolean | null)[]>;
}

export function LinkValue({ value, options, row }: LinkValueProps) {
  const rawHref = options?.hrefKey && row ? String(row[options.hrefKey] ?? "") : value;
  const href = resolveSafeNavigationHref(rawHref);
  const external = options?.external ?? false;

  if (!href) {
    return <span>{value}</span>;
  }

  return (
    <a
      aria-label={external ? `${value} (opens in a new tab)` : undefined}
      className="text-accent-foreground inline-block max-w-full break-words underline underline-offset-2 hover:opacity-90"
      href={href}
      onClick={(e) => e.stopPropagation()}
      rel={external ? "noopener noreferrer" : undefined}
      target={external ? "_blank" : undefined}
    >
      {value}
      {external && (
        <span aria-label="Opens in new tab" className="ml-1 inline-block">
          ↗
        </span>
      )}
    </a>
  );
}

interface NumberValueProps {
  value: number;
  options?: Extract<FormatConfig, { kind: "number" }>;
  locale?: string;
}

export function NumberValue({ value, options, locale }: NumberValueProps) {
  const decimals = options?.decimals ?? 0;
  const unit = options?.unit ?? "";
  const compact = options?.compact ?? false;
  const showSign = options?.showSign ?? false;

  const formatted = new Intl.NumberFormat(locale, {
    maximumFractionDigits: decimals,
    minimumFractionDigits: decimals,
    notation: compact ? "compact" : "standard",
  }).format(value);

  const display = showSign && value > 0 ? `+${formatted}` : formatted;

  return (
    <span className="tabular-nums">
      {display}
      {unit}
    </span>
  );
}

interface BadgeValueProps {
  value: string;
  options?: Extract<FormatConfig, { kind: "badge" }>;
}

export function BadgeValue({ value, options }: BadgeValueProps) {
  const tone = options?.colorMap?.[value] ?? "neutral";

  const variant = tone === "danger" ? "destructive" : tone === "neutral" ? "outline" : "secondary";

  return (
    <Badge
      className={cn(
        "border",
        tone === "warning" && "bg-warning/10 text-warning",
        tone === "success" && "bg-success/10 text-success",
        tone === "info" && "bg-info/10 text-info",
        tone === "danger" && "bg-error/10 text-error"
      )}
      variant={variant}
    >
      {value}
    </Badge>
  );
}

interface ArrayValueProps {
  value: (string | number | boolean | null)[] | string;
  options?: Extract<FormatConfig, { kind: "array" }>;
}

export function ArrayValue({ value, options }: ArrayValueProps) {
  const maxVisible = options?.maxVisible ?? 3;
  const items: (string | number | boolean | null)[] = Array.isArray(value)
    ? value
    : typeof value === "string"
      ? value.split(",").map((s) => s.trim())
      : [];

  if (items.length === 0) {
    return <span className="text-muted-foreground">—</span>;
  }

  const visible = items.slice(0, maxVisible);
  const remaining = items.length - maxVisible;

  const hidden = items.slice(maxVisible);

  return (
    <span className="inline-flex flex-wrap items-center gap-1">
      {visible.map((item, i) => (
        <span
          className="bg-muted text-muted-foreground inline-flex items-center rounded-md px-2 py-0.5"
          key={i}
        >
          {item === null ? "null" : String(item)}
        </span>
      ))}
      {remaining > 0 && (
        <Tooltip>
          <TooltipTrigger asChild>
            <span className="text-muted-foreground cursor-default">+{remaining} more</span>
          </TooltipTrigger>
          <TooltipContent>
            {hidden.map((item) => (item === null ? "null" : String(item))).join(", ")}
          </TooltipContent>
        </Tooltip>
      )}
    </span>
  );
}

interface RenderFormattedValueParams {
  value: string | number | boolean | null | (string | number | boolean | null)[];
  column: { format?: FormatConfig };
  row?: Record<string, string | number | boolean | null | (string | number | boolean | null)[]>;
  locale?: string;
}

export function renderFormattedValue({
  value,
  column,
  row,
  locale,
}: RenderFormattedValueParams): React.ReactNode {
  if (value == null || value === "") {
    return <span className="text-muted-foreground">—</span>;
  }

  const fmt = column.format;

  switch (fmt?.kind) {
    case "delta":
      return <DeltaValue locale={locale} options={fmt} value={Number(value)} />;
    case "status":
      return <StatusBadge options={fmt} value={String(value)} />;
    case "currency":
      return <CurrencyValue locale={locale} options={fmt} value={Number(value)} />;
    case "percent":
      return <PercentValue locale={locale} options={fmt} value={Number(value)} />;
    case "date":
      return <DateValue locale={locale} options={fmt} value={String(value)} />;
    case "boolean":
      return <BooleanValue options={fmt} value={Boolean(value)} />;
    case "link":
      return <LinkValue options={fmt} row={row} value={String(value)} />;
    case "number":
      return <NumberValue locale={locale} options={fmt} value={Number(value)} />;
    case "badge":
      return <BadgeValue options={fmt} value={String(value)} />;
    case "array":
      return <ArrayValue options={fmt} value={Array.isArray(value) ? value : String(value)} />;
    default:
      return String(value);
  }
}
