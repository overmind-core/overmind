export function cycleDrfOrdering(current: string | undefined, field: string): string | undefined {
  if (current === field) return `-${field}`;
  if (current === `-${field}`) return undefined;
  return field;
}

export function parseDrfOrdering(
  ordering: string | undefined
): { field: string; dir: "asc" | "desc" } | null {
  if (!ordering) return null;
  if (ordering.startsWith("-")) return { dir: "desc", field: ordering.slice(1) };
  return { dir: "asc", field: ordering };
}
