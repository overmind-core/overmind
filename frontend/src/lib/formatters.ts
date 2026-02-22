/**
 * Utility functions for formatting data throughout the application.
 */

export function formatTimestamp(timestamp: string | number | null | undefined): string {
  if (timestamp == null) return "";

  try {
    const date =
      typeof timestamp === "number"
        ? timestamp > 1e15
          ? new Date(timestamp / 1_000_000)
          : new Date(timestamp)
        : new Date(timestamp);

    if (Number.isNaN(date.getTime())) return String(timestamp);

    return date.toLocaleString(undefined, {
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
      month: "short",
      second: "2-digit",
      year: "numeric",
    });
  } catch {
    return String(timestamp);
  }
}

export function formatPercentage(value: number | null | undefined, decimals = 1): string {
  if (value == null) return "N/A";
  return `${(value * 100).toFixed(decimals)}%`;
}

export function formatNumber(value: number | null | undefined): string {
  if (value == null) return "N/A";
  return value.toLocaleString("en-US");
}

export function formatDuration(ms: number | null | undefined): string {
  if (ms == null) return "N/A";

  if (ms < 1000) {
    return `${ms}ms`;
  }
  if (ms < 60_000) {
    return `${(ms / 1000).toFixed(2)}s`;
  }
  const minutes = Math.floor(ms / 60_000);
  const seconds = ((ms % 60_000) / 1000).toFixed(1);
  return `${minutes}m ${seconds}s`;
}

export function truncateText(text: string | null | undefined, maxLength = 100): string {
  if (!text) return "";
  if (text.length <= maxLength) return text;
  return `${text.slice(0, maxLength)}...`;
}

export function formatCost(value: number | string | null | undefined): string {
  if (value == null) return "—";
  const n = typeof value === "string" ? parseFloat(value) : value;
  if (Number.isNaN(n)) return "—";
  if (n < 0.0001) return n.toExponential(2);
  if (n < 1) return `$${n.toFixed(4)}`;
  return `$${n.toFixed(2)}`;
}

export function getTimeRangeStartTimestamp(timeFilter: string): string {
  const now = Date.now();

  switch (timeFilter) {
    case "all":
      return new Date("1996-01-01T00:00:00.000Z").toISOString();
    case "past5m":
      return new Date(now - 5 * 60 * 1000).toISOString();
    case "past1h":
      return new Date(now - 60 * 60 * 1000).toISOString();
    case "past24h":
      return new Date(now - 24 * 60 * 60 * 1000).toISOString();
    case "past7d":
      return new Date(now - 7 * 24 * 60 * 60 * 1000).toISOString();
    default:
      return new Date(now - 30 * 24 * 60 * 60 * 1000).toISOString();
  }
}
