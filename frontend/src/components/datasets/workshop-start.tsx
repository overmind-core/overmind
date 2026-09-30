import { type CSSProperties, type RefObject, useEffect, useRef, useState } from "react";

import { useNavigate } from "@tanstack/react-router";

import { Attachment } from "@/components/datasets/attachment";
import { WorkshopFundingControl } from "@/components/datasets/workshop-funding";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { EmptyState } from "@/components/ui/empty-state";
import { Icon } from "@/components/ui/icons";
import { Spinner } from "@/components/ui/spinner";
import { useCreateDatasetMutation } from "@/hooks/use-datasets";
import { useGuestGate } from "@/hooks/use-guest-gate";
import type { useDatasetUploads } from "@/hooks/use-uploads";
import { useWorkshopFunding } from "@/hooks/use-workshop-funding";
import { errorMessage } from "@/lib/notify";
import { cn } from "@/lib/utils";

const WELCOME = "Ready to train?";

export function WorkshopStart({
  projectId,
  uploads,
  inputRef,
  focus = false,
  dragging = false,
  onBusyChange,
  onImportTraces,
}: {
  projectId: string;
  uploads: ReturnType<typeof useDatasetUploads>;
  inputRef?: RefObject<HTMLInputElement | null>;
  focus?: boolean;
  dragging?: boolean;
  onBusyChange?: (busy: boolean) => void;
  onImportTraces?: () => void;
}) {
  const [brief, setBrief] = useState("");
  const [error, setError] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const sending = useRef(false);
  const localInput = useRef<HTMLInputElement>(null);
  const fileInput = inputRef ?? localInput;
  const textInput = useRef<HTMLTextAreaElement>(null);
  useEffect(() => {
    if (focus) textInput.current?.focus();
  }, [focus]);
  const create = useCreateDatasetMutation();
  const navigate = useNavigate();
  const guard = useGuestGate();
  const { isChanging: changingFunding } = useWorkshopFunding();
  const pending = submitting || create.isPending;
  const failed = uploads.files.some((file) => file.status === "error");
  const unfinished = uploads.files.some((file) => file.status !== "ready");
  const tooMany = uploads.files.length > 100;
  const canStart =
    !!brief.trim() &&
    uploads.files.length > 0 &&
    !unfinished &&
    !tooMany &&
    !pending &&
    !changingFunding;
  const start = async () => {
    if (!canStart || sending.current) return;
    sending.current = true;
    setSubmitting(true);
    onBusyChange?.(true);
    setError("");
    try {
      const filename = uploads.files.length === 1 ? uploads.files[0].file.name : "";
      const dataset = await create.mutateAsync({
        brief: brief.trim(),
        capabilityId: null,
        intent: "pending",
        name:
          filename.replace(
            /\.(csv|tsv|json|jsonl|ndjson|parquet|pdf|docx|md|txt|png|jpe?g|webp)(\.gz)?$/i,
            ""
          ) || "Untitled dataset",
        projectId,
        source: { uploads: uploads.files.map((file) => file.uploadId!) },
      });
      await navigate({
        params: { datasetId: dataset.id },
        search: { projectId },
        to: "/datasets/$datasetId",
      });
    } catch (err) {
      setError(errorMessage(err, "Couldn't start the dataset."));
    } finally {
      sending.current = false;
      setSubmitting(false);
      onBusyChange?.(false);
    }
  };
  const hint = tooMany
    ? "Choose up to 100 files."
    : failed
      ? "Retry or remove failed attachments."
      : unfinished
        ? "Uploading attachments…"
        : "";
  return (
    <section
      aria-label="New dataset"
      className="flex min-h-0 flex-1 flex-col items-center pt-8 pb-1"
    >
      <EmptyState
        className="workshop-intro min-h-48 w-full py-8"
        icon={Icon.workshopTitle}
        iconClassName="workshop-intro-mark mb-4 size-8 shrink-0 sm:size-12"
        title={
          <>
            <span className="sr-only">{WELCOME}</span>
            <span aria-hidden className="workshop-intro-text">
              {Array.from(WELCOME, (letter, index) => (
                <span
                  className="workshop-intro-letter"
                  key={`${index}-${letter}`}
                  style={{ "--letter-index": index } as CSSProperties}
                >
                  {letter}
                </span>
              ))}
            </span>
          </>
        }
        titleClassName="workshop-intro-title"
      />
      <form
        aria-label="Start a dataset"
        className="w-full max-w-4xl shrink-0"
        onSubmit={(event) => {
          event.preventDefault();
          guard(() => void start())();
        }}
      >
        <Card
          className={cn(
            "relative bg-wash-raised p-1 transition-colors duration-200 motion-reduce:transition-none",
            dragging && "border-primary"
          )}
        >
          <div className="rounded-md border border-border bg-card transition-colors duration-200 focus-within:border-input motion-reduce:transition-none">
            <textarea
              aria-label="Describe your data task"
              className="block max-h-64 min-h-28 w-full resize-none overflow-y-auto bg-transparent px-5 pt-5 pb-3 text-sm leading-relaxed outline-none field-sizing-content placeholder:text-muted-foreground"
              disabled={pending}
              maxLength={8000}
              onChange={(event) => {
                setBrief(event.target.value);
                setError("");
              }}
              onKeyDown={(event) => {
                if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
                  event.preventDefault();
                  guard(() => void start())();
                }
              }}
              onPaste={(event) => {
                const files = Array.from(event.clipboardData.files);
                if (files.length) {
                  event.preventDefault();
                  guard(() => uploads.add(files))();
                }
              }}
              placeholder="Describe what you want to do with your data…"
              ref={textInput}
              rows={3}
              value={brief}
            />
            <div className="flex items-end gap-2 px-3 pb-3">
              <div className="flex min-w-0 flex-1 flex-wrap items-center gap-2">
                <DropdownMenu>
                  <DropdownMenuTrigger asChild>
                    <Button
                      aria-label="Add source"
                      disabled={pending}
                      size="icon"
                      title="Add source"
                      type="button"
                      variant="ghost"
                    >
                      <Icon.add />
                    </Button>
                  </DropdownMenuTrigger>
                  <DropdownMenuContent align="start" side="top">
                    <DropdownMenuItem onSelect={guard(() => fileInput.current?.click())}>
                      <Icon.files />
                      Add files
                    </DropdownMenuItem>
                    {onImportTraces && (
                      <DropdownMenuItem onSelect={onImportTraces}>
                        <Icon.observability />
                        Select from traces
                      </DropdownMenuItem>
                    )}
                  </DropdownMenuContent>
                </DropdownMenu>
                <WorkshopFundingControl disabled={pending} />
                <ul
                  aria-label="Attached files"
                  className="flex max-h-32 min-w-0 flex-1 flex-wrap gap-1.5 overflow-y-auto"
                >
                  {uploads.files.map((entry) => (
                    <Attachment
                      disabled={pending}
                      entry={entry}
                      key={entry.id}
                      onRemove={() => uploads.remove(entry.id)}
                      onRetry={() => uploads.retry(entry)}
                    />
                  ))}
                </ul>
              </div>
              <Button
                aria-label="Start workshop"
                disabled={!canStart}
                size="icon"
                title="Start workshop (Enter)"
                type="submit"
                variant="secondary"
              >
                {pending ? <Spinner className="size-4" /> : <Icon.undo />}
              </Button>
            </div>
          </div>
          <input
            accept=".csv,.tsv,.json,.jsonl,.ndjson,.parquet,.gz,.pdf,.docx,.md,.txt,.png,.jpg,.jpeg,.webp"
            aria-label="Attach source files"
            className="hidden"
            disabled={pending}
            multiple
            onChange={(event) => {
              const files = Array.from(event.target.files ?? []);
              guard(() => uploads.add(files))();
              event.target.value = "";
              textInput.current?.focus();
            }}
            ref={fileInput}
            type="file"
          />
          {dragging && (
            <div className="pointer-events-none absolute inset-0 flex items-center justify-center rounded-md border border-dashed border-primary bg-card/95">
              <span className="flex items-center gap-2 text-sm">
                <Icon.files className="size-5" />
                Drop files to attach
              </span>
            </div>
          )}
        </Card>
        {uploads.files
          .filter((entry) => entry.error)
          .map((entry) => (
            <p className="mt-2 break-words text-xs text-destructive" key={entry.id} role="alert">
              {entry.file.name}: {entry.error}
            </p>
          ))}
        {(hint || error) && (
          <p
            className={cn(
              "mt-2 text-xs",
              error || failed || tooMany ? "text-destructive" : "text-muted-foreground"
            )}
            role={error || tooMany ? "alert" : "status"}
          >
            {error || hint}
          </p>
        )}
      </form>
    </section>
  );
}
