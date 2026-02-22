import { type ClassValue, clsx } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

export function formatDate(iso?: string | null): string {
  if (!iso) return "—";
  try {
    const d = new Date(iso);
    return Number.isNaN(d.getTime())
      ? "—"
      : d.toLocaleDateString(undefined, {
          day: "numeric",
          hour: "2-digit",
          minute: "2-digit",
          month: "short",
          second: "2-digit",
          year: "numeric",
        });
  } catch {
    return "—";
  }
}

export const offsetToNavgiation = ({
  offset,
  limit,
  count,
}: {
  offset: number;
  limit: number;
  count: number;
}) => {
  const page = Math.floor(offset / limit) + 1;
  return {
    page,
    hasPrevious: page > 1,
    hasNext: count > offset + limit,
    nextPage: page + 1,
    previousPage: Math.max(1, page - 1),
    totalPages: Math.ceil(count / limit),
  };
};

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
    page,
    pageSize,
    count,
    total: count,
    startItem: (page - 1) * pageSize + 1,
    endItem: Math.min(page * pageSize, (page - 1) * pageSize + count),
    hasPrevious: page > 1,
    hasNext: count > page * pageSize,
    nextPage: page + 1,
    previousPage: Math.max(1, page - 1),
    totalPages: Math.ceil(count / pageSize),
  };
};
