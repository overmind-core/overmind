import { useRef, useState } from "react";

import apiClient from "@/client";
import { Attachment } from "@/components/datasets/attachment";
import { Button } from "@/components/ui/button";
import { Icon } from "@/components/ui/icons";
import { Spinner } from "@/components/ui/spinner";
import { useAttachDatasetSourceMutation } from "@/hooks/use-datasets";
import { useDatasetUploads } from "@/hooks/use-uploads";
import { errorMessage } from "@/lib/notify";

interface SourceArtifact {
  id: string;
  filename: string;
  rows: number;
  sha256: string;
  extraction?: { method?: string; version?: string; pages?: number; limitations?: string[] };
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
  brief,
  spec,
}: {
  datasetId: string;
  brief?: string;
  spec: unknown;
}) {
  const sources = (spec as { sources?: SourceArtifact[] } | null)?.sources ?? [];
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
  if (!brief && !sources.length) return null;
  return (
    <details className="border-b border-border/70 px-2.5 py-2 text-xs">
      <summary className="cursor-pointer text-muted-foreground hover:text-foreground focus-visible:outline-primary">
        {sources.length
          ? `${sources.length} source ${sources.length === 1 ? "file" : "files"}`
          : "Original request"}
      </summary>
      <div className="mt-3 space-y-3">
        {brief && <p className="max-w-prose whitespace-pre-wrap leading-relaxed">{brief}</p>}
        {sources.map((source) => (
          <div className="space-y-1" key={source.id + source.filename}>
            <div className="flex items-center gap-2">
              <Icon.file className="size-3 shrink-0 text-muted-foreground" />
              <span className="min-w-0 flex-1 truncate">{source.filename}</span>
              <span className="text-muted-foreground">{source.rows.toLocaleString()} rows</span>
              <Button
                aria-label={`Download ${source.filename}`}
                disabled={downloading !== null}
                onClick={() => void download(source)}
                size="icon-xs"
                variant="ghost"
              >
                {downloading === source.id ? <Spinner className="size-3" /> : <Icon.download />}
              </Button>
            </div>
            <p className="break-all font-mono text-muted-foreground" title={source.sha256}>
              {source.sha256.slice(0, 12)}
              {source.extraction?.method
                ? ` · ${source.extraction.method} ${source.extraction.version}`
                : ""}
            </p>
            {source.extraction?.limitations?.map((limitation) => (
              <p className="text-warning" key={limitation}>
                {limitation}
              </p>
            ))}
          </div>
        ))}
        {error && (
          <p className="text-destructive" role="alert">
            {error}
          </p>
        )}
      </div>
    </details>
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
