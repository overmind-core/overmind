import { useEffect, useRef, useState } from "react";

import {
  AssistantRuntimeProvider,
  ComposerPrimitive,
  type ThreadMessageLike,
  ThreadPrimitive,
  useExternalStoreRuntime,
} from "@assistant-ui/react";
import { useMutation, useQueryClient } from "@tanstack/react-query";

import api from "@/client";
import { Attachment } from "@/components/datasets/attachment";
import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Icon } from "@/components/ui/icons";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { Spinner } from "@/components/ui/spinner";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import {
  activeCellOf,
  datasetDisplayName,
  invalidateDataset,
  useCreateDatasetMutation,
  useDatasetQuery,
} from "@/hooks/use-datasets";
import { useGuestGate } from "@/hooks/use-guest-gate";
import { useDatasetUploads } from "@/hooks/use-uploads";
import { errorMessage } from "@/lib/notify";
import { cn } from "@/lib/utils";
import type { Dataset, WorkshopStartRequest } from "@/openapi";

type Submission = Omit<WorkshopStartRequest, "project">;

export function WorkshopComposer({
  projectId,
  onStarted,
  datasets = [],
  onRemoveDataset,
  onLockedChange,
}: {
  projectId: string;
  onStarted: (datasetId: string) => void;
  datasets?: Dataset[];
  onRemoveDataset?: (datasetId: string) => void;
  onLockedChange?: (locked: boolean) => void;
}) {
  const [dragging, setDragging] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [submission, setSubmission] = useState<Submission | null>(null);
  const fileInput = useRef<HTMLInputElement>(null);
  const attachments = useRef<HTMLUListElement>(null);
  const startRequested = useRef(false);
  const sending = useRef(false);
  const upload = useDatasetUploads();
  const create = useCreateDatasetMutation();
  const source = useDatasetQuery(submission?.dataset);
  const sourceCell = activeCellOf(source.data)?.id;
  const qc = useQueryClient();
  const guard = useGuestGate();
  const start = useMutation({
    mutationFn: (request: Submission) =>
      api.workshopTasks.workshopTasksCreate({
        workshopStartRequest: { ...request, project: projectId },
      }),
    onSuccess: (task, request) => {
      invalidateDataset(qc, request.dataset);
      qc.setQueryData(["workshop-agent", projectId, request.dataset], {
        enabled: true,
        tasks: [task],
      });
      onStarted(request.dataset);
    },
  });
  const startTask = start.mutate;
  useEffect(() => {
    if (!submission || !sourceCell || source.data?.state === "landing") return;
    if (startRequested.current || source.data?.state === "error") return;
    startRequested.current = true;
    startTask(submission);
  }, [submission, sourceCell, source.data?.state, startTask]);

  const attachmentCount = upload.files.length + datasets.length;
  useEffect(() => {
    if (attachmentCount) {
      attachments.current?.scrollTo({
        behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches
          ? "instant"
          : "smooth",
        left: attachments.current.scrollWidth,
      });
    }
  }, [attachmentCount]);

  const locked = create.isPending || !!submission;
  useEffect(() => onLockedChange?.(locked), [locked, onLockedChange]);
  const uploading = upload.files.some((file) => !["ready", "error"].includes(file.status));
  const ready =
    attachmentCount > 0 &&
    datasets.length <= 100 &&
    datasets.every((dataset) => !!dataset.activeVersion) &&
    upload.files.every((file) => file.status === "ready");
  const sendDisabled = !ready || locked;
  const sendHint =
    attachmentCount === 0
      ? "Attach data before sending."
      : datasets.length > 100
        ? "Attach up to 100 datasets."
        : datasets.some((dataset) => !dataset.activeVersion)
          ? "Selected datasets must have published data."
          : uploading
            ? "Wait for the files to finish uploading."
            : upload.files.some((file) => file.status === "error")
              ? "Retry or remove failed attachments."
              : locked
                ? "Preparing dataset…"
                : "Send";
  const addFiles = (files: File[]) => {
    if (locked || !files.length) return;
    if (upload.files.length + files.length > 100) {
      setError("Choose up to 100 files.");
      return;
    }
    setError(null);
    guard(() => upload.add(files))();
  };
  const submit = async (objective: string) => {
    if (!objective.trim() || !ready || locked || sending.current) return;
    sending.current = true;
    setError(null);
    try {
      const dataset = upload.files.length
        ? await create.mutateAsync({
            brief: objective.trim(),
            capabilityId: null,
            intent: "pending",
            name: upload.files[0].file.name.replace(/\.[^.]+$/, ""),
            projectId,
            source: { uploads: upload.files.map((file) => file.uploadId!) },
          })
        : datasets[0];
      setSubmission({
        contextDatasets: datasets
          .filter((reference) => reference.id !== dataset.id)
          .map((reference) => reference.id),
        dataset: dataset.id,
        objective: objective.trim(),
        requestKey: crypto.randomUUID(),
      });
    } catch (err) {
      setError(errorMessage(err, "Couldn't create the dataset."));
      if (!runtime.thread.composer.getState().text) runtime.thread.composer.setText(objective);
    } finally {
      sending.current = false;
    }
  };
  const failure =
    error ||
    (start.error && errorMessage(start.error, "Couldn't start the agent.")) ||
    (source.error && errorMessage(source.error, "Couldn't check the import.")) ||
    (source.data?.state === "error" && "The source import failed. Open the dataset to review it.");

  const runtime = useExternalStoreRuntime<ThreadMessageLike>({
    convertMessage: (message) => message,
    isSendDisabled: sendDisabled,
    messages: [],
    onNew: async (message) =>
      submit(
        message.content
          .filter((part) => part.type === "text")
          .map((part) => part.text)
          .join("\n")
      ),
  });
  useEffect(() => {
    if (failure && submission) runtime.thread.composer.setText(submission.objective);
  }, [failure, submission, runtime]);

  return (
    <AssistantRuntimeProvider runtime={runtime}>
      <ThreadPrimitive.Root asChild>
        <Card
          className={cn(
            "workshop-chat-surface workshop-composer w-full p-1.5",
            dragging && "border-ring"
          )}
        >
          <ComposerPrimitive.Root
            aria-label="Prepare data with the Workshop agent"
            className="workshop-chat-inner flex min-w-0 flex-col border border-border/70 bg-muted"
            onDragLeave={(event) => {
              if (!event.currentTarget.contains(event.relatedTarget as Node | null))
                setDragging(false);
            }}
            onDragOver={(event) => {
              event.preventDefault();
              if (!locked) setDragging(true);
            }}
            onDrop={(event) => {
              event.preventDefault();
              setDragging(false);
              addFiles(Array.from(event.dataTransfer.files));
            }}
            onSubmit={guard()}
          >
            <span aria-hidden="true" className="workshop-prompt-mark">
              &gt;
            </span>
            <ComposerPrimitive.Input
              addAttachmentOnPaste={false}
              aria-label="What would you like to train?"
              cancelOnEscape={false}
              className="workshop-chat-input block w-full outline-none min-h-28 max-h-56 resize-none border-0 bg-transparent px-4 py-4 text-sm focus-visible:ring-0 md:text-sm"
              disabled={locked}
              id="workshop-intent"
              maxLength={16000}
              onKeyDown={(event) => {
                if (event.key === "Enter") guard()(event);
              }}
              onPaste={(event) => {
                if (event.clipboardData.files.length) {
                  event.preventDefault();
                  addFiles(Array.from(event.clipboardData.files));
                }
              }}
              placeholder="What would you like to train?"
            />
            {failure && <Alert className="mx-3 mb-2 w-auto">{failure}</Alert>}
            {submission && failure && (
              <div
                aria-live="polite"
                className="flex items-center gap-2 px-4 pb-2 text-xs text-muted-foreground"
              >
                <span>Request retained</span>
                {start.isError && (
                  <Button
                    onClick={() => start.mutate(submission)}
                    size="xs"
                    type="button"
                    variant="secondary"
                  >
                    Retry
                  </Button>
                )}
                {source.isError && (
                  <Button
                    onClick={() => void source.refetch()}
                    size="xs"
                    type="button"
                    variant="secondary"
                  >
                    Retry
                  </Button>
                )}
                <Button
                  className="ml-auto"
                  onClick={() => onStarted(submission.dataset)}
                  size="xs"
                  type="button"
                  variant="secondary"
                >
                  Open dataset
                  <Icon.forward />
                </Button>
              </div>
            )}
            <div className="flex min-w-0 items-center gap-2 px-3 pb-3 pt-1">
              <input
                accept=".csv,.tsv,.json,.jsonl,.ndjson,.parquet,.xlsx,.gz,.pdf,.docx,.md,.txt,.png,.jpg,.jpeg,.webp"
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
                className="workshop-attachments flex min-w-0 flex-1 items-center gap-1.5 overflow-x-auto"
                ref={attachments}
              >
                {datasets.map((dataset, index) => (
                  <li
                    className="workshop-attachment flex h-7 max-w-44 shrink-0 items-center gap-1 rounded-sm border border-border/70 bg-card pl-1.5 pr-0.5 text-xs text-muted-foreground"
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
                      <PopoverContent
                        align="start"
                        className="w-64 space-y-2 p-3 text-xs"
                        side="top"
                      >
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
                      className="workshop-attachment-remove text-muted-foreground transition-opacity"
                      disabled={locked}
                      onClick={() => onRemoveDataset?.(dataset.id)}
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
                  <span
                    aria-label="Send"
                    className="inline-flex shrink-0 rounded-sm outline-none focus-visible:ring-2 focus-visible:ring-ring/60"
                    role="group"
                    tabIndex={sendDisabled ? 0 : undefined}
                  >
                    <ComposerPrimitive.Send asChild>
                      <Button
                        className="disabled:text-muted-foreground"
                        disabled={sendDisabled}
                        onClick={guard()}
                        size="sm"
                        type="button"
                        variant="secondary"
                      >
                        {locked || uploading ? <Spinner size="sm" /> : <Icon.send />}
                        Send
                      </Button>
                    </ComposerPrimitive.Send>
                  </span>
                </TooltipTrigger>
                <TooltipContent>{sendHint}</TooltipContent>
              </Tooltip>
            </div>
          </ComposerPrimitive.Root>
        </Card>
      </ThreadPrimitive.Root>
    </AssistantRuntimeProvider>
  );
}
