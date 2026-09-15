import { type ClassValue, clsx } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

// `value` is a 0–1 fraction; the clamp stops a legacy out-of-range score from
// rendering as something like 5991%.
export const scorePct = (value: number): number =>
  Math.max(0, Math.min(100, Math.round(value * 100)));

export const paginationFromPageLimit = ({
  page,
  pageSize,
  count,
}: {
  page: number;
  pageSize: number;
  count: number;
}) => {
  return {
    count,
    endItem: Math.min(page * pageSize, (page - 1) * pageSize + count),
    hasNext: count > page * pageSize,
    hasPrevious: page > 1,
    nextPage: page + 1,
    page,
    pageSize,
    previousPage: Math.max(1, page - 1),
    startItem: (page - 1) * pageSize + 1,
    total: count,
    totalPages: Math.ceil(count / pageSize),
  };
};

export type PaginationItem = number | "ellipsis";

/** 1-based. Keeps the first and last page plus a `siblings`-wide window around
 *  the current one, collapsing each gap to a single "ellipsis". */
export const paginationItems = (
  current: number,
  totalPages: number,
  siblings = 1
): PaginationItem[] => {
  const range = (start: number, end: number): number[] =>
    Array.from({ length: Math.max(0, end - start + 1) }, (_, i) => start + i);

  // first + last + current + 2 siblings + 2 ellipsis slots
  const maxVisible = siblings * 2 + 5;
  if (totalPages <= maxVisible) return range(1, totalPages);

  const left = Math.max(current - siblings, 1);
  const right = Math.min(current + siblings, totalPages);
  const showLeftDots = left > 3;
  const showRightDots = right < totalPages - 2;
  const edgeCount = 3 + 2 * siblings;

  if (!showLeftDots && showRightDots) {
    return [...range(1, edgeCount), "ellipsis", totalPages];
  }
  if (showLeftDots && !showRightDots) {
    return [1, "ellipsis", ...range(totalPages - edgeCount + 1, totalPages)];
  }
  return [1, "ellipsis", ...range(left, right), "ellipsis", totalPages];
};

export function usdToOvermindCredits(usd: number): number {
  return Math.round(usd / 0.01);
}

export function usdToOvermindCreditsWithLabel(usd: number): string {
  return `${usdToOvermindCredits(usd)} credits`;
}

export const plural = (count: number, noun: string): string =>
  `${count} ${noun}${count === 1 ? "" : "s"}`;
