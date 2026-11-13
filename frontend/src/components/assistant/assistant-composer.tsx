import { useRef, useState } from "react";

import { useMutation, useQueryClient } from "@tanstack/react-query";

import apiClient from "@/client";
import { Attachment } from "@/components/datasets/attachment";
import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Icon } from "@/components/ui/icons";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { Spinner } from "@/components/ui/spinner";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { sendToDataset } from "@/hooks/use-assistant-thread";
import { activeCellOf, datasetDisplayName, invalidateDataset } from "@/hooks/use-datasets";
import { useGuestGate } from "@/hooks/use-guest-gate";
import { useDatasetUploads } from "@/hooks/use-uploads";
import { errorMessage } from "@/lib/notify";
import type { Dataset } from "@/openapi";

const ACCEPT =
  ".csv,.tsv,.json,.jsonl,.ndjson,.parquet,.gz,.pdf,.docx,.md,.txt,.png,.jpg,.jpeg,.webp";
const stripExtension = (name: string) => name.replace(/(\.[a-z0-9]+){1,2}$/i, "");

/** The goal a user starts from: attached files or datasets, and one sentence. Sending
 *  creates the working dataset if files were attached, opens its thread and queues the
 *  message; the assistant starts when the source has landed. A retry of the same request
 *  repeats nothing; a changed request is a new one. */
