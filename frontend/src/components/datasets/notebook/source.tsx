import { useRef, useState } from "react";

import apiClient from "@/client";
import { Attachment } from "@/components/datasets/attachment";
import { SOURCE_KIND_LABEL } from "@/components/datasets/badges";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Icon } from "@/components/ui/icons";
import { Spinner } from "@/components/ui/spinner";
import { useAttachDatasetSourceMutation } from "@/hooks/use-datasets";
import { useDatasetUploads } from "@/hooks/use-uploads";
import { errorMessage } from "@/lib/notify";
import type { Dataset } from "@/openapi";

interface SourceArtifact {
  id: string;
  filename: string;
  rows: number;
}

export function extractionStatus(spec: unknown): string | undefined {
  const progress = (
    spec as {
      landing_progress?: { filename?: string; stage?: string; completed?: number; total?: number };
    } | null
  )?.landing_progress;
  if (!progress) return;
  if (progress.stage === "merging") return "Adding extracted rows";
  const action = progress.stage === "extracting" ? "Extracting text" : "Reading file";
  const count =
    progress.total && progress.total > 1
      ? ` · ${(progress.completed ?? 0) + 1} of ${progress.total}`
      : "";
  return `${action}${progress.filename ? ` · ${progress.filename}` : ""}${count}`;
}

export function SourceDetails({
  datasetId,
  kind,
  spec,
}: {
  datasetId: string;
  kind: Dataset["sourceKind"];
  spec: unknown;
}) {
  const sourceSpec = spec as {
    sources?: SourceArtifact[];
    filename?: string;
    derived_from?: { version?: string };
  } | null;
  const sources = sourceSpec?.sources ?? [];
  const [error, setError] = useState("");
  const [downloading, setDownloading] = useState<string | null>(null);
  const download = async (source: SourceArtifact) => {
    setDownloading(source.id);
    setError("");
    try {
      const blob = await apiClient.datasets.datasetsSourcesRetrieve({
        artifactId: source.id,
        id: datasetId,
      });
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = source.filename;
      anchor.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (err) {
      setError(errorMessage(err, "Couldn't download the source."));
    } finally {
      setDownloading(null);
    }
  };
  const label = sourceSpec?.derived_from
    ? `Derived dataset${sourceSpec.derived_from.version ? ` · ${sourceSpec.derived_from.version}` : ""}`
    : kind === "file"
      ? sourceSpec?.filename || SOURCE_KIND_LABEL.file
      : SOURCE_KIND_LABEL[kind];
  if (!sources.length && kind === "pending" && !sourceSpec?.derived_from) return null;
  return (
    <div aria-label="Dataset sources" className="border-b border-border/70 px-2.5 py-2">
      <div className="flex flex-wrap gap-2">
        {sources.length ? (
          sources.map((source) => (
            <Badge
              className="h-7 min-w-0 max-w-full gap-2 bg-card pr-0.5"
              key={source.id + source.filename}
              size="chip"
              title={source.filename}
              variant="outline"
            >
              <Icon.file className="size-3 text-muted-foreground" />
              <span className="min-w-0 max-w-sm truncate">{source.filename}</span>
              <span className="shrink-0 text-muted-foreground">
                {source.rows.toLocaleString()} rows
              </span>
              <Button
                aria-label={`Download ${source.filename}`}
                disabled={downloading !== null}
                onClick={() => void download(source)}
                size="icon-xs"
                variant="ghost"
              >
                {downloading === source.id ? <Spinner className="size-3" /> : <Icon.download />}
              </Button>
            </Badge>
          ))
        ) : (
          <Badge className="max-w-full gap-2 bg-card" size="chip" title={label} variant="outline">
            {kind === "file" && !sourceSpec?.derived_from ? (
              <Icon.file className="size-3" />
            ) : (
              <Icon.dataset className="size-3" />
            )}
            <span className="truncate">{label}</span>
          </Badge>
        )}
      </div>
      {error && (
        <p className="mt-2 text-xs text-destructive" role="alert">
          {error}
        </p>
      )}
    </div>
  );
}

export function SourceLanding({
  datasetId,
  brief,
  busy,
  landing,
  landingStatus,
}: {
  datasetId: string;
  brief: string;
  busy: boolean;
  landing: boolean;
  landingStatus?: string;
}) {
  const input = useRef<HTMLInputElement>(null);
  const upload = useDatasetUploads();
  const attach = useAttachDatasetSourceMutation(datasetId);
  const [error, setError] = useState("");
  const ready = upload.files.length > 0 && upload.files.every((file) => file.status === "ready");
  const add = (files: File[]) => {
    if (attach.isPending) return;
    if (upload.files.length + files.length > 100) {
      setError("Choose up to 100 files.");
      return;
    }
    setError("");
    upload.add(files);
  };
  return (
    <article aria-label="Source" className="m-3 rounded-sm border border-border">
      <div className="flex items-center gap-2 border-b border-border/70 px-3 py-2 text-xs">
        <Icon.file className="size-3" />
        Source
      </div>
      <div
        className="space-y-4 px-3 py-4"
        onDragOver={(event) => event.preventDefault()}
        onDrop={(event) => {
          event.preventDefault();
          add(Array.from(event.dataTransfer.files));
        }}
      >
        {brief && (
          <p className="max-w-prose whitespace-pre-wrap text-sm leading-relaxed">{brief}</p>
        )}
        {landing ? (
          <p className="flex items-center gap-2 text-xs text-muted-foreground">
            <Spinner className="size-3" />
            {landingStatus || "Reading source data"}
          </p>
        ) : (
          <>
            <p className="text-xs text-muted-foreground">
              Drop tables, documents, PNG, JPEG or WebP images here.
            </p>
            {upload.files.length > 0 && (
              <ul aria-label="Selected files" className="flex flex-wrap gap-2">
                {upload.files.map((file) => (
                  <Attachment
                    disabled={attach.isPending}
                    entry={file}
                    key={file.id}
                    onRemove={() => upload.remove(file.id)}
                    onRetry={() => upload.retry(file)}
                  />
                ))}
              </ul>
            )}
            <input
              accept=".csv,.tsv,.json,.jsonl,.ndjson,.parquet,.gz,.pdf,.docx,.md,.txt,.png,.jpg,.jpeg,.webp"
              aria-label="Attach source files"
              className="hidden"
              multiple
              onChange={(event) => {
                add(Array.from(event.target.files ?? []));
                event.target.value = "";
              }}
              ref={input}
              type="file"
            />
            <div className="flex gap-2">
              <Button
                disabled={attach.isPending}
                onClick={() => input.current?.click()}
                size="sm"
                variant="secondary"
              >
                <Icon.add />
                Add files
              </Button>
              {upload.files.length > 0 && (
                <Button
                  disabled={busy || !ready || attach.isPending}
                  onClick={() =>
                    attach.mutate({ uploads: upload.files.map((file) => file.uploadId!) })
                  }
                  size="sm"
                >
                  {attach.isPending && <Spinner className="size-3" />}Read source
                </Button>
              )}
            </div>
          </>
        )}
        {error && (
          <p className="text-xs text-destructive" role="alert">
            {error}
          </p>
        )}
      </div>
    </article>
  );
}
