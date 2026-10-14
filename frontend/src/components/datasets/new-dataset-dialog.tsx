import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { useNavigate } from "@tanstack/react-router";

import { Attachment } from "@/components/datasets/attachment";
import { evaluationRows } from "@/components/datasets/dataset-split";
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

type Purpose = "pending" | "train" | "eval" | "split";
const PURPOSES: Array<{ value: Purpose; label: string }> = [
  { label: "Decide later", value: "pending" },
  { label: "Training", value: "train" },
  { label: "Evaluation", value: "eval" },
  { label: "Split into training and evaluation", value: "split" },
];
const POSITIONS: Array<{ value: SplitPosition; label: string }> = [
  { label: "The first rows", value: "head" },
  { label: "The last rows", value: "tail" },
  { label: "Random rows", value: "random" },
];
const AUTO_CAPABILITY = "__auto__";
const NO_CAPABILITY = "__none__";
const TABLES = [".csv", ".tsv", ".json", ".jsonl", ".ndjson", ".parquet"];
const DOCUMENTS = [".pdf", ".docx", ".md", ".txt", ".png", ".jpg", ".jpeg", ".webp"];
const ACCEPT = [...TABLES, ".gz", ...DOCUMENTS].join(",");
const MIXED = "Upload tables and documents as separate datasets.";
const isDocument = (name: string) =>
  DOCUMENTS.some((suffix) => name.toLowerCase().endsWith(suffix));
const stripExtension = (name: string) =>
  name.replace(/\.(csv|tsv|json|jsonl|ndjson|parquet|pdf|docx|md|txt|png|jpe?g|webp)(\.gz)?$/i, "");

type Readiness = { source: SourceRequest; rows?: number } | { hint: string };

/** A version of another dataset in the project, landed as the new dataset's source. */
export interface VersionSource {
  datasetId: string;
  datasetName: string;
  cellId: string;
  version: string;
  rows: number;
}

export interface NewDatasetDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  projectId: string;
  initialCapabilityId?: string;
  initialTraceIds?: string[];
  initialSelection?: TraceSelectionSpec | null;
  initialSelectionCount?: number;
  initialVersion?: VersionSource;
  onDone?: (dataset: Dataset) => void;
}

