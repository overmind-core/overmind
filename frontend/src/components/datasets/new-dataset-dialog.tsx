import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { useNavigate } from "@tanstack/react-router";

import {
  TraceBulkSourcePicker,
  type TraceSelectionState,
} from "@/components/traces/trace-bulk-source-picker";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { DismissibleAlert } from "@/components/ui/dismissible-alert";
import { Icon } from "@/components/ui/icons";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Progress } from "@/components/ui/progress";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { SelectableCard, SelectableCardGroup } from "@/components/ui/selectable-card";
import { Spinner } from "@/components/ui/spinner";
import { Textarea } from "@/components/ui/textarea";
import {
  type Intent,
  type SplitPosition,
  type TraceSelectionSpec,
  traceSelectionBody,
  useCreateDatasetMutation,
  useCreateDatasetSplitMutation,
} from "@/hooks/use-datasets";
import { useProjectCapabilitiesQuery } from "@/hooks/use-evaluations";
import { useFileUpload } from "@/hooks/use-uploads";
import { errorMessage } from "@/lib/notify";
import { cn } from "@/lib/utils";
import type { Dataset, SourceRequest } from "@/openapi";

type SourceTab = "file" | "paste" | "traces";
const SOURCES: Array<{ value: SourceTab; label: string; detail: string }> = [
  { detail: "CSV, TSV, JSON, JSONL or Parquet", label: "Upload file", value: "file" },
  { detail: "JSON lines, a JSON array or CSV", label: "Paste rows", value: "paste" },
  { detail: "One row per trace", label: "From traces", value: "traces" },
];
type Purpose = Intent | "propose" | "split";
const PURPOSES: Array<{ value: Purpose; label: string; detail: string }> = [
  { detail: "Set when the rows land", label: "Decide from the rows", value: "propose" },
  { detail: "Inputs with expected outputs", label: "Evaluation", value: "eval" },
  { detail: "Message transcripts", label: "Training", value: "train" },
  { detail: "One source, two datasets", label: "Train + eval", value: "split" },
];
const POSITIONS: Array<{ value: SplitPosition; label: string }> = [
  { label: "First", value: "head" },
  { label: "Last", value: "tail" },
  { label: "Random", value: "random" },
];
const DEFAULT_EVAL_PERCENT = 20;
const clampPercent = (n: number) => Math.min(99, Math.max(1, Math.round(n)));
/** Mirrors the server cut: at least one row on each side. */
const evalRows = (rows: number, percent: number) =>
  rows < 2 ? 0 : Math.min(Math.max(Math.floor((rows * percent + 50) / 100), 1), rows - 1);
const AUTO_CAPABILITY = "__auto__";
const stripExtension = (name: string) =>
  name.replace(/\.(csv|tsv|json|jsonl|ndjson|parquet)(\.gz)?$/i, "");

/** Either the source the request will carry, or the one line that says why not yet. */
type Readiness = { source: SourceRequest; rows?: number } | { hint: string };

export interface NewDatasetDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  projectId: string;
  /** Preselects the capability; the picker stays editable. */
  initialCapabilityId?: string;
  /** A file dropped on the page lands straight in the upload tab. */
  initialFile?: File | null;
  /** Ticked traces from the traces page. */
  initialTraceIds?: string[];
  /** "Select all matching" from the traces page, with the count the page showed. */
  initialSelection?: TraceSelectionSpec | null;
  initialSelectionCount?: number;
  onDone?: (dataset: Dataset) => void;
}

