import { Link } from "@tanstack/react-router";

import { Icon } from "@/components/ui/icons";
import { formatBytes } from "@/lib/formatters";

interface SourceFile {
  filename: string;
  bytes?: number;
  rows?: number;
}

interface SourceSpec {
  files?: SourceFile[];
  filename?: string;
  extraction?: { limitations?: string[] };
  dataset_id?: string;
  dataset_name?: string;
  cell_id?: string;
  version?: string;
}

export function sourceFiles(spec: unknown): SourceFile[] {
  const read = (spec ?? {}) as SourceSpec;
  if (Array.isArray(read.files)) return read.files.filter((file) => !!file?.filename);
  return read.filename ? [{ filename: read.filename }] : [];
}

export function SourceDetails({ spec, projectId }: { spec: unknown; projectId: string }) {
  const read = (spec ?? {}) as SourceSpec;
  const files = sourceFiles(spec);
  const limitations = read.extraction?.limitations ?? [];
  const origin = read.dataset_id && read.cell_id ? read : null;
  if (!files.length && !limitations.length && !origin) return null;
  return (
    <div className="flex flex-col gap-2 px-2.5 pb-2.5">
      {origin && (
        <p className="flex items-center gap-1.5 text-xs text-muted-foreground">
          <Icon.dataset className="size-3 shrink-0" />
          <span className="shrink-0">From</span>
          <Link
            className="inline-flex min-w-0 items-center gap-1.5 text-foreground hover:underline"
            params={{ datasetId: origin.dataset_id! }}
            search={{ cell: origin.cell_id, projectId }}
            to="/datasets/$datasetId"
          >
            <span className="truncate">{origin.dataset_name || "Dataset"}</span>
            <span aria-hidden className="text-muted-foreground">
              ·
            </span>
            <span className="shrink-0 font-mono">{origin.version}</span>
          </Link>
        </p>
      )}
      {files.length > 0 && (
        <ul aria-label="Source files" className="flex flex-wrap gap-1.5">
          {files.map((file, index) => (
            <li
              className="inline-flex h-6 max-w-full items-center gap-1.5 rounded-sm border border-border/70 px-1.5 text-xs"
              key={`${file.filename}-${index}`}
            >
              <Icon.file className="size-3 shrink-0 text-muted-foreground" />
              <span className="truncate text-foreground" title={file.filename}>
                {file.filename}
              </span>
              {file.bytes != null && (
                <span className="shrink-0 text-muted-foreground tabular-nums">
                  {formatBytes(file.bytes)}
                </span>
              )}
              {file.rows != null && (
                <span className="shrink-0 text-muted-foreground tabular-nums">
                  {file.rows.toLocaleString()} {file.rows === 1 ? "row" : "rows"}
                </span>
              )}
            </li>
          ))}
        </ul>
      )}
      {limitations.length > 0 && (
        <ul aria-label="Extraction limitations" className="flex flex-col gap-0.5">
          {limitations.map((limitation) => (
            <li className="flex items-start gap-1.5 text-xs text-muted-foreground" key={limitation}>
              <Icon.info className="mt-0.5 size-3 shrink-0" />
              {limitation}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
