import { useState } from "react";

import type { SortState } from "@/components/ui/sortable-header";

type SortValue = string | number | boolean | null | undefined;
const collator = new Intl.Collator(undefined, { numeric: true, sensitivity: "base" });
const missing = (value: SortValue) =>
  value == null || (typeof value === "number" && !Number.isFinite(value));

export function useTableSort<K extends string>(initial: SortState<K> | null = null) {
  const [sort, setSort] = useState(initial);
  const onSort = (key: K) =>
    setSort((current) => ({
      dir: current?.key === key && current.dir === "asc" ? "desc" : "asc",
      key,
    }));
  const sortRows = <T>(rows: readonly T[], value: (row: T, key: K) => SortValue): T[] => {
    if (!sort) return [...rows];
    return [...rows].sort((a, b) => {
      const left = value(a, sort.key);
      const right = value(b, sort.key);
      // Missing measurements stay last in both directions, never becoming zero.
      if (missing(left)) return missing(right) ? 0 : 1;
      if (missing(right)) return -1;
      const order =
        typeof left === "number" && typeof right === "number"
          ? left - right
          : typeof left === "boolean" && typeof right === "boolean"
            ? Number(left) - Number(right)
            : collator.compare(String(left), String(right));
      return sort.dir === "asc" ? order : -order;
    });
  };
  return { onSort, setSort, sort, sortRows };
}
