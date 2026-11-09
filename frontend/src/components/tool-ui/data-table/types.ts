import type { ToolUIId, ToolUIReceipt, ToolUIRole } from "../shared/schema";
import type { FormatConfig } from "./formatters";

type JsonPrimitive = string | number | boolean | null;

export type RowPrimitive = JsonPrimitive | JsonPrimitive[];
export type DataTableRowData = Record<string, RowPrimitive>;
export type RowData = Record<string, unknown>;
export type ColumnKey<T extends object> = Extract<keyof T, string>;

export type FormatFor<V> = V extends number
  ? Extract<FormatConfig, { kind: "number" | "currency" | "percent" | "delta" }>
  : V extends boolean
    ? Extract<FormatConfig, { kind: "boolean" | "status" | "badge" }>
    : V extends (string | number | boolean | null)[]
      ? Extract<FormatConfig, { kind: "array" }>
      : V extends string
        ? Extract<FormatConfig, { kind: "text" | "link" | "date" | "badge" | "status" }>
        : Extract<FormatConfig, { kind: "text" }>;

export interface Column<
  T extends object = DataTableRowData,
  K extends ColumnKey<T> = ColumnKey<T>,
> {
  key: K;

  label: string;

  abbr?: string;

  sortable?: boolean;

  align?: "left" | "right" | "center";

  width?: string;

  truncate?: boolean;

  priority?: "primary" | "secondary" | "tertiary";

  hideOnMobile?: boolean;

  format?: FormatFor<T[K]>;
}

export interface DataTableSerializableProps<T extends object = RowData> {
  id: ToolUIId;

  role?: ToolUIRole;

  receipt?: ToolUIReceipt;

  columns: Column<T>[];

  data: T[];

  rowIdKey?: ColumnKey<T>;

  defaultSort?: { by?: ColumnKey<T>; direction?: "asc" | "desc" };

  sort?: { by?: ColumnKey<T>; direction?: "asc" | "desc" };

  emptyMessage?: string;

  maxHeight?: string;

  locale?: string;
}

export interface DataTableClientProps<T extends object = RowData> {
  className?: string;

  onSortChange?: (next: { by?: ColumnKey<T>; direction?: "asc" | "desc" }) => void;
}

export interface DataTableProps<T extends object = RowData>
  extends DataTableSerializableProps<T>,
    DataTableClientProps<T> {}

export interface DataTableContextValue<T extends object = RowData> {
  columns: Column<T>[];
  data: T[];
  rowIdKey?: ColumnKey<T>;
  sortBy?: ColumnKey<T>;
  sortDirection?: "asc" | "desc";
  toggleSort?: (key: ColumnKey<T>) => void;
  id?: string;
  locale?: string;
}