export function NewDatasetDialog({
  open,
  onOpenChange,
  projectId,
  initialCapabilityId,
  initialTraceIds,
  initialSelection,
  initialSelectionCount,
  initialVersion,
  onDone,
}: NewDatasetDialogProps) {
  const navigate = useNavigate();
  const fromTraces = (initialTraceIds?.length ?? 0) > 0 || !!initialSelection;
  const fixedSource = fromTraces || !!initialVersion;
  const [name, setName] = useState("");
  const [nameTouched, setNameTouched] = useState(false);
  const [capabilityId, setCapabilityId] = useState(initialCapabilityId ?? AUTO_CAPABILITY);
  const [purpose, setPurpose] = useState<Purpose>("pending");
  const [percentInput, setPercentInput] = useState("30");
  const [position, setPosition] = useState<SplitPosition>("tail");
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
    setPercentInput("30");
    setPosition("tail");
    setError(null);
    setDragging(false);
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

  const mixed = new Set(upload.files.map((file) => isDocument(file.file.name))).size > 1;
  const readiness = useMemo((): Readiness => {
    if (initialVersion)
      return {
        rows: initialVersion.rows,
        source: {
          version: { dataset: initialVersion.datasetId, version: initialVersion.cellId },
        },
      };
    if (initialTraceIds?.length)
      return { rows: initialTraceIds.length, source: { traces: { trace_ids: initialTraceIds } } };
    if (initialSelection)
      return {
        rows: initialSelectionCount,
        source: { traces: traceSelectionBody(initialSelection) },
      };
    if (!upload.files.length) return { hint: "" };
    if (mixed) return { hint: MIXED };
    if (upload.files.some((file) => file.status === "error"))
      return { hint: "Remove or retry files with errors" };
    if (upload.files.some((file) => file.status !== "ready")) return { hint: "Uploading files…" };
    const counted = upload.files.every((file) => typeof file.rows === "number");
    return {
      rows: counted ? upload.files.reduce((sum, file) => sum + (file.rows ?? 0), 0) : undefined,
      source: { uploads: upload.files.map((file) => file.uploadId!) },
    };
  }, [
    mixed,
    upload.files,
    initialTraceIds,
    initialSelection,
    initialSelectionCount,
    initialVersion,
  ]);
  const ready = "source" in readiness;
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
  const suggestedName = initialVersion
    ? `${initialVersion.datasetName} ${initialVersion.version}`
    : !fromTraces
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
    if (!ready || busy || splitError) return;
    setError(null);
    const base = {
      capabilityId:
        capabilityId === AUTO_CAPABILITY
          ? undefined
          : capabilityId === NO_CAPABILITY
            ? null
            : capabilityId,
      name: name.trim() || suggestedName || "Untitled dataset",
      projectId,
      source: readiness.source,
    };
    try {
      if (purpose === "split") {
        const pair = await createSplit.mutateAsync({
          ...base,
          deduplicate: true,
          evalPercent,
          position,
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
      <DialogContent size="lg">
        <DialogHeader>
          <DialogTitle>New dataset</DialogTitle>
          {initialVersion && (
            <DialogDescription>
              From {initialVersion.datasetName} · {initialVersion.version} ·{" "}
              {`${initialVersion.rows.toLocaleString()} ${initialVersion.rows === 1 ? "row" : "rows"}`}
            </DialogDescription>
          )}
        </DialogHeader>
        <DialogBody className="flex flex-col gap-5">
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="dataset-name">Dataset name</Label>
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

          {!fixedSource && (
            <section
              aria-label="Upload files"
              className={cn(
                "flex flex-col gap-3 rounded-md border border-dashed p-4 transition-colors",
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
                <ul aria-label="Selected files" className="flex flex-col gap-2">
                  {upload.files.map((entry) => (
                    <Attachment
                      disabled={busy}
                      entry={entry}
                      key={entry.id}
                      onRemove={() => upload.remove(entry.id)}
                      onRetry={() => upload.retry(entry)}
                    />
                  ))}
                </ul>
              )}
              <div
                className={cn(
                  "flex items-center gap-3",
                  upload.files.length ? "justify-between" : "flex-col py-5 text-center"
                )}
              >
                {!upload.files.length && <Icon.upload className="size-6 text-muted-foreground" />}
                <div className="flex flex-col gap-1">
                  <p className="text-sm">
                    {upload.files.length ? "Drop more files here" : "Drop files here"}
                  </p>
                  <p className="text-xs text-muted-foreground">
                    CSV, TSV, JSON, JSONL, Parquet, PDF, DOCX, Markdown, text or images
                  </p>
                </div>
                <input
                  accept={ACCEPT}
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
          )}
          {fromTraces && (
            <div className="rounded-md border border-border bg-muted px-3 py-3">
              <p className="text-sm">
                {initialTraceIds?.length
                  ? `${initialTraceIds.length.toLocaleString()} selected traces`
                  : `${(initialSelectionCount ?? 0).toLocaleString()} matching traces`}
              </p>
              <p className="text-xs text-muted-foreground">One row per trace</p>
            </div>
          )}

          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="dataset-capability">Capability</Label>
              <Select disabled={busy} onValueChange={setCapabilityId} value={capabilityId}>
                <SelectTrigger className="w-full" id="dataset-capability">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value={AUTO_CAPABILITY}>
                    {initialVersion ? "Same as the source" : "Detect from the rows"}
                  </SelectItem>
                  <SelectItem value={NO_CAPABILITY}>None</SelectItem>
                  {capabilities.map((capability) => (
                    <SelectItem key={capability.id} value={capability.id}>
                      {capability.name}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="dataset-purpose">Purpose</Label>
              <Select
                disabled={busy}
                onValueChange={(value) => setPurpose(value as Purpose)}
                value={purpose}
              >
                <SelectTrigger className="w-full" id="dataset-purpose">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {PURPOSES.map((option) => (
                    <SelectItem key={option.value} value={option.value}>
                      {initialVersion && option.value === "pending"
                        ? "Same as the source"
                        : option.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          </div>

          {purpose === "split" && (
            <section
              aria-label="Data split"
              className="flex flex-col gap-4 border-t border-border pt-4"
            >
              <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                <div className="flex flex-col gap-1.5">
                  <Label htmlFor="eval-share">Evaluation share</Label>
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
                <div className="flex flex-col gap-1.5">
                  <Label htmlFor="eval-position">Take evaluation rows from</Label>
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
                className="grid grid-cols-2 gap-3 rounded-md bg-muted p-3"
              >
                <div>
                  <p className="text-xs text-muted-foreground">Training dataset</p>
                  <p className="text-sm tabular-nums">
                    {heldRows === undefined || rows === undefined
                      ? "—"
                      : `${(rows - heldRows).toLocaleString()} rows`}
                  </p>
                </div>
                <div>
                  <p className="text-xs text-muted-foreground">Evaluation dataset</p>
                  <p className="text-sm tabular-nums">
                    {heldRows === undefined ? "—" : `${heldRows.toLocaleString()} rows`}
                  </p>
                </div>
              </div>
              {splitError && (
                <p className="text-xs text-destructive" id="dataset-split-error" role="alert">
                  {splitError}
                </p>
              )}
            </section>
          )}
          <DismissibleAlert message={error} variant="destructive" />
        </DialogBody>
        <DialogFooter className="items-center">
          <span aria-live="polite" className="mr-auto text-xs text-muted-foreground">
            {ready
              ? rows === undefined || initialVersion
                ? null
                : `${rows.toLocaleString()} ${rows === 1 ? "row" : "rows"}`
              : readiness.hint}
          </span>
          <Button disabled={busy} onClick={() => onOpenChange(false)} variant="secondary">
            Cancel
          </Button>
          <Button disabled={!ready || busy || !!splitError} onClick={() => void handleSubmit()}>
            {busy ? <Spinner className="size-4" /> : <Icon.datasetAdd />}
            {purpose === "split" ? "Create datasets" : "Create dataset"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
