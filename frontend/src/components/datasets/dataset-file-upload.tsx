import { useRef, useState } from "react";

import { Attachment } from "@/components/datasets/attachment";
import { Button } from "@/components/ui/button";
import { Icon } from "@/components/ui/icons";
import type { useDatasetUploads } from "@/hooks/use-uploads";
import { cn } from "@/lib/utils";

export function DatasetFileUpload({
  upload,
  busy = false,
  onError,
}: {
  upload: ReturnType<typeof useDatasetUploads>;
  busy?: boolean;
  onError: (message: string | null) => void;
}) {
  const [dragging, setDragging] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const addFiles = (files: File[]) => {
    if (busy) return;
    if (upload.files.length + files.length > 100) {
      onError("Choose up to 100 files.");
      return;
    }
    onError(null);
    upload.add(files);
  };
  return (
    <section
      aria-label="Upload files"
      className={cn(
        "flex flex-col gap-3 rounded-md border border-dashed p-3 transition-colors",
        dragging ? "border-primary bg-accent" : "border-border"
      )}
      onDragLeave={(e) => {
        if (!e.currentTarget.contains(e.relatedTarget as Node | null)) setDragging(false);
      }}
      onDragOver={(e) => {
        e.preventDefault();
        e.stopPropagation();
        if (!busy) setDragging(true);
      }}
      onDrop={(e) => {
        e.preventDefault();
        e.stopPropagation();
        setDragging(false);
        addFiles(Array.from(e.dataTransfer.files));
      }}
    >
      {upload.files.length > 0 && (
        <ul aria-label="Selected files" className="flex flex-wrap gap-2">
          {upload.files.map((entry) => (
            <Attachment
              detailed
              disabled={busy}
              entry={entry}
              key={entry.id}
              onRemove={() => upload.remove(entry.id)}
              onRetry={() => upload.retry(entry)}
            />
          ))}
        </ul>
      )}
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex min-w-0 flex-col gap-1">
          <p className="text-sm">
            {upload.files.length ? "Drop more files here" : "Drop files here"}
          </p>
          <p className="text-xs text-muted-foreground">
            Tables, documents, PNG, JPEG or WebP images
          </p>
        </div>
        <input
          accept=".csv,.tsv,.json,.jsonl,.ndjson,.parquet,.gz,.pdf,.docx,.md,.txt,.png,.jpg,.jpeg,.webp"
          aria-label="Choose dataset files"
          className="hidden"
          disabled={busy}
          multiple
          onChange={(e) => {
            addFiles(Array.from(e.target.files ?? []));
            e.target.value = "";
          }}
          ref={fileInputRef}
          type="file"
        />
        <Button
          disabled={busy}
          onClick={() => fileInputRef.current?.click()}
          size="sm"
          type="button"
          variant="secondary"
        >
          <Icon.add />
          {upload.files.length ? "Add files" : "Choose files"}
        </Button>
      </div>
    </section>
  );
}