export function NewDatasetDialog({
  open,
  onOpenChange,
  projectId,
  initialCapabilityId,
  initialFile,
  initialTraceIds,
  initialSelection,
  initialSelectionCount,
  onDone,
}: NewDatasetDialogProps) {
  const navigate = useNavigate();
  const fromTraces = (initialTraceIds?.length ?? 0) > 0 || !!initialSelection;
  const [tab, setTab] = useState<SourceTab>(fromTraces ? "traces" : "file");
  const [name, setName] = useState("");
  const [nameTouched, setNameTouched] = useState(false);
  const [capabilityId, setCapabilityId] = useState(initialCapabilityId ?? AUTO_CAPABILITY);
  const [purpose, setPurpose] = useState<Purpose>("propose");
  // The field holds what was typed; the clamp applies to the value used and on blur.
  const [evalDraft, setEvalDraft] = useState(String(DEFAULT_EVAL_PERCENT));
  const evalPercent = clampPercent(Number(evalDraft) || DEFAULT_EVAL_PERCENT);
  const [position, setPosition] = useState<SplitPosition>("tail");
  const [file, setFile] = useState<File | null>(null);
  const [uploadId, setUploadId] = useState<string | null>(null);
  const [text, setText] = useState("");
  const [picked, setPicked] = useState<TraceSelectionState>({ status: "counting" });
  const [error, setError] = useState<string | null>(null);
  const [dragging, setDragging] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const upload = useFileUpload();
  const create = useCreateDatasetMutation();
  const createSplit = useCreateDatasetSplitMutation();
  const capabilitiesQuery = useProjectCapabilitiesQuery(projectId);
  const capabilities = capabilitiesQuery.data?.results ?? [];
  const busy = create.isPending || createSplit.isPending;

  // Reset per open so a second create starts clean; the initial props re-seed it.
  // biome-ignore lint/correctness/useExhaustiveDependencies: seeds once per open
  useEffect(() => {
    if (!open) {
      upload.cancel();
      return;
    }
    setTab(fromTraces ? "traces" : "file");
    setName("");
    setNameTouched(false);
    setCapabilityId(initialCapabilityId ?? AUTO_CAPABILITY);
    setPurpose("propose");
    setEvalDraft(String(DEFAULT_EVAL_PERCENT));
    setPosition("tail");
    setFile(null);
    setUploadId(null);
    setText("");
    setPicked({ status: "counting" });
    setError(null);
    upload.reset();
    if (initialFile) void pickFile(initialFile);
  }, [open]);

  const pickFile = useCallback(
    async (next: File) => {
      setFile(next);
      setUploadId(null);
      setError(null);
      const id = await upload.start(next);
      if (id) setUploadId(id);
    },
    [upload]
  );

  const uploadPercent = upload.progress
    ? Math.round((upload.progress.sent / Math.max(upload.progress.total, 1)) * 100)
    : null;

  const readiness = useMemo((): Readiness => {
    if (tab === "file") {
      if (!file) return { hint: "Choose a file" };
      if (upload.error) return { hint: upload.error };
      if (!uploadId) return { hint: `Uploading ${uploadPercent ?? 0}%` };
      return { source: { filename: file.name, uploadId } };
    }
    if (tab === "paste") {
      return text.trim() ? { source: { text } } : { hint: "Paste at least one row" };
    }
    if (initialTraceIds?.length) {
      return {
        rows: initialTraceIds.length,
        source: { traces: { trace_ids: initialTraceIds } },
      };
    }
    if (initialSelection) {
      return {
        rows: initialSelectionCount,
        source: { traces: traceSelectionBody(initialSelection) },
      };
    }
    if (picked.status === "counting") return { hint: "Counting matching traces" };
    if (picked.status === "error") return { hint: "Couldn't count matching traces" };
    if (picked.status === "incomplete") return { hint: picked.hint };
    if (picked.count === 0) return { hint: "No traces match" };
    return { rows: picked.count, source: { traces: traceSelectionBody(picked.selection) } };
  }, [
    tab,
    file,
    upload.error,
    uploadId,
    uploadPercent,
    text,
    initialTraceIds,
    initialSelection,
    initialSelectionCount,
    picked,
  ]);
  const tooFewToSplit = purpose === "split" && "source" in readiness && (readiness.rows ?? 2) < 2;
  const ready = "source" in readiness && !tooFewToSplit;

  // The name follows the source until the user types one.
  const capabilityName = capabilities.find((c) => c.id === capabilityId)?.name;
  const suggestedName = useMemo(() => {
    if (tab === "file") return file ? stripExtension(file.name) : "";
    if (tab === "paste") return "Pasted rows";
    return capabilityName ? `${capabilityName} traces` : "Traces";
  }, [tab, file, capabilityName]);
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

  const handleSubmit = useCallback(async () => {
    if (!ready || busy) return;
    setError(null);
    const base = {
      capabilityId: capabilityId === AUTO_CAPABILITY ? undefined : capabilityId,
      name: name.trim() || suggestedName || "Untitled dataset",
      projectId,
      source: readiness.source,
    };
    try {
      if (purpose === "split") {
        const pair = await createSplit.mutateAsync({ ...base, evalPercent, position });
        finish(pair.train);
      } else {
        const dataset = await create.mutateAsync({
          ...base,
          intent: purpose === "propose" ? undefined : purpose,
        });
        finish(dataset);
      }
    } catch (err) {
      setError(errorMessage(err, "Couldn't create the dataset."));
    }
  }, [
    ready,
    busy,
    create,
    createSplit,
    capabilityId,
    purpose,
    evalPercent,
    position,
    name,
    suggestedName,
    projectId,
    readiness,
    finish,
  ]);

  const rowsLabel = useMemo(() => {
    if (!ready || readiness.rows === undefined) return null;
    const rows = readiness.rows;
    if (purpose === "split" && rows >= 2) {
      const held = evalRows(rows, evalPercent);
      return `${(rows - held).toLocaleString()} train · ${held.toLocaleString()} eval`;
    }
    return `${rows.toLocaleString()} ${rows === 1 ? "row" : "rows"}`;
  }, [ready, readiness, purpose, evalPercent]);

  return (
    <Dialog onOpenChange={(o) => !busy && onOpenChange(o)} open={open}>
      <DialogContent size="lg">
        <DialogHeader>
          <DialogTitle>New dataset</DialogTitle>
        </DialogHeader>
        <DialogBody className="flex flex-col gap-4">
          <section className="flex flex-col gap-2">
            <Label>Source</Label>
            {fromTraces ? (
              <div className="flex flex-col gap-1 rounded-md border border-border/60 bg-background px-3 py-2.5">
                <p className="text-sm text-foreground">
                  {initialTraceIds?.length
                    ? `${initialTraceIds.length.toLocaleString()} selected ${initialTraceIds.length === 1 ? "trace" : "traces"}`
                    : `${(initialSelectionCount ?? 0).toLocaleString()} traces matching the current filters`}
                </p>
                <p className="text-xs text-muted-foreground">One row per trace</p>
              </div>
            ) : (
              <SelectableCardGroup className="grid grid-cols-1 gap-2 sm:grid-cols-3">
                {SOURCES.map((option) => (
                  <SelectableCard
                    className="flex flex-col gap-0.5 px-3 py-2"
                    key={option.value}
                    onSelect={() => setTab(option.value)}
                    role="radio"
                    selected={tab === option.value}
                  >
                    <span className="text-sm font-medium">{option.label}</span>
                    <span className="text-xs text-muted-foreground">{option.detail}</span>
                  </SelectableCard>
                ))}
              </SelectableCardGroup>
            )}

            {tab === "file" && (
              <div className="flex flex-col gap-3">
                <div
                  className={cn(
                    "flex flex-col items-center justify-center gap-1.5 rounded-md border border-dashed px-6 py-4 text-center transition-colors",
                    dragging ? "border-primary/60 bg-accent/40" : "border-border"
                  )}
                  onDragLeave={() => setDragging(false)}
                  onDragOver={(e) => {
                    e.preventDefault();
                    setDragging(true);
                  }}
                  onDrop={(e) => {
                    e.preventDefault();
                    setDragging(false);
                    const dropped = e.dataTransfer.files?.[0];
                    if (dropped) void pickFile(dropped);
                  }}
                >
                  <Icon.upload className="size-6 text-muted-foreground" />
                  {file ? (
                    <p className="font-mono text-sm text-foreground">{file.name}</p>
                  ) : (
                    <p className="text-sm text-foreground">Drop a file here</p>
                  )}
                  <p className="text-xs text-muted-foreground">CSV, TSV, JSON, JSONL or Parquet</p>
                  <input
                    accept=".csv,.tsv,.json,.jsonl,.ndjson,.parquet,.gz"
                    className="hidden"
                    onChange={(e) => {
                      const chosen = e.target.files?.[0];
                      if (chosen) void pickFile(chosen);
                      e.target.value = "";
                    }}
                    ref={fileInputRef}
                    type="file"
                  />
                  <Button
                    onClick={() => fileInputRef.current?.click()}
                    size="sm"
                    type="button"
                    variant="secondary"
                  >
                    {file ? "Choose another file" : "Choose file"}
                  </Button>
                </div>
                <div className="h-1.5">
                  {upload.progress && uploadPercent !== null && (
                    <Progress
                      label={uploadId ? "Uploaded" : `Uploading ${upload.progress.filename}`}
                      percent={uploadPercent}
                    />
                  )}
                </div>
              </div>
            )}

            {tab === "paste" && (
              <Textarea
                aria-label="Rows"
                className="min-h-28 font-mono text-xs"
                onChange={(e) => setText(e.target.value)}
                placeholder={
                  '{"input": "…", "expected_output": "…"}\none JSON object per line, a JSON array, or CSV with a header row'
                }
                spellCheck={false}
                value={text}
              />
            )}

            {tab === "traces" && !fromTraces && (
              <TraceBulkSourcePicker onChange={setPicked} projectId={projectId} />
            )}
          </section>

          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="dataset-name">Name</Label>
              <Input
                id="dataset-name"
                onChange={(e) => {
                  setNameTouched(true);
                  setName(e.target.value);
                }}
                placeholder={suggestedName || "Dataset name"}
                value={name}
              />
            </div>
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="dataset-capability">Capability</Label>
              <Select onValueChange={setCapabilityId} value={capabilityId}>
                <SelectTrigger className="w-full" id="dataset-capability">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value={AUTO_CAPABILITY}>Decide from the rows</SelectItem>
                  {capabilities.map((c) => (
                    <SelectItem key={c.id} value={c.id}>
                      {c.name}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          </div>

          <div className="flex flex-col gap-1.5">
            <div className="flex min-h-6 items-center justify-between gap-3">
              <Label>Purpose</Label>
              {purpose === "split" && (
                <div className="flex items-center gap-3">
                  <Label className="gap-1.5 text-xs text-muted-foreground" htmlFor="eval-share">
                    Eval share
                    <Input
                      className="w-14 text-right"
                      id="eval-share"
                      inputMode="numeric"
                      max={99}
                      min={1}
                      onBlur={() => setEvalDraft(String(evalPercent))}
                      onChange={(e) => setEvalDraft(e.target.value)}
                      size="xs"
                      type="number"
                      value={evalDraft}
                    />
                    %
                  </Label>
                  <div className="flex items-center gap-1.5">
                    <span className="text-xs text-muted-foreground">Eval rows</span>
                    <SelectableCardGroup aria-label="Eval rows" className="flex gap-1">
                      {POSITIONS.map((option) => (
                        <SelectableCard
                          className="px-2 py-0.5 text-xs"
                          key={option.value}
                          onSelect={() => setPosition(option.value)}
                          role="radio"
                          selected={position === option.value}
                        >
                          {option.label}
                        </SelectableCard>
                      ))}
                    </SelectableCardGroup>
                  </div>
                </div>
              )}
            </div>
            <SelectableCardGroup className="grid grid-cols-2 gap-2 sm:grid-cols-4">
              {PURPOSES.map((option) => (
                <SelectableCard
                  className="flex flex-col gap-0.5 px-3 py-2"
                  key={option.value}
                  onSelect={() => setPurpose(option.value)}
                  role="radio"
                  selected={purpose === option.value}
                >
                  <span className="text-sm font-medium">{option.label}</span>
                  <span className="text-xs text-muted-foreground">{option.detail}</span>
                </SelectableCard>
              ))}
            </SelectableCardGroup>
          </div>

          <DismissibleAlert message={error} variant="destructive" />
        </DialogBody>
        <DialogFooter className="items-center">
          <span
            className={cn(
              "mr-auto text-xs",
              tab === "file" && upload.error ? "text-destructive" : "text-muted-foreground"
            )}
          >
            {tooFewToSplit
              ? "Two rows are needed to split"
              : "hint" in readiness
                ? readiness.hint
                : rowsLabel}
          </span>
          <Button disabled={busy} onClick={() => onOpenChange(false)} variant="secondary">
            Cancel
          </Button>
          <Button disabled={!ready || busy} onClick={() => void handleSubmit()}>
            {busy ? <Spinner className="size-4" /> : <Icon.datasetAdd />}
            Create dataset
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
