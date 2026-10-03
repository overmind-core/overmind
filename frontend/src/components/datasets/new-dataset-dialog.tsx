import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { useNavigate } from "@tanstack/react-router";

import { Attachment } from "@/components/datasets/attachment";
import { evaluationRows } from "@/components/datasets/dataset-split";
import type { DatasetSource } from "@/components/datasets/new-dataset-button";
import {
  TraceBulkSourcePicker,
  type TraceSelectionState,
} from "@/components/traces/trace-bulk-source-picker";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { DismissibleAlert } from "@/components/ui/dismissible-alert";
import { Icon } from "@/components/ui/icons";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Spinner } from "@/components/ui/spinner";
import {
  type SplitPosition,
  type TraceSelectionSpec,
  traceSelectionBody,
  useCreateDatasetMutation,
  useCreateDatasetSplitMutation,
} from "@/hooks/use-datasets";
import { useProjectCapabilitiesQuery } from "@/hooks/use-evaluations";
import { useDatasetUploads } from "@/hooks/use-uploads";
import { errorMessage } from "@/lib/notify";
import { cn } from "@/lib/utils";
import type { Dataset, SourceRequest } from "@/openapi";

type Purpose = "train" | "eval" | "explore" | "split" | "pending";
const PURPOSES: Array<{ value: Purpose; label: string }> = [
  { label: "Choose in workshop", value: "pending" },
  { label: "Data exploration", value: "explore" },
  { label: "Evaluation", value: "eval" },
  { label: "Training", value: "train" },
  { label: "Train + eval", value: "split" },
];
const POSITIONS: Array<{ value: SplitPosition; label: string }> = [
  { label: "First rows", value: "head" },
  { label: "Last rows", value: "tail" },
  { label: "Random rows", value: "random" },
];
const AUTO_CAPABILITY = "__auto__";
const NO_CAPABILITY = "__none__";
const stripExtension = (name: string) =>
  name.replace(/\.(csv|tsv|json|jsonl|ndjson|parquet|pdf|docx|md|txt|png|jpe?g|webp)(\.gz)?$/i, "");

type Readiness = { source: SourceRequest; rows?: number } | { hint: string };

export interface NewDatasetDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  projectId: string;
  initialCapabilityId?: string;
  initialSource?: DatasetSource;
  initialFiles?: File[];
  initialTraceIds?: string[];
  initialSelection?: TraceSelectionSpec | null;
  initialSelectionCount?: number;
  onDone?: (dataset: Dataset) => void;
}

