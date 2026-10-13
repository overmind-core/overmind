import { useState } from "react";

import { RowsGrid } from "@/components/datasets/notebook/rows-grid";
import { ScriptCode } from "@/components/datasets/notebook/script-code";
import { SourceDetails } from "@/components/datasets/notebook/source";
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
import { HoverCard, HoverCardContent, HoverCardTrigger } from "@/components/ui/hover-card";
import { Icon, type IconName } from "@/components/ui/icons";
import { columnsOf, fitOf } from "@/hooks/use-datasets";
import { cn } from "@/lib/utils";
import type { Cell } from "@/openapi";

/** `train_eval`: the eval dataset that scores a training job. */
export type UsePurpose = "train" | "train_eval" | "optimise";

interface VersionActions {
  onActivate: () => void;
  onUse: (purpose: UsePurpose) => void;
  onExport: (fmt: "jsonl" | "csv") => void;
  onIntent: (intent: "train" | "eval") => void;
  onCapability: (id: string | null) => void;
}

interface Suggestion {
  key: string;
  icon: IconName;
  title: string;
  hint: string;
  run: () => void;
}

export interface CapabilityChoice {
  id: string;
  name: string;
  /** Landing's score for this table, when it ranked the capability. */
  score?: number;
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
          className="h-full rounded-none"
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
            {rows.toLocaleString()} rows × {columns} columns, as stored. Exporting does not mark the
            version Used.
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
      className="flex h-8 w-full items-center gap-1.5 px-2.5 text-xs text-muted-foreground hover:bg-wash-raised hover:text-foreground"
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

type Report = {
  ok?: boolean;
  reason?: string;
  rows?: number;
  rows_ok?: number;
  /** false when no cell holds text, so no cell could shape the table. */
  fixable?: boolean;
};

interface Check {
  label: string;
  ok: boolean;
  /** What the contract asks of every row. */
  requires: string;
  /** Columns the contract reads, marked present or missing in the frame. */
  columns: string[];
  /** What the report measured. */
  found: string;
  /** What makes it pass. */
  fix?: string;
  /** false when nothing the agent could add would make it pass. */
  fixable?: boolean;
}

function intentCheck(intent: string, shape: Report): Check {
  if (intent !== "train" && intent !== "eval") {
    return {
      columns: [],
      fix: "Set the intent to train or eval in Dataset settings.",
      found: "train or eval not chosen",
      label: "Intent",
      ok: false,
      requires: "train or eval",
    };
  }
  const label = intent === "eval" ? "Eval table" : "Train table";
  const requires =
    intent === "eval"
      ? "an input column with a value on every row; expected_output when a reference exists"
      : "a messages column: a list of role and content turns on every row";
  const columns = intent === "eval" ? ["input", "expected_output"] : ["messages"];
  if (shape.ok) return { columns, found: "every row holds the shape", label, ok: true, requires };
  const reason = shape.reason ?? "";
  if (shape.fixable === false)
    return {
      columns,
      fix: "None. The rows hold numbers and ids only; a different source is needed.",
      fixable: false,
      found: "no text in any column",
      label,
      ok: false,
      requires,
    };
  let fix = `Add a version that gives every row the ${intent} shape.`;
  if (reason.startsWith("no input column"))
    fix = "Add an input column: rename the prompt column or build it from messages.";
  else if (reason.startsWith("no messages column"))
    fix = "Add a messages column: build it from the prompt and answer columns.";
  else if (reason.startsWith("no expected_output"))
    fix = "Add an expected_output column with the reference answer.";
  else if (reason.includes("empty input")) fix = "Drop the rows whose input is empty.";
  else if (reason.includes("cut at landing"))
    fix = "Drop the rows that landing cut: part of each transcript is missing.";
  else if (reason.includes("no usable messages") || reason.includes(" rows: "))
    fix = "Drop the rows whose messages are empty or not a list.";
  else if (reason === "no rows") fix = "The table is empty.";
  return { columns, fix, found: reason || "the shape does not hold", label, ok: false, requires };
}

function capabilityCheck(name: string, report: Report, intent: string): Check {
  const label = `${name} rows`;
  const requires =
    intent === "eval"
      ? `every input to carry the keys ${name} requires`
      : `every transcript to be ${name}'s own: its system prompt first, only its tools called`;
  const columns = intent === "eval" ? ["input"] : ["messages"];
  if (report.ok !== false) {
    return {
      columns,
      found:
        report.rows != null
          ? `${report.rows_ok ?? report.rows} of ${report.rows} rows`
          : "no contract to check",
      label,
      ok: true,
      requires,
    };
  }
  const reason = report.reason ?? "";
  let fix = `Add a version that aligns the rows with ${name}.`;
  if (reason.startsWith("system turn differs"))
    fix = `Set the first message of every row to ${name}'s system prompt.`;
  else if (reason.startsWith("undeclared tool calls"))
    fix = `Keep only rows whose tool calls use ${name}'s tools, or drop those calls.`;
  else if (reason.startsWith("missing "))
    fix = `Fill the input keys ${name} requires, or drop the rows that lack them.`;
  else if (reason.startsWith("no input column"))
    fix = `Add an input column that carries ${name}'s input keys.`;
  else if (reason.startsWith("no messages column"))
    fix = "Add a messages column: a list of role and content turns per row.";
  const found =
    report.rows != null ? `${report.rows_ok ?? 0} of ${report.rows} rows pass; ${reason}` : reason;
  return { columns, fix, found, label, ok: false, requires };
}

function CheckRow({ check, present }: { check: Check; present: Set<string> }) {
  return (
    <li className="flex items-start gap-2 text-xs">
      {check.ok ? (
        <Icon.success className="mt-0.5 size-3 shrink-0 text-success" />
      ) : (
        <Icon.warning className="mt-0.5 size-3 shrink-0 text-warning" />
      )}
      <div className="grid min-w-0 flex-1 grid-cols-[3.5rem_1fr] gap-x-2 gap-y-1">
        <span className="col-span-2 text-foreground">{check.label}</span>
        <span className="pixel-label text-muted-foreground">Needs</span>
        <span className="text-muted-foreground">{check.requires}</span>
        {check.columns.length > 0 && (
          <>
            <span className="pixel-label text-muted-foreground">Columns</span>
            <span className="flex flex-wrap gap-1">
              {check.columns.map((column) => (
                <span
                  className={cn(
                    "inline-flex items-center gap-0.5 rounded-xs border px-1 font-mono",
                    present.has(column)
                      ? "border-border/70 text-foreground"
                      : "border-warning/40 text-warning line-through"
                  )}
                  key={column}
                >
                  {column}
                </span>
              ))}
            </span>
          </>
        )}
        <span className="pixel-label text-muted-foreground">Found</span>
        <span className={check.ok ? "text-muted-foreground" : "text-warning"}>{check.found}</span>
        {check.fix && (
          <>
            <span className="pixel-label text-muted-foreground">Fix</span>
            <span className="text-foreground">{check.fix}</span>
          </>
        )}
      </div>
    </li>
  );
}

function FitChip({
  cell,
  intent,
  capabilityName,
  capabilities,
  actions,
}: {
  cell: Cell;
  intent: string;
  capabilityName: string;
  capabilities: CapabilityChoice[];
  actions?: Pick<VersionActions, "onIntent" | "onCapability">;
}) {
  const reports = (cell.intentReport as Record<string, Report> | null) ?? {};
  const shape = (reports[intent] ?? {}) as Report;
  const other: "train" | "eval" | null =
    intent === "train" ? "eval" : intent === "eval" ? "train" : null;
  const checks = [intentCheck(intent, shape)];
  if (capabilityName) {
    checks.push(capabilityCheck(capabilityName, (cell.capabilityReport ?? {}) as Report, intent));
  }
  const present = new Set(columnsOf(cell).map((c) => c.name));
  const failing = checks.find((c) => !c.ok);
  const capabilityFails = !!failing?.label.endsWith(" rows");

  const suggestions: Suggestion[] = [];
  if (failing && actions) {
    const holds = (choice: "train" | "eval") => !!(reports[choice] as Report | undefined)?.ok;
    if (failing.label === "Intent") {
      for (const choice of ["train", "eval"] as const) {
        if (holds(choice))
          suggestions.push({
            hint: `The ${choice} shape already holds on this version.`,
            icon: "target",
            key: `intent-${choice}`,
            run: () => actions.onIntent(choice),
            title: `Use it for ${choice}`,
          });
      }
    } else {
      if (other && holds(other))
        suggestions.push({
          hint: `The ${other} shape already holds on this version.`,
          icon: "target",
          key: "intent",
          run: () => actions.onIntent(other),
          title: `Use it for ${other} instead`,
        });
      if (capabilityFails) {
        const current = capabilities.find((c) => c.name === capabilityName)?.score ?? 0;
        const better = capabilities
          .filter((c) => c.name !== capabilityName && (c.score ?? 0) > current)
          .slice(0, 2);
        for (const c of better)
          suggestions.push({
            hint: `${Math.round((c.score ?? 0) * 100)}% of rows match it, against ${Math.round(current * 100)}% for ${capabilityName}.`,
            icon: "capability",
            key: `cap-${c.id}`,
            run: () => actions.onCapability(c.id),
            title: `Switch to ${c.name}`,
          });
        if (!better.length)
          suggestions.push({
            hint: `No capability matches these rows better. Only the ${intent} shape is checked then.`,
            icon: "close",
            key: "detach",
            run: () => actions.onCapability(null),
            title: "Remove the capability",
          });
      }
    }
  }

  const headline = !failing
    ? "Format valid"
    : failing.label === "Intent"
      ? "Intent pending"
      : capabilityFails
        ? `Rows do not match ${capabilityName}.`
        : `The active version does not match the ${intent} shape.`;
  return (
    <HoverCard openDelay={250}>
      <HoverCardTrigger asChild>
        <button
          className={cn(
            "nodrag inline-flex h-6 min-w-0 max-w-96 cursor-default items-center gap-1 whitespace-nowrap rounded-sm border px-1.5 text-xs",
            failing
              ? "border-warning/40 bg-warning/10 text-warning"
              : "border-success/40 bg-success/10 text-success"
          )}
          type="button"
        >
          {failing ? (
            <Icon.warning className="size-3 shrink-0" />
          ) : (
            <Icon.success className="size-3 shrink-0" />
          )}
          <span className="truncate">{headline}</span>
        </button>
      </HoverCardTrigger>
      <HoverCardContent align="start" className="w-[28rem] p-2.5">
        <ul className="flex flex-col gap-3">
          {checks.map((check) => (
            <CheckRow check={check} key={check.label} present={present} />
          ))}
          {!capabilityName && (
            <li className="flex items-start gap-2 text-xs text-muted-foreground">
              <Icon.info className="mt-0.5 size-3 shrink-0" />
              <span>No capability is attached, so only the {intent} shape is checked.</span>
            </li>
          )}
        </ul>
        {suggestions.length > 0 && (
          <div className="mt-3 flex flex-col gap-1 border-t border-border/70 pt-2">
            {suggestions.map((item) => {
              const Glyph = Icon[item.icon];
              return (
                <button
                  className="flex w-full items-start gap-2 rounded-sm border border-border/70 px-2 py-1.5 text-left hover:bg-accent/50"
                  key={item.key}
                  onClick={item.run}
                  type="button"
                >
                  <Glyph className="mt-0.5 size-3.5 shrink-0 text-muted-foreground" />
                  <span className="flex min-w-0 flex-col">
                    <span className="text-xs text-foreground">{item.title}</span>
                    <span className="text-xs text-muted-foreground">{item.hint}</span>
                  </span>
                </button>
              );
            })}
          </div>
        )}
      </HoverCardContent>
    </HoverCard>
  );
}

interface Impact {
  rows_before?: number | null;
  rows_after?: number | null;
  rows_removed?: number | null;
  rows_added?: number | null;
  identity_preserved?: boolean;
  input_evidence_removed?: number | null;
  instruction_changes?: number | null;
}

const count = (n: number) => n.toLocaleString();

function Changes({ impact }: { impact: Impact }) {
  const { rows_before: before, rows_after: after } = impact;
  const lines: Array<{ key: string; parts: string[]; warning?: boolean }> = [];
  if (before != null && after != null && before !== after)
    lines.push({ key: "rows", parts: [`${count(before)} → ${count(after)} rows`] });
  const moved = [
    impact.rows_removed ? `${count(impact.rows_removed)} removed` : "",
    impact.rows_added ? `${count(impact.rows_added)} added` : "",
  ].filter(Boolean);
  if (moved.length) lines.push({ key: "moved", parts: moved });
  if (impact.identity_preserved === false)
    lines.push({ key: "identity", parts: ["Row identity not kept"], warning: true });
  if (impact.input_evidence_removed)
    lines.push({
      key: "evidence",
      parts: [`Input evidence removed in ${count(impact.input_evidence_removed)} rows`],
      warning: true,
    });
  if (impact.instruction_changes)
    lines.push({
      key: "instructions",
      parts: [`Instructions changed in ${count(impact.instruction_changes)} rows`],
      warning: true,
    });
  if (!lines.length) return null;
  return (
    <ul aria-label="Changes" className="flex flex-col gap-0.5 px-3 pb-2.5">
      {lines.map((line) => (
        <li
          className={cn(
            "flex items-center gap-1.5 text-xs",
            line.warning ? "text-warning" : "text-muted-foreground"
          )}
          key={line.key}
        >
          {line.warning && <Icon.warning className="size-3 shrink-0" />}
          {line.parts.map((part, index) => (
            <span className="tabular-nums" key={part}>
              {index > 0 && <span aria-hidden> · </span>}
              <span>{part}</span>
            </span>
          ))}
        </li>
      ))}
    </ul>
  );
}

const CONSUMER_BUTTON = "border-primary/40 bg-primary/10 text-primary hover:bg-primary/20";

export function VersionCard({
  datasetId,
  cell,
  intent,
  capabilityName,
  capabilities,
  sourceSpec,
  active,
  editable,
  selected,
  onSelect,
  actions,
}: {
  datasetId: string;
  cell: Cell;
  intent: string;
  capabilityName: string;
  capabilities: CapabilityChoice[];
  sourceSpec: unknown;
  active: boolean;
  editable: boolean;
  selected: boolean;
  onSelect: () => void;
  actions: VersionActions;
}) {
  const source = cell.parent == null;
  const title = source ? "Source" : cell.title;
  const [scriptOpen, setScriptOpen] = useState(true);
  const [outputOpen, setOutputOpen] = useState(false);
  const [tableOpen, setTableOpen] = useState(active);
  const fit = fitOf(cell);
  const columns = columnsOf(cell).length;
  const changes = (cell.impact ?? {}) as Impact;

  return (
    <article
      aria-current={selected ? "true" : undefined}
      aria-label={`${cell.version} ${title}`}
      className="relative pt-3"
      onClickCapture={onSelect}
    >
      <div className="version-drag-handle absolute top-0 left-3 z-10 flex cursor-grab items-center gap-1 bg-card px-1 active:cursor-grabbing">
        <span
          className={cn(
            "inline-flex h-6 items-center overflow-hidden rounded-sm bg-card text-xs",
            active ? "border-[1.5px] border-success/60" : "border border-border",
            selected || source ? "text-foreground" : "text-muted-foreground"
          )}
        >
          <span className="inline-flex items-center gap-1.5 pr-1.5 pl-2">
            {active && <span className="size-1.5 rounded-xs bg-success" />}
            <span className={cn("font-mono", active && "font-semibold text-foreground")}>
              {cell.version}
            </span>
            <span className="max-w-72 truncate" title={title}>
              {title}
            </span>
          </span>
          <span className="nodrag flex h-full items-center border-l border-border/70">
            <ExportDialog
              columns={columns}
              onExport={actions.onExport}
              rows={cell.rows}
              title={`${cell.version} ${title}`}
            />
          </span>
        </span>
        {cell.frozen && (
          <span className="inline-flex items-center gap-1 text-xs text-muted-foreground">
            <Icon.lock className="size-3" />
            Used
          </span>
        )}
      </div>
      <div
        className={cn(
          "w-full min-w-0 rounded-md border bg-card pt-3 transition-colors duration-200",
          active
            ? "border-[1.5px] border-success/60"
            : selected
              ? "border-primary/60"
              : "border-border"
        )}
        data-cell-frame
      >
        {source ? (
          <SourceDetails spec={sourceSpec} />
        ) : (
          <>
            <SectionHeader
              label="Script"
              meta={cell.seconds ? `${cell.seconds.toFixed(1)}s` : undefined}
              onToggle={() => setScriptOpen((open) => !open)}
              open={scriptOpen}
            />
            {scriptOpen && (
              <div className="px-3 pt-1 pb-2.5">
                <ScriptCode aria-label={`Script of ${cell.version}`} source={cell.script} />
              </div>
            )}
            {cell.stdout && (
              <>
                <SectionHeader
                  label="Output"
                  onToggle={() => setOutputOpen((open) => !open)}
                  open={outputOpen}
                />
                {outputOpen && (
                  <pre className="nodrag nowheel mx-3 mb-2.5 max-h-48 overflow-auto whitespace-pre-wrap break-words rounded-sm border border-border/70 px-2 py-1 font-mono text-xs text-muted-foreground">
                    {cell.stdout}
                  </pre>
                )}
              </>
            )}
            <Changes impact={changes} />
          </>
        )}
        <div className="border-t border-border/70">
          <SectionHeader
            label="Data"
            meta={`${cell.rows.toLocaleString()} rows × ${columns}`}
            onToggle={() => setTableOpen((open) => !open)}
            open={tableOpen}
          />
          {tableOpen && (
            <div className="nodrag">
              <RowsGrid cellId={cell.id} datasetId={datasetId} diff={!source} />
            </div>
          )}
        </div>
        <footer className="nodrag flex min-h-8 flex-wrap items-center gap-1 border-t border-border/70 px-2.5 py-1">
          {active ? (
            <>
              <span className="inline-flex h-6 shrink-0 items-center gap-1 whitespace-nowrap rounded-sm border border-success/40 bg-success/10 px-1.5 text-xs text-success">
                <Icon.success className="size-3" />
                Active
              </span>
              <FitChip
                actions={editable ? actions : undefined}
                capabilities={capabilities}
                capabilityName={capabilityName}
                cell={cell}
                intent={intent}
              />
              <span className="flex-1" />
              {intent === "train" && (
                <Button
                  className={CONSUMER_BUTTON}
                  disabled={!fit.ok}
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
                    className={CONSUMER_BUTTON}
                    disabled={!fit.ok}
                    onClick={() => actions.onUse("optimise")}
                    size="xs"
                    variant="outline"
                  >
                    <Icon.optimiser />
                    Run the optimiser
                  </Button>
                  <Button
                    className={CONSUMER_BUTTON}
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
            </>
          ) : (
            <Button
              className="ml-auto"
              disabled={!editable}
              onClick={actions.onActivate}
              size="xs"
              variant="outline"
            >
              <Icon.pin />
              Set active
            </Button>
          )}
        </footer>
      </div>
    </article>
  );
}
