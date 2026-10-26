const integer = new Intl.NumberFormat("en-GB");
const usd = new Intl.NumberFormat("en-US", { currency: "USD", style: "currency" });
const date = new Intl.DateTimeFormat("en-GB", { day: "numeric", month: "short", year: "numeric" });
const day = new Intl.DateTimeFormat("en-GB", { day: "numeric", month: "short" });

export const formatInt = (value: number) => integer.format(value);
export const formatUsd = (value: string | number) => usd.format(Number(value));
export const formatDate = (iso: string) => date.format(new Date(iso));
export const formatDay = (iso: string) => day.format(new Date(iso));

export function formatAgo(iso: string | null): string {
  if (!iso) return "Never";
  const hours = (Date.now() - new Date(iso).getTime()) / 3_600_000;
  if (hours < 1) return "<1h ago";
  if (hours < 48) return `${Math.floor(hours)}h ago`;
  return `${Math.floor(hours / 24)}d ago`;
}

export const humanize = (key: string) =>
  key.charAt(0).toUpperCase() + key.slice(1).replace(/[_-]+/g, " ");

/** One point per calendar day in the window; the API omits days with no rows. */
export function fillDays<T extends { day: string; value: number | string }>(
  series: T[],
  days: number
): { day: string; value: number }[] {
  const byDay = new Map(series.map((point) => [point.day.slice(0, 10), Number(point.value)]));
  const today = new Date();
  return Array.from({ length: days }, (_, index) => {
    const d = new Date(today);
    d.setUTCDate(today.getUTCDate() - (days - 1 - index));
    const key = d.toISOString().slice(0, 10);
    return { day: key, value: byDay.get(key) ?? 0 };
  });
}

export const sum = (series: { value: number | string }[]) =>
  series.reduce((total, point) => total + Number(point.value), 0);