export function AssistantComposer({
  projectId,
  datasets,
  onRemoveDataset,
  onStarted,
}: {
  projectId: string;
  /** Datasets attached from the list. Without uploads the first is the working dataset. */
  datasets: Dataset[];
  onRemoveDataset: (id: string) => void;
  onStarted: (datasetId: string) => void;
}) {
  const qc = useQueryClient();
  const guard = useGuestGate();
  const upload = useDatasetUploads();
  const fileInput = useRef<HTMLInputElement>(null);
  const [text, setText] = useState("");
  const [dragging, setDragging] = useState(false);
  const progress = useRef<{ request: string; key: string; dataset?: string }>(null);

  const uploading = upload.files.some((file) => file.status !== "ready" && file.status !== "error");
  const ready = upload.files.filter((file) => file.status === "ready");
  const start = useMutation({
    mutationFn: async (message: string) => {
      const uploads = ready.map((file) => file.uploadId!);
      const request = JSON.stringify([message, uploads, datasets.map((dataset) => dataset.id)]);
      if (progress.current?.request !== request)
        progress.current = { key: crypto.randomUUID(), request };
      const step = progress.current;
      if (!step.dataset) {
        step.dataset = uploads.length
          ? (
              await apiClient.datasets.datasetsCreate({
                datasetCreateRequest: {
                  name: stripExtension(ready[0].file.name) || "Untitled dataset",
                  project: projectId,
                  source: { uploads },
                },
              })
            ).id
          : datasets[0].id;
        invalidateDataset(qc);
      }
      const working = step.dataset;
      const references = await Promise.all(
        datasets
          .filter((dataset) => dataset.id !== working)
          .map((dataset) => apiClient.datasets.datasetsRetrieve({ id: dataset.id }))
      );
      await sendToDataset(qc, projectId, working, {
        cells: references.flatMap((dataset) => activeCellOf(dataset)?.id ?? []),
        requestKey: step.key,
        text: message,
      });
      return working;
    },
  });

  const locked = start.isPending;
  const hasData = ready.length > 0 || datasets.length > 0;
  const sendHint = locked
    ? "Starting"
    : uploading
      ? "Uploading"
      : !hasData
        ? "No data attached"
        : !text.trim()
          ? "No message"
          : "Send";
  const submit = guard(() => {
    if (locked || uploading || !hasData || !text.trim()) return;
    // Called only while this page is mounted: a start that ends later moves nobody.
    start.mutate(text.trim(), { onSuccess: onStarted });
  });
  const addFiles = guard((files: File[]) => upload.add(files));

  return (
    <Card
      className="assistant-surface assistant-composer w-full p-1.5 data-[dragging=true]:border-ring"
      data-dragging={dragging}
    >
      <form
        aria-label="Start with the assistant"
        className="assistant-chat-inner overflow-hidden border border-border/70 bg-muted"
        onDragLeave={() => setDragging(false)}
        onDragOver={(event) => {
          event.preventDefault();
          setDragging(true);
        }}
        onDrop={(event) => {
          event.preventDefault();
          setDragging(false);
          addFiles(Array.from(event.dataTransfer.files));
        }}
        onSubmit={(event) => {
          event.preventDefault();
          submit();
        }}
      >
        <span aria-hidden="true" className="assistant-prompt-mark">
          &gt;
        </span>
        <textarea
          aria-label="What would you like to train?"
          className="assistant-chat-input block max-h-56 min-h-28 w-full resize-none border-0 bg-transparent text-sm outline-none"
          disabled={locked}
          id="assistant-intent"
          maxLength={16000}
          onChange={(event) => setText(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
              event.preventDefault();
              submit();
            }
          }}
          onPaste={(event) => {
            if (event.clipboardData.files.length) {
              event.preventDefault();
              addFiles(Array.from(event.clipboardData.files));
            }
          }}
          placeholder="What would you like to train?"
          value={text}
        />
        {start.error && (
          <div className="flex items-center gap-2 px-3 pb-2">
            <Alert className="w-auto flex-1">
              {errorMessage(start.error, "The request could not be started.")}
            </Alert>
            <Button onClick={submit} size="xs" type="button" variant="secondary">
              Retry
            </Button>
          </div>
        )}
        <div className="flex min-w-0 items-center gap-2 px-3 pt-1 pb-3">
          <input
            accept={ACCEPT}
            aria-label="Attach dataset files"
            className="hidden"
            disabled={locked}
            multiple
            onChange={(event) => {
              addFiles(Array.from(event.target.files ?? []));
              event.target.value = "";
            }}
            ref={fileInput}
            type="file"
          />
          <Tooltip>
            <TooltipTrigger asChild>
              <Button
                aria-label="Attach files"
                disabled={locked}
                onClick={guard(() => fileInput.current?.click())}
                size="icon-sm"
                type="button"
                variant="outline"
              >
                <Icon.add />
              </Button>
            </TooltipTrigger>
            <TooltipContent>Attach files</TooltipContent>
          </Tooltip>
          <ul
            aria-label="Attached data"
            className="assistant-attachments flex min-w-0 flex-1 items-center gap-1.5 overflow-x-auto"
          >
            {datasets.map((dataset, index) => (
              <li
                className="assistant-attachment flex h-7 max-w-44 shrink-0 items-center gap-1 rounded-sm border border-border/70 bg-card pr-0.5 pl-1.5 text-xs text-muted-foreground"
                key={dataset.id}
              >
                <Popover>
                  <PopoverTrigger asChild>
                    <button
                      aria-label={`Dataset details: ${datasetDisplayName(dataset)}`}
                      className="flex min-w-0 items-center gap-1.5 rounded-sm outline-none focus-visible:ring-2 focus-visible:ring-ring/60"
                      type="button"
                    >
                      <Icon.dataset className="size-3.5 shrink-0 text-cat-2" />
                      <span className="truncate">{datasetDisplayName(dataset)}</span>
                    </button>
                  </PopoverTrigger>
                  <PopoverContent align="start" className="w-64 space-y-2 p-3 text-xs" side="top">
                    <p className="break-words font-medium">{datasetDisplayName(dataset)}</p>
                    <p className="text-muted-foreground">
                      {dataset.activeVersion} · {dataset.rows.toLocaleString()} rows
                    </p>
                    <p className="text-muted-foreground">
                      {index === 0 && !upload.files.length
                        ? "Working dataset"
                        : "Reference dataset"}
                    </p>
                  </PopoverContent>
                </Popover>
                <Button
                  aria-label={`Remove ${datasetDisplayName(dataset)} from chat`}
                  className="assistant-attachment-remove text-muted-foreground transition-opacity"
                  disabled={locked}
                  onClick={() => onRemoveDataset(dataset.id)}
                  size="icon-xs"
                  type="button"
                  variant="ghost"
                >
                  <Icon.close />
                </Button>
              </li>
            ))}
            {upload.files.map((entry) => (
              <Attachment
                compact
                disabled={locked}
                entry={entry}
                key={entry.id}
                onRemove={() => upload.remove(entry.id)}
                onRetry={() => upload.retry(entry)}
              />
            ))}
          </ul>
          <Tooltip>
            <TooltipTrigger asChild>
              <span className="inline-flex shrink-0">
                <Button
                  className="disabled:text-muted-foreground"
                  disabled={locked || uploading || !hasData || !text.trim()}
                  size="sm"
                  type="submit"
                  variant="secondary"
                >
                  {locked || uploading ? <Spinner size="sm" /> : <Icon.send />}
                  Send
                </Button>
              </span>
            </TooltipTrigger>
            <TooltipContent>{sendHint}</TooltipContent>
          </Tooltip>
        </div>
      </form>
    </Card>
  );
}
