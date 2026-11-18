import { type ReactNode, useEffect, useState } from "react";

import { Link } from "@tanstack/react-router";
import { Handle, Position, useNodeConnections } from "@xyflow/react";

import { CellLabels } from "@/components/datasets/notebook/cell-labels";
import { CellScript } from "@/components/datasets/notebook/cell-script";
import { Field } from "@/components/datasets/notebook/field";
import { RowsGrid } from "@/components/datasets/notebook/rows-grid";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Icon } from "@/components/ui/icons";
import { Spinner } from "@/components/ui/spinner";
import { type CellState, columnsOf, fitOf, intentLabel } from "@/hooks/use-datasets";
import { cn } from "@/lib/utils";
import type { Cell } from "@/openapi";

/** `train_eval`: the eval dataset that scores a training job. */
export type UsePurpose = "train" | "train_eval" | "optimise";

export interface CellActions {
  onUse: (purpose: UsePurpose) => void;
  onExport: (fmt: "jsonl" | "csv") => void;
  onIntent: (intent: "train" | "eval", trainFormat?: "sft" | "decision") => void;
}

function ExportDialog({
  title,
  rows,
  columns,
  onExport,
}: {
  title: string;
  rows: number;
  columns: number;
  onExport: (fmt: "jsonl" | "csv") => void;
}) {
  const [open, setOpen] = useState(false);
  const pick = (fmt: "jsonl" | "csv") => {
    onExport(fmt);
    setOpen(false);
  };
  return (
    <Dialog onOpenChange={setOpen} open={open}>
      <DialogTrigger asChild>
        <Button
          aria-label={`Export ${title}`}
          className="nodrag h-full rounded-none"
          onClick={(e) => e.stopPropagation()}
          size="icon-xs"
          type="button"
          variant="ghost"
        >
          <Icon.download />
        </Button>
      </DialogTrigger>
      <DialogContent onClick={(e) => e.stopPropagation()} size="sm">
        <DialogHeader>
          <DialogTitle>Export {title}</DialogTitle>
          <DialogDescription>
            {rows.toLocaleString()} rows × {columns} columns, as stored. An export is not a use.
          </DialogDescription>
        </DialogHeader>
        <DialogFooter>
          <Button onClick={() => pick("csv")} variant="secondary">
            <Icon.download />
            CSV
          </Button>
          <Button onClick={() => pick("jsonl")}>
            <Icon.download />
            JSONL
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function SectionHeader({
  label,
  meta,
  open,
  onToggle,
}: {
  label: string;
  meta?: string;
  open: boolean;
  onToggle: () => void;
}) {
  return (
    <button
      aria-expanded={open}
      className="flex min-h-8 w-full flex-wrap items-center gap-1.5 px-2.5 py-1 text-xs text-muted-foreground hover:bg-wash-raised hover:text-foreground focus-visible:outline focus-visible:outline-ring"
      onClick={(e) => {
        e.stopPropagation();
        onToggle();
      }}
      type="button"
    >
      <Icon.chevronRight
        className={cn("size-3 transition-transform duration-150", open && "rotate-90")}
      />
      <span className="pixel-label whitespace-nowrap">{label}</span>
      <span className="flex-1" />
      {meta && <span className="truncate font-mono tabular-nums">{meta}</span>}
    </button>
  );
}

const STATE_LABEL: Record<CellState, string> = {
  failed: "failed",
  ok: "",
  queued: "queued",
  running: "running",
};

const USES = [
  { format: "sft", intent: "train", key: "sft" },
  { format: "decision", intent: "train", key: "decision" },
  { format: "", intent: "eval", key: "eval" },
] as const;

type Fit = Record<string, { ok: boolean; reason: string }>;

/** The dataset's intent and whether this version fits it. With no intent chosen, the
 *  uses the rows fit, each a one-click choice while the intent can still change. */
function IntentFields({
  cell,
  fixed,
  intent,
  trainFormat,
  onIntent,
}: {
  cell: Cell;
  /** A run used this version, so its intent can no longer change. */
  fixed: boolean;
  intent: string;
  trainFormat: string;
  /** Absent while the dataset is busy or frozen. */
  onIntent?: CellActions["onIntent"];
}) {
  const fit = (cell.fit ?? {}) as unknown as Fit;
  const chosen = USES.find(
    (use) => use.intent === intent && (use.intent === "eval" || use.format === trainFormat)
  );
  if (chosen) {
    const verdict = fit[chosen.key];
    return verdict?.ok ? (
      <Field label="Intent" tone="text-success">
        <Icon.success className="size-3 shrink-0" />
        {intentLabel(chosen.intent, chosen.format)}
      </Field>
    ) : (
      <>
        <Field label="Intent">{intentLabel(chosen.intent, chosen.format)}</Field>
        <Field label="Fit" title={verdict?.reason} tone="text-warning">
          <Icon.warning className="size-3 shrink-0" />
          <span className="truncate">{verdict?.reason || "Rows do not fit"}</span>
        </Field>
        {onIntent &&
          USES.filter((use) => use !== chosen && fit[use.key]?.ok).map((use) => (
            <Button
              key={use.key}
              onClick={() => onIntent(use.intent, use.format || undefined)}
              size="xs"
              variant="outline"
            >
              Use for {intentLabel(use.intent, use.format)}
            </Button>
          ))}
      </>
    );
  }
  const fitting = USES.filter(
    (use) => (intent !== "train" || use.intent === "train") && fit[use.key]?.ok
  );
  return (
    <>
      <Field
        label="Intent"
        title={fixed ? "A run used this version, so the intent is fixed." : undefined}
        tone="text-muted-foreground"
      >
        {fixed && <Icon.lock className="size-3 shrink-0" />}
        {intentLabel(intent)}
      </Field>
      {fitting.length > 0 &&
        (onIntent ? (
          fitting.map((use) => (
            <Button
              key={use.key}
              onClick={() => onIntent(use.intent, use.format || undefined)}
              size="xs"
              variant="outline"
            >
              Use for {intentLabel(use.intent, use.format)}
            </Button>
          ))
        ) : (
          <Field label="Fits" tone="text-success">
            <Icon.success className="size-3 shrink-0" />
            {fitting.map((use) => intentLabel(use.intent, use.format)).join(", ")}
          </Field>
        ))}
    </>
  );
}

/** Shown only when the rows do not match the bound capability. */
function CapabilityMismatch({ cell, name }: { cell: Cell; name: string }) {
  const report = (cell.capabilityReport ?? {}) as { ok?: boolean; reason?: string };
  if (!name || report.ok !== false) return null;
  return (
    <Field label="Capability" title={report.reason} tone="text-warning">
      <Icon.warning className="size-3 shrink-0" />
      <span className="truncate">Does not match {name}</span>
    </Field>
  );
}

export function NotebookCell({
  datasetId,
  cell,
  intent,
  trainFormat,
  capabilityName,
  active,
  member,
  intentLocked,
  editable,
  selected,
  onSelect,
  actions,
  sourceDetails,
  preparationActions,
}: {
  datasetId: string;
  cell: Cell;
  intent: string;
  trainFormat: string;
  capabilityName: string;
  active: boolean;
  /** The cell's part of a training split, named for the reader. */
  /** A cell of another dataset, such as a partition member: its name links there. */
  member?: { datasetId: string; label: string };
  /** A run used some version of the dataset: its intent no longer changes. */
  intentLocked: boolean;
  editable: boolean;
  selected: boolean;
  onSelect: () => void;
  actions: CellActions;
  sourceDetails?: ReactNode;
  preparationActions?: ReactNode;
}) {
  const source = cell.position === 0;
  const attachment = (cell.review as { kind?: string } | undefined)?.kind === "attachment";
  const hasScript = !source && !attachment && !!cell.script;
  const ran = cell.state === "ok" && !!cell.fingerprint;
  const incoming = useNodeConnections({ handleType: "target" });
  const outgoing = useNodeConnections({ handleType: "source" });
  const [scriptOpen, setScriptOpen] = useState(!ran);
  // Intermediate versions start folded so the end of the flow stays in view.
  const [tableChoice, setTableOpen] = useState<boolean | null>(null);
  const [labelsOpen, setLabelsOpen] = useState(false);
  const terminal = outgoing.length === 0 || active;
  const tableOpen = tableChoice ?? (terminal || selected);
  // A table that selection opened stays open, with its filters, when selection moves on.
  useEffect(() => {
    if (selected && tableChoice === null) setTableOpen(true);
  }, [selected, tableChoice]);
  useEffect(() => {
    if (cell.state === "failed" || cell.state === "queued") setScriptOpen(true);
  }, [cell.state]);
  const fit = fitOf(cell);
  const columns = columnsOf(cell).length;

  const version = cell.version;
  const frameBorderWidth = active && cell.state !== "failed" && cell.state !== "running" ? 1.5 : 1;
  const stateTone =
    cell.state === "failed"
      ? "border-destructive/60"
      : cell.state === "running"
        ? "border-info/60"
        : active
          ? "border-success/60"
          : selected
            ? "border-primary/60"
            : "border-border";

  return (
    <article
      aria-current={selected ? "true" : undefined}
      aria-label={cell.title}
      className="group/cell relative px-3 py-3 outline-none focus-visible:bg-wash-raised"
      id={`cell-${cell.id}`}
      onClick={onSelect}
      onFocus={onSelect}
      onKeyDown={(e) => {
        if (e.key === "Enter" && e.target === e.currentTarget) onSelect();
      }}
    >
      {/* The chip sticks to the top of the scroll while its cell is in view; the frame
          starts half a chip lower so the border runs through the chip's middle. */}
      <div className="pointer-events-none sticky top-1 z-20 flex flex-wrap items-center justify-between gap-2 px-2.5">
        <div className="pointer-events-auto flex items-center gap-1 bg-card">
          <span
            className={cn(
              "inline-flex h-6 items-center overflow-hidden rounded-sm bg-card text-xs",
              active ? "border-[1.5px] border-success/60" : "border border-border",
              selected || source ? "text-foreground" : "text-muted-foreground"
            )}
          >
            <span className="inline-flex items-center gap-1.5 pr-1.5 pl-2">
              {active && <span className="size-1.5 rounded-xs bg-success" />}
              <button
                aria-label={`Focus version ${version}`}
                className={cn(
                  "nodrag font-mono hover:underline",
                  active && "font-semibold text-foreground"
                )}
                onClick={onSelect}
                type="button"
              >
                {version}
              </button>
              {member ? (
                <Link
                  className="nodrag hover:underline"
                  params={{ datasetId: member.datasetId }}
                  search={(previous) => ({ projectId: previous.projectId })}
                  to="/datasets/$datasetId"
                >
                  {member.label}
                </Link>
              ) : (
                <span>{source ? "Source" : cell.title}</span>
              )}
            </span>
            <span
              aria-label={`${cell.title} cell`}
              className="flex h-full items-center divide-x divide-border/70 border-l border-border/70"
              role="toolbar"
            >
              {ran && (
                <ExportDialog
                  columns={columns}
                  onExport={actions.onExport}
                  rows={cell.rows}
                  title={`${version} ${cell.title}`}
                />
              )}
            </span>
          </span>
          {cell.state === "running" ? (
            <Spinner className="size-3" />
          ) : STATE_LABEL[cell.state as CellState] ? (
            <span
              className={cn(
                "pixel-label text-xs",
                cell.state === "failed" ? "text-destructive" : "text-muted-foreground"
              )}
            >
              {STATE_LABEL[cell.state as CellState]}
            </span>
          ) : null}
        </div>
        {preparationActions}
      </div>
      <div
        className={cn(
          "relative -mt-3 w-full min-w-0 rounded-md border bg-card pt-3 transition-colors duration-200 motion-reduce:transition-none",
          stateTone
        )}
        data-cell-frame
        style={{ borderWidth: frameBorderWidth }}
      >
        <Handle
          className={cn("!size-2 !border-border !bg-card", !incoming.length && "invisible")}
          position={Position.Top}
          style={{ top: -frameBorderWidth / 2 }}
          type="target"
        />
        <Handle
          className={cn("!size-2 !border-border !bg-card", !outgoing.length && "invisible")}
          position={Position.Bottom}
          style={{ bottom: -frameBorderWidth / 2 }}
          type="source"
        />
        {hasScript && (
          <SectionHeader
            label={
              cell.transformation.execution === "platform_operations" ? "Operations" : "Script"
            }
            meta={
              cell.transformation.execution === "isolated_container" ||
              cell.transformation.execution === "platform_operations"
                ? undefined
                : "Execution not recorded"
            }
            onToggle={() => setScriptOpen((o) => !o)}
            open={scriptOpen}
          />
        )}
        {hasScript && scriptOpen && <CellScript cell={cell} key={cell.id} />}
        {cell.transformation.execution === "external_import" && (
          <p
            className="px-3 pt-1 pb-2 text-xs text-muted-foreground"
            title={cell.transformation.provenance}
          >
            External import · execution not verified by Overmind
          </p>
        )}
        {attachment && <p className="px-3 pt-1 pb-2 text-xs text-muted-foreground">{cell.note}</p>}
        {source && sourceDetails}
        {cell.state === "failed" && cell.error && (
          <div className="mx-3 mb-2.5 flex flex-col gap-1">
            <span className="pixel-label flex items-center gap-1 text-xs text-muted-foreground">
              <Icon.warning className="size-3" />
              Error
            </span>
            <pre className="whitespace-pre-wrap break-words rounded-sm border border-border px-2 py-1 font-mono text-xs text-muted-foreground">
              {cell.error}
            </pre>
          </div>
        )}

        {ran ? (
          <div className={cn(!source && (hasScript || attachment) && "border-t border-border/70")}>
            <SectionHeader
              label="Data"
              meta={`${cell.rows.toLocaleString()} rows × ${columns}`}
              onToggle={() => setTableOpen(!tableOpen)}
              open={tableOpen}
            />
            {tableOpen && (
              <RowsGrid
                cellId={cell.id}
                datasetId={datasetId}
                diff={!source}
                initialColumns={columnsOf(cell)}
                pageSize={10}
              />
            )}
            {terminal && !!cell.analytics?.families.length && (
              <div className="border-t border-border/70">
                <SectionHeader
                  label="Labels"
                  onToggle={() => setLabelsOpen((open) => !open)}
                  open={labelsOpen}
                />
                {labelsOpen && <CellLabels cell={cell} />}
              </div>
            )}
            {active && (
              <footer className="flex min-h-10 flex-wrap items-center gap-2 border-t border-border/70 px-2.5 py-2">
                <IntentFields
                  cell={cell}
                  fixed={intentLocked}
                  intent={intent}
                  onIntent={editable && !intentLocked ? actions.onIntent : undefined}
                  trainFormat={trainFormat}
                />
                <CapabilityMismatch cell={cell} name={capabilityName} />
                <span className="flex-1" />
                {intent === "train" && (
                  <Button
                    className="border-primary/40 bg-primary/10 text-primary hover:bg-primary/20"
                    disabled={
                      trainFormat ? !(cell.fit as unknown as Fit)?.[trainFormat]?.ok : !fit.ok
                    }
                    onClick={() => actions.onUse("train")}
                    size="xs"
                    variant="outline"
                  >
                    <Icon.training />
                    Train a model
                  </Button>
                )}
                {intent === "eval" && (
                  <>
                    <Button
                      className="border-primary/40 bg-primary/10 text-primary hover:bg-primary/20"
                      disabled={!fit.ok}
                      onClick={() => actions.onUse("optimise")}
                      size="xs"
                      variant="outline"
                    >
                      <Icon.optimiser />
                      Run the optimiser
                    </Button>
                    <Button
                      className="border-primary/40 bg-primary/10 text-primary hover:bg-primary/20"
                      disabled={!fit.ok}
                      onClick={() => actions.onUse("train_eval")}
                      size="xs"
                      variant="outline"
                    >
                      <Icon.training />
                      Use in a training job
                    </Button>
                  </>
                )}
              </footer>
            )}
          </div>
        ) : null}
      </div>
    </article>
  );
}
