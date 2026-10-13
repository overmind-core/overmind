import { useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import { HoverCard, HoverCardContent, HoverCardTrigger } from "@/components/ui/hover-card";
import { Icon } from "@/components/ui/icons";
import { Progress } from "@/components/ui/progress";
import { Spinner } from "@/components/ui/spinner";
import type { DatasetUpload } from "@/hooks/use-uploads";
import { formatBytes } from "@/lib/formatters";
import { cn } from "@/lib/utils";

const IMAGE = /\.(png|jpe?g|webp)$/i;

const fileType = (name: string) =>
  name.toLowerCase().replace(/\.gz$/, "").split(".").at(-1)?.toUpperCase() || "FILE";

function statusText(entry: DatasetUpload): string {
  if (entry.status === "error") return entry.error ?? "Upload failed";
  if (entry.status === "queued") return "Queued";
  if (entry.status === "uploading") return `Uploading ${entry.percent}%`;
  if (entry.status === "counting") return "Counting rows…";
  if (entry.rows == null) return "Ready";
  return `${entry.rows.toLocaleString()} ${entry.rows === 1 ? "row" : "rows"}`;
}

function ImagePreview({ file }: { file: File }) {
  const [url, setUrl] = useState<string | null>(null);
  useEffect(() => {
    const next = URL.createObjectURL(file);
    setUrl(next);
    return () => URL.revokeObjectURL(next);
  }, [file]);
  return url ? (
    <img alt="" className="max-h-48 w-full rounded-sm object-contain" src={url} />
  ) : null;
}

export function Attachment({
  entry,
  disabled,
  onRetry,
  onRemove,
}: {
  entry: DatasetUpload;
  disabled: boolean;
  onRetry: () => void;
  onRemove: () => void;
}) {
  const name = entry.file.name;
  const image = IMAGE.test(name);
  const working = entry.status !== "ready" && entry.status !== "error";
  return (
    <li className="flex items-center gap-3 rounded-sm border border-border bg-muted px-3 py-2">
      <HoverCard openDelay={200}>
        <HoverCardTrigger asChild>
          <span className="flex min-w-0 flex-1 items-center gap-3">
            <Icon.file className="size-4 shrink-0 text-muted-foreground" />
            <span className="min-w-0 flex-1">
              <span className="block truncate text-sm" title={name}>
                {name}
              </span>
              <span
                className={cn(
                  "block text-xs",
                  entry.status === "error" ? "text-destructive" : "text-muted-foreground"
                )}
              >
                {statusText(entry)}
              </span>
            </span>
          </span>
        </HoverCardTrigger>
        <HoverCardContent align="start" aria-label={`File details: ${name}`} className="w-72 p-2.5">
          <div className="flex flex-col gap-2 text-xs">
            {image && <ImagePreview file={entry.file} />}
            <p className="truncate text-foreground">{name}</p>
            <p className="text-muted-foreground">
              {fileType(name)} · {formatBytes(entry.file.size)}
            </p>
            <p className={entry.status === "error" ? "text-destructive" : "text-muted-foreground"}>
              {statusText(entry)}
            </p>
            {entry.status === "ready" && entry.rows == null && (
              <p className="text-muted-foreground">
                Text is extracted after the dataset is created.
              </p>
            )}
            {entry.status === "uploading" && (
              <Progress label={`Uploading ${name}`} percent={entry.percent} />
            )}
          </div>
        </HoverCardContent>
      </HoverCard>
      <span className="shrink-0 rounded-sm border border-border px-1.5 py-0.5 text-xs text-muted-foreground">
        {fileType(name)}
      </span>
      <span className="shrink-0 text-xs tabular-nums text-muted-foreground">
        {formatBytes(entry.file.size)}
      </span>
      {entry.status === "error" && (
        <Button
          aria-label={`Retry ${name}`}
          disabled={disabled}
          onClick={onRetry}
          size="sm"
          title="Retry upload"
          variant="secondary"
        >
          Retry
        </Button>
      )}
      {working && <Spinner className="size-3.5" />}
      <Button
        aria-label={`Remove ${name}`}
        disabled={disabled}
        onClick={onRemove}
        size="icon-sm"
        type="button"
        variant="ghost"
      >
        <Icon.close className="size-4" />
      </Button>
    </li>
  );
}
