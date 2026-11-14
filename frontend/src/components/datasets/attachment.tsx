import { useEffect, useId, useState } from "react";

import { FileTypeIcon } from "@/components/file-type-icon";
import { Button } from "@/components/ui/button";
import { Icon } from "@/components/ui/icons";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { Progress } from "@/components/ui/progress";
import { Spinner } from "@/components/ui/spinner";
import type { DatasetUpload } from "@/hooks/use-uploads";
import { pdfThumbnail } from "@/lib/pdf-thumbnail";
import { cn } from "@/lib/utils";

function fileSize(bytes: number) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 ** 2) return `${(bytes / 1024).toFixed(1)} KB`;
  if (bytes < 1024 ** 3) return `${(bytes / 1024 ** 2).toFixed(1)} MB`;
  return `${(bytes / 1024 ** 3).toFixed(1)} GB`;
}

export function Attachment({
  entry,
  disabled,
  onRemove,
  onRetry,
  detailed = false,
  compact = false,
}: {
  entry: DatasetUpload;
  disabled: boolean;
  onRemove: () => void;
  onRetry: () => void;
  detailed?: boolean;
  compact?: boolean;
}) {
  const statusId = useId();
  const isImage = /\.(png|jpe?g|webp)$/i.test(entry.file.name);
  const [preview, setPreview] = useState<string | null>(null);
  const isPdf = /\.pdf$/i.test(entry.file.name);
  useEffect(() => {
    if (!isImage && !isPdf) return;
    let url: string | undefined;
    let gone = false;
    const show = (made: string) => {
      if (gone) URL.revokeObjectURL(made);
      else {
        url = made;
        setPreview(made);
      }
    };
    if (isImage) show(URL.createObjectURL(entry.file));
    // A PDF that cannot be drawn keeps the file icon.
    else void pdfThumbnail(entry.file).then(show, () => undefined);
    return () => {
      gone = true;
      if (url) URL.revokeObjectURL(url);
    };
  }, [entry.file, isImage, isPdf]);
  const working = entry.status === "uploading" || entry.status === "counting";
  const status = {
    counting: "Checking file…",
    error: "Upload failed",
    queued: "Queued",
    ready: isImage ? "Ready for OCR" : "Ready",
    uploading: `Uploading ${entry.percent}%`,
  }[entry.status];
  const type = entry.file.name.replace(/\.gz$/i, "").split(".").at(-1)?.toUpperCase();
  return (
    <li
      className={cn(
        "flex h-8 min-w-0 max-w-full items-center rounded-sm bg-control pr-1 sm:max-w-72",
        detailed && "h-auto w-full gap-2 border border-border bg-muted py-1.5 sm:max-w-none",
        compact &&
          "assistant-attachment h-7 max-w-44 shrink-0 border border-border/70 bg-card pr-0.5 sm:max-w-44",
        entry.status === "error" && "text-destructive"
      )}
    >
      <Popover>
        <PopoverTrigger
          aria-describedby={statusId}
          aria-label={`View ${entry.file.name}`}
          className={cn(
            "flex h-8 min-w-0 flex-1 items-center gap-1.5 rounded-sm px-2 text-left text-sm outline-none transition-colors hover:bg-control-hover focus-visible:ring-2 focus-visible:ring-ring/60",
            detailed && "h-auto gap-2.5",
            compact && "h-6 gap-2 pr-0 pl-1 text-xs text-muted-foreground hover:text-foreground"
          )}
          title={`${entry.file.name} · ${fileSize(entry.file.size)} · ${status}`}
          type="button"
        >
          <span
            className={cn(
              "grid shrink-0 place-items-center",
              compact && "size-5 overflow-hidden rounded-xs"
            )}
          >
            {working ? (
              <Spinner className="size-3.5" />
            ) : entry.status === "error" ? (
              <Icon.warning className="size-3.5 text-destructive" />
            ) : entry.status === "queued" ? (
              <Icon.job className="size-3.5" />
            ) : preview ? (
              <img
                alt=""
                className={cn(
                  "size-6 rounded-sm bg-background",
                  isPdf ? "object-contain" : "object-cover",
                  compact && "size-5"
                )}
                onError={() => setPreview(null)}
                src={preview}
              />
            ) : (
              <FileTypeIcon className="size-3.5" filename={entry.file.name} />
            )}
          </span>
          <span className="min-w-0 flex-1">
            <span className="block truncate">{entry.file.name}</span>
            {detailed && (
              <span className="block truncate text-xs text-muted-foreground">
                {entry.error ||
                  (entry.rows != null ? `${entry.rows.toLocaleString()} rows` : status)}
              </span>
            )}
          </span>
        </PopoverTrigger>
        <PopoverContent
          align="start"
          aria-label={`File details: ${entry.file.name}`}
          className="w-64 max-w-(--radix-popover-content-available-width) space-y-2 p-3 text-xs"
          side="top"
        >
          {preview && (
            <img
              alt={`Preview of ${entry.file.name}`}
              className="max-h-48 w-full rounded-sm bg-background object-contain"
              src={preview}
            />
          )}
          <p className="break-words font-medium">{entry.file.name}</p>
          <p className="text-muted-foreground">
            {type} · {fileSize(entry.file.size)}
          </p>
          <p
            className={cn("text-muted-foreground", entry.status === "error" && "text-destructive")}
          >
            {entry.error || status}
          </p>
          {isImage && entry.status === "ready" && (
            <p className="text-muted-foreground">Text extraction starts when sent.</p>
          )}
          {entry.rows != null && (
            <p className="text-muted-foreground">
              {entry.rows.toLocaleString()} {entry.rows === 1 ? "row" : "rows"}
            </p>
          )}
          {working && (
            <Progress
              label={`Uploading ${entry.file.name}`}
              percent={entry.status === "counting" ? null : entry.percent}
            />
          )}
        </PopoverContent>
      </Popover>
      {detailed && (
        <>
          <span className="shrink-0 rounded-sm border border-border px-1.5 py-0.5 text-xs text-muted-foreground">
            {type}
          </span>
          <span className="shrink-0 text-xs tabular-nums text-muted-foreground">
            {fileSize(entry.file.size)}
          </span>
        </>
      )}
      <span className="sr-only" id={statusId} role="status">
        {status}
      </span>
      {entry.status === "error" && (
        <Button
          aria-label={`Retry ${entry.file.name}`}
          disabled={disabled}
          onClick={onRetry}
          size="icon-xs"
          title="Retry upload"
          type="button"
          variant="ghost"
        >
          <Icon.refresh />
        </Button>
      )}
      <Button
        aria-label={`Remove ${entry.file.name}`}
        className={cn(
          "text-muted-foreground",
          compact && "assistant-attachment-remove transition-opacity"
        )}
        disabled={disabled}
        onClick={onRemove}
        size="icon-xs"
        title="Remove attachment"
        type="button"
        variant="ghost"
      >
        <Icon.close />
      </Button>
    </li>
  );
}