export function NewDatasetDialog({
  open,
  onOpenChange,
  projectId,
  initialCapabilityId,
  initialSource = "file",
  initialFiles,
  initialTraceIds,
  initialSelection,
  initialSelectionCount,
  onDone,
}: NewDatasetDialogProps) {
  const navigate = useNavigate();
  const fromTraces = (initialTraceIds?.length ?? 0) > 0 || !!initialSelection;
  const sourceType = fromTraces ? "traces" : initialSource;
  const [name, setName] = useState("");
  const [brief, setBrief] = useState("");
  const [nameTouched, setNameTouched] = useState(false);
  const [capabilityId, setCapabilityId] = useState(initialCapabilityId ?? AUTO_CAPABILITY);
  const [purpose, setPurpose] = useState<Purpose>("pending");
  const [percentInput, setPercentInput] = useState("30");
  const [position, setPosition] = useState<SplitPosition>("tail");
  const [picked, setPicked] = useState<TraceSelectionState>({ status: "counting" });
  const [error, setError] = useState<string | null>(null);
  const [dragging, setDragging] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const upload = useDatasetUploads();
  const create = useCreateDatasetMutation();
  const createSplit = useCreateDatasetSplitMutation();
  const capabilitiesQuery = useProjectCapabilitiesQuery(projectId);
  const capabilities = capabilitiesQuery.data?.results ?? [];
  const busy = create.isPending || createSplit.isPending;
  const evalPercent = Number(percentInput);
  const validPercent =
    percentInput.trim() !== "" &&
    Number.isInteger(evalPercent) &&
    evalPercent >= 1 &&
    evalPercent <= 99;

  // biome-ignore lint/correctness/useExhaustiveDependencies: initial props seed a new dialog session
  useEffect(() => {
    upload.reset();
    if (!open) return;
    setName("");
    setNameTouched(false);
    setCapabilityId(initialCapabilityId ?? AUTO_CAPABILITY);
    setPurpose("pending");
    setBrief("");
    setPercentInput("30");
    setPosition("tail");
    setPicked({ status: "counting" });
    setError(null);
    setDragging(false);
    if (initialFiles && initialFiles.length > 100) {
      setError("Choose up to 100 files.");
    } else if (initialFiles?.length) {
      upload.add(initialFiles);
    }
  }, [open]);

  const addFiles = (files: File[]) => {
    if (busy) return;
    if (upload.files.length + files.length > 100) {
      setError("Choose up to 100 files.");
      return;
    }
    setError(null);
    upload.add(files);
  };

  const readiness = useMemo((): Readiness => {
    if (sourceType === "file") {
      if (!upload.files.length) return { hint: "Choose files" };
      if (upload.files.some((file) => file.status === "error"))
        return { hint: "Remove or retry files with errors" };
      if (upload.files.some((file) => file.status !== "ready"))
        return { hint: "Uploading and counting rows" };
      return {
        rows: upload.files.every((file) => file.rows != null)
          ? upload.files.reduce((sum, file) => sum + (file.rows ?? 0), 0)
          : undefined,
        source: { uploads: upload.files.map((file) => file.uploadId!) },
      };
    }
    if (initialTraceIds?.length)
      return { rows: initialTraceIds.length, source: { traces: { trace_ids: initialTraceIds } } };
    if (initialSelection)
      return {
        rows: initialSelectionCount,
        source: { traces: traceSelectionBody(initialSelection) },
      };
    if (picked.status === "counting") return { hint: "Counting matching traces" };
    if (picked.status === "error") return { hint: "Couldn't count matching traces" };
    if (picked.status === "incomplete") return { hint: picked.hint };
    if (picked.count === 0) return { hint: "No traces match" };
    return { rows: picked.count, source: { traces: traceSelectionBody(picked.selection) } };
  }, [sourceType, upload.files, initialTraceIds, initialSelection, initialSelectionCount, picked]);
  const ready = "source" in readiness;
  const canStart = ready || (sourceType === "file" && upload.files.length === 0 && !!brief.trim());
  const rows = ready ? readiness.rows : undefined;
  const splitError =
    purpose === "split"
      ? !validPercent
        ? "Enter a percentage from 1 to 99."
        : rows !== undefined && rows < 2
          ? "At least two rows are needed to split."
          : null
      : null;
  const heldRows =
    rows !== undefined && rows >= 2 && validPercent ? evaluationRows(rows, evalPercent) : undefined;

  const capabilityName = capabilities.find((c) => c.id === capabilityId)?.name;
  const suggestedName =
    sourceType === "file"
      ? stripExtension(upload.files[0]?.file.name ?? "")
      : capabilityName
        ? `${capabilityName} traces`
        : "Traces";
  useEffect(() => {
    if (!nameTouched) setName(suggestedName);
  }, [nameTouched, suggestedName]);

  const finish = useCallback(
    (dataset: Dataset) => {
      onOpenChange(false);
      onDone?.(dataset);
      void navigate({
        params: { datasetId: dataset.id },
        search: { projectId },
        to: "/datasets/$datasetId",
      });
    },
    [navigate, onDone, onOpenChange, projectId]
  );

  const handleSubmit = async () => {
    if (!canStart || busy || splitError || (purpose === "split" && !ready)) return;
    setError(null);
    const base = {
      brief: brief.trim(),
      capabilityId:
        capabilityId === AUTO_CAPABILITY
          ? undefined
          : capabilityId === NO_CAPABILITY
            ? null
            : capabilityId,
      name: name.trim() || suggestedName || "Untitled dataset",
      projectId,
      source: ready ? readiness.source : undefined,
    };
    try {
      if (purpose === "split") {
        const pair = await createSplit.mutateAsync({
          ...base,
          deduplicate: true,
          evalPercent,
          position,
          source: ready ? readiness.source : {},
        });
        finish(pair.train);
      } else {
        finish(await create.mutateAsync({ ...base, intent: purpose }));
      }
    } catch (err) {
      setError(errorMessage(err, "Couldn't create the dataset."));
    }
  };

  return (
    <Dialog onOpenChange={(next) => !busy && onOpenChange(next)} open={open}>
      <DialogContent size="md">
        <DialogHeader>
          <DialogTitle>New dataset</DialogTitle>
          {fromTraces && (
            <DialogDescription>
              {initialTraceIds?.length
                ? `${initialTraceIds.length.toLocaleString()} selected ${initialTraceIds.length === 1 ? "trace" : "traces"}`
                : `${(initialSelectionCount ?? 0).toLocaleString()} matching traces`}
              {" · One row per trace"}
            </DialogDescription>
          )}
        </DialogHeader>
        <DialogBody className="flex flex-col gap-4">
          <div className="flex flex-col gap-1.5">
            <Label className="text-xs text-muted-foreground" htmlFor="dataset-name">
              Dataset name
            </Label>
            <Input
              disabled={busy}
              id="dataset-name"
              onChange={(e) => {
                setNameTouched(true);
                setName(e.target.value);
              }}
              placeholder="Dataset name"
              value={name}
            />
          </div>

          {sourceType === "file" ? (
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
          ) : !fromTraces ? (
            <TraceBulkSourcePicker onChange={setPicked} projectId={projectId} />
          ) : null}

          <div className="grid grid-cols-1 gap-x-5 gap-y-3 sm:grid-cols-2">
            <div className="flex min-w-0 flex-col gap-1.5">
              <Label className="text-xs text-muted-foreground" htmlFor="dataset-capability">
                Capability
              </Label>
              <Select disabled={busy} onValueChange={setCapabilityId} value={capabilityId}>
                <SelectTrigger className="w-full" id="dataset-capability">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value={AUTO_CAPABILITY}>Decide from the rows</SelectItem>
                  <SelectItem value={NO_CAPABILITY}>None</SelectItem>
                  {capabilities.map((capability) => (
                    <SelectItem key={capability.id} value={capability.id}>
                      {capability.name}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="flex min-w-0 flex-col gap-1.5">
              <Label className="text-xs text-muted-foreground" htmlFor="dataset-purpose">
                Purpose
              </Label>
              <Select
                disabled={busy}
                onValueChange={(value) => setPurpose(value as Purpose)}
                required
                value={purpose}
              >
                <SelectTrigger className="w-full" id="dataset-purpose">
                  <SelectValue placeholder="Select purpose" />
                </SelectTrigger>
                <SelectContent>
                  {PURPOSES.map((option) => (
                    <SelectItem key={option.value} value={option.value}>
                      {option.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          </div>

          {purpose === "split" && (
            <section aria-label="Data split" className="flex flex-col gap-3">
              <div className="grid grid-cols-1 gap-x-5 gap-y-3 sm:grid-cols-2">
                <div className="flex flex-col gap-1.5">
                  <Label className="text-xs text-muted-foreground" htmlFor="eval-share">
                    Data split
                  </Label>
                  <div className="flex items-center gap-2">
                    <Input
                      aria-describedby={splitError ? "dataset-split-error" : undefined}
                      aria-invalid={!validPercent}
                      className="w-24"
                      disabled={busy}
                      id="eval-share"
                      inputMode="numeric"
                      max={99}
                      min={1}
                      onChange={(e) => setPercentInput(e.target.value)}
                      step={1}
                      type="number"
                      value={percentInput}
                    />
                    <span className="text-sm text-muted-foreground">%</span>
                  </div>
                </div>
                <div className="flex min-w-0 flex-col gap-1.5">
                  <Label className="text-xs text-muted-foreground" htmlFor="eval-position">
                    Evaluation rows
                  </Label>
                  <Select
                    disabled={busy}
                    onValueChange={(value) => setPosition(value as SplitPosition)}
                    value={position}
                  >
                    <SelectTrigger className="w-full" id="eval-position">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      {POSITIONS.map((option) => (
                        <SelectItem key={option.value} value={option.value}>
                          {option.label}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>
              </div>
              <div
                aria-atomic="true"
                aria-live="polite"
                className="flex flex-wrap gap-x-5 gap-y-1 text-xs text-muted-foreground"
              >
                <p>
                  Training dataset{" "}
                  <span className="tabular-nums text-foreground">
                    {heldRows === undefined || rows === undefined
                      ? "—"
                      : `${(rows - heldRows).toLocaleString()} rows`}
                  </span>
                </p>
                <p>
                  Evaluation dataset{" "}
                  <span className="tabular-nums text-foreground">
                    {heldRows === undefined ? "—" : `${heldRows.toLocaleString()} rows`}
                  </span>
                </p>
              </div>
              {splitError && (
                <p className="text-xs text-destructive" id="dataset-split-error" role="alert">
                  {splitError}
                </p>
              )}
            </section>
          )}
          <div className="flex flex-col gap-1.5">
            <Label className="text-xs text-muted-foreground" htmlFor="dataset-brief">
              Intent (optional)
            </Label>
            <textarea
              className="min-h-16 max-h-40 w-full resize-y rounded-sm border border-border bg-background px-3 py-2 text-sm outline-none transition-colors focus-visible:border-primary placeholder:text-muted-foreground"
              disabled={busy}
              id="dataset-brief"
              maxLength={8000}
              onChange={(event) => setBrief(event.target.value)}
              placeholder="e.g. Prepare support conversations for fine-tuning."
              rows={2}
              value={brief}
            />
          </div>
          <DismissibleAlert message={error} variant="destructive" />
        </DialogBody>
        <DialogFooter className="flex-wrap">
          {(!ready || sourceType === "file") && (
            <span
              aria-live="polite"
              className="w-full text-xs text-muted-foreground sm:mr-auto sm:w-auto"
            >
              {ready
                ? rows === undefined
                  ? null
                  : `${rows.toLocaleString()} ${rows === 1 ? "row" : "rows"}`
                : readiness.hint}
            </span>
          )}
          <Button disabled={busy} onClick={() => onOpenChange(false)} variant="secondary">
            Cancel
          </Button>
          <Button
            disabled={!canStart || busy || !!splitError || (purpose === "split" && !ready)}
            onClick={() => void handleSubmit()}
          >
            {busy ? <Spinner className="size-4" /> : <Icon.datasetAdd />}
            {purpose === "split" ? "Create datasets" : "Create dataset"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
