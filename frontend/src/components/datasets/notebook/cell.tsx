import { useState } from "react";

import {
  type Analytics,
  AnalyticsDetails,
  AnalyticsSummary,
  hasAnalytics,
} from "@/components/datasets/notebook/analytics";
import { PreparePromptDialog } from "@/components/datasets/notebook/prepare-prompt";
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
import { columnsOf, downloadAttachment, fitOf } from "@/hooks/use-datasets";
import { notify } from "@/lib/notify";
import { cn } from "@/lib/utils";
import type { Cell } from "@/openapi";

const count = (n: number) => n.toLocaleString();
const plural = (n: number, noun: string) => `${count(n)} ${n === 1 ? noun : `${noun}s`}`;

/** `train_eval`: the eval dataset that scores a training job. */
export type UsePurpose = "train" | "train_eval" | "optimise" | "decision";

interface VersionActions {
  onActivate: () => void;
  onUse: (purpose: UsePurpose) => void;
  onExport: (fmt: "jsonl" | "csv") => void;
  onIntent: (intent: "train" | "eval") => void;
  onCapability: (id: string | null) => void;
  onNewDataset: () => void;
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
            {plural(rows, "row")} × {plural(columns, "column")}
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
  meta?: React.ReactNode;
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

const PURPOSE: Record<string, string> = { eval: "evaluation", train: "training" };

const sentence = (text: string) => (text ? text[0].toUpperCase() + text.slice(1) : text);

function intentCheck(intent: string, shape: Report): Check {
  if (intent !== "train" && intent !== "eval") {
    return {
      columns: [],
      fix: "Ask your coding agent to shape the rows for training or evaluation and set the purpose.",
      found: "Not set",
      label: "Purpose",
      ok: false,
      requires: "Training or evaluation",
    };
  }
  const purpose = PURPOSE[intent];
  const label = `${sentence(purpose)} format`;
  const requires =
    intent === "eval"
      ? "An input on every row, and an expected_output wherever a reference answer exists"
      : "A messages column holding the conversation on every row";
  const columns = intent === "eval" ? ["input", "expected_output"] : ["messages"];
  if (shape.ok) return { columns, found: "Every row", label, ok: true, requires };
  const reason = shape.reason ?? "";
  if (shape.fixable === false)
    return {
      columns,
      fix: "Start from a source with text. These rows hold only numbers and IDs.",
      fixable: false,
      found: "No text in any column",
      label,
      ok: false,
      requires,
    };
  let fix = `Ask your coding agent to reshape the rows for ${purpose}.`;
  if (reason.startsWith("no input column"))
    fix = "Add an input column: rename the prompt column or build it from messages.";
  else if (reason.startsWith("no messages column"))
    fix = "Add a messages column built from the prompt and answer columns.";
  else if (reason.startsWith("no expected_output"))
    fix = "Add an expected_output column with the reference answers.";
  else if (reason.includes("empty input")) fix = "Drop the rows whose input is empty.";
  else if (reason.includes("cut at landing")) fix = "Drop the rows that were cut short on import.";
  else if (reason.includes("no assistant turn"))
    fix = "Drop the rows with no assistant reply, or add the reply.";
  else if (reason.includes("no usable messages") || reason.includes(" rows: "))
    fix = "Drop the rows whose messages are empty or not a list.";
  else if (reason === "no rows") fix = "The version has no rows.";
  return {
    columns,
    fix,
    found:
      sentence(
        reason
          .replace(/^(\d+) rows: /, "$1 rows have ")
          .replace("no assistant turn to train on", "no assistant reply")
      ) || "The format does not hold",
    label,
    ok: false,
    requires,
  };
}

function capabilityCheck(name: string, report: Report, intent: string): Check {
  const label = `Matches ${name}`;
  const requires =
    intent === "eval"
      ? `Every input carries the fields ${name} reads`
      : `Every conversation opens with ${name}'s system prompt and calls only its tools`;
  const columns = intent === "eval" ? ["input"] : ["messages"];
  if (report.ok !== false) {
    return {
      columns,
      found:
        report.rows != null
          ? `${(report.rows_ok ?? report.rows).toLocaleString()} of ${report.rows.toLocaleString()} rows`
          : `Nothing to check: ${name} records no prompt, tools or inputs`,
      label,
      ok: true,
      requires,
    };
  }
  const reason = report.reason ?? "";
  let fix = `Ask your coding agent to align the rows with ${name}.`;
  if (reason.startsWith("system turn differs"))
    fix = `Set the first message of every row to ${name}'s system prompt.`;
  else if (reason.startsWith("undeclared tool calls"))
    fix = `Keep only rows whose tool calls use ${name}'s tools, or drop those calls.`;
  else if (reason.startsWith("missing "))
    fix = `Fill the input fields ${name} reads, or drop the rows that lack them.`;
  else if (reason.startsWith("no input column"))
    fix = `Add an input column that carries ${name}'s input fields.`;
  else if (reason.startsWith("no messages column"))
    fix = "Add a messages column holding the conversation on every row.";
  const detail = reason
    .replace("system turn differs from the capability's", "a different system prompt")
    .replace("undeclared tool calls", `tools ${name} does not have`);
  const found =
    report.rows != null
      ? `${(report.rows_ok ?? 0).toLocaleString()} of ${report.rows.toLocaleString()} rows; ${detail}`
      : sentence(detail);
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
      <div className="grid min-w-0 flex-1 grid-cols-[4rem_1fr] gap-x-2 gap-y-1">
        <span className="col-span-2 text-foreground">{check.label}</span>
        <span className="pixel-label text-muted-foreground">Expects</span>
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
            <span className="pixel-label text-muted-foreground">To fix</span>
            <span className="text-foreground">{check.fix}</span>
          </>
        )}
      </div>
    </li>
  );
}

/** Decision rows: present when the version's rows look like decision requests. */
function decisionReport(cell: Cell): Report | null {
  const report = ((cell.intentReport ?? {}) as Record<string, Report>).decision;
  return report && report.reason !== "no decision rows" ? report : null;
}

function DecisionChip({
  report,
  intent,
  present,
  onIntent,
}: {
  report: Report;
  intent: string;
  present: Set<string>;
  onIntent?: (intent: "train" | "eval") => void;
}) {
  const pending = intent !== "train" && intent !== "eval";
  const ok = !!report.ok && !pending;
  const headline = !report.ok
    ? "Not valid decision rows"
    : pending
      ? "Purpose not set"
      : intent === "eval"
        ? "Ready for decision evaluation"
        : "Ready for decision training";
  const check: Check = {
    columns: ["input", "expected_output"],
    found: report.ok ? plural(report.rows ?? 0, "row") : sentence(report.reason ?? ""),
    label: "Decision format",
    ok: !!report.ok,
    requires: "A state and questions in input, and an answer for every question in expected_output",
    ...(report.ok ? {} : { fix: "Ask your coding agent to fix the rows named above." }),
  };
  return (
    <HoverCard openDelay={250}>
      <HoverCardTrigger asChild>
        <button
          className={cn(
            "nodrag inline-flex h-6 min-w-0 max-w-96 cursor-default items-center gap-1 whitespace-nowrap rounded-sm border px-1.5 text-xs",
            ok
              ? "border-success/40 bg-success/10 text-success"
              : "border-warning/40 bg-warning/10 text-warning"
          )}
          type="button"
        >
          {ok ? (
            <Icon.success className="size-3 shrink-0" />
          ) : (
            <Icon.warning className="size-3 shrink-0" />
          )}
          <span className="truncate">{headline}</span>
        </button>
      </HoverCardTrigger>
      <HoverCardContent align="start" className="w-[28rem] p-2.5">
        <ul className="flex flex-col gap-3">
          <CheckRow check={check} present={present} />
        </ul>
        {report.ok && pending && onIntent && (
          <div className="mt-3 flex flex-col gap-1 border-t border-border/70 pt-2">
            {(["train", "eval"] as const).map((choice) => (
              <button
                className="flex w-full items-start gap-2 rounded-sm border border-border/70 px-2 py-1.5 text-left hover:bg-accent/50"
                key={choice}
                onClick={() => onIntent(choice)}
                type="button"
              >
                <Icon.target className="mt-0.5 size-3.5 shrink-0 text-muted-foreground" />
                <span className="text-xs text-foreground">Use for {PURPOSE[choice]}</span>
              </button>
            ))}
          </div>
        )}
      </HoverCardContent>
    </HoverCard>
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
  const capabilityFails = !!failing?.label.startsWith("Matches ");

  const suggestions: Suggestion[] = [];
  if (failing && actions) {
    const holds = (choice: "train" | "eval") => !!(reports[choice] as Report | undefined)?.ok;
    if (failing.label === "Purpose") {
      for (const choice of ["train", "eval"] as const) {
        if (holds(choice))
          suggestions.push({
            hint: `This version is already in the ${PURPOSE[choice]} format.`,
            icon: "target",
            key: `intent-${choice}`,
            run: () => actions.onIntent(choice),
            title: `Use for ${PURPOSE[choice]}`,
          });
      }
    } else {
      if (other && holds(other))
        suggestions.push({
          hint: `This version is already in the ${PURPOSE[other]} format.`,
          icon: "target",
          key: "intent",
          run: () => actions.onIntent(other),
          title: `Use for ${PURPOSE[other]} instead`,
        });
      if (capabilityFails) {
        const current = capabilities.find((c) => c.name === capabilityName)?.score ?? 0;
        const better = capabilities
          .filter((c) => c.name !== capabilityName && (c.score ?? 0) > current)
          .slice(0, 2);
        for (const c of better)
          suggestions.push({
            hint: `${Math.round((c.score ?? 0) * 100)}% of rows match it; ${Math.round(current * 100)}% match ${capabilityName}.`,
            icon: "capability",
            key: `cap-${c.id}`,
            run: () => actions.onCapability(c.id),
            title: `Switch to ${c.name}`,
          });
        if (!better.length)
          suggestions.push({
            hint: `No other capability fits better. Without one, only the ${PURPOSE[intent]} format is checked.`,
            icon: "close",
            key: "detach",
            run: () => actions.onCapability(null),
            title: "Remove the capability",
          });
      }
    }
  }

  const headline = !failing
    ? `Ready for ${PURPOSE[intent]}`
    : failing.label === "Purpose"
      ? "Purpose not set"
      : capabilityFails
        ? `Does not match ${capabilityName}`
        : `Not in the ${PURPOSE[intent]} format`;
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
          {!capabilityName && PURPOSE[intent] && (
            <li className="flex items-start gap-2 text-xs text-muted-foreground">
              <Icon.info className="mt-0.5 size-3 shrink-0" />
              <span>No capability attached. Only the {PURPOSE[intent]} format is checked.</span>
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

function Changes({ impact }: { impact: Impact }) {
  const { rows_before: before, rows_after: after } = impact;
  const lines: Array<{ key: string; parts: string[]; warning?: boolean }> = [];
  const counts = [
    before != null && after != null && before !== after
      ? `${count(before)} → ${plural(after, "row")}`
      : "",
    impact.rows_removed ? `${count(impact.rows_removed)} removed` : "",
    impact.rows_added ? `${count(impact.rows_added)} added` : "",
  ].filter(Boolean);
  if (counts.length) lines.push({ key: "rows", parts: counts });
  if (impact.identity_preserved === false)
    lines.push({
      key: "identity",
      parts: ["Rows were renumbered, so they cannot be matched to the parent"],
      warning: true,
    });
  if (impact.input_evidence_removed)
    lines.push({
      key: "evidence",
      parts: [`Context removed from ${plural(impact.input_evidence_removed, "input")}`],
      warning: true,
    });
  if (impact.instruction_changes)
    lines.push({
      key: "instructions",
      parts: [`Instructions changed in ${plural(impact.instruction_changes, "row")}`],
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

interface AttachmentInfo {
  filename: string;
  rows?: number | null;
}

const CONSUMER_BUTTON = "border-primary/40 bg-primary/10 text-primary hover:bg-primary/20";

export function VersionCard({
  datasetId,
  projectId,
  cell,
  intent,
  capabilityName,
  capabilities,
  sourceSpec,
  analyticsChanged,
  active,
  editable,
  selected,
  onSelect,
  actions,
}: {
  datasetId: string;
  projectId: string;
  cell: Cell;
  intent: string;
  capabilityName: string;
  capabilities: CapabilityChoice[];
  sourceSpec: unknown;
  /** The analytics differ from the parent version's. */
  analyticsChanged: boolean;
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
  const analytics = (cell.analytics ?? {}) as Analytics;
  const [analyticsOpen, setAnalyticsOpen] = useState(source || analyticsChanged);
  const fit = fitOf(cell);
  const decision = decisionReport(cell);
  const columns = columnsOf(cell).length;
  const changes = (cell.impact ?? {}) as Impact;
  const attachment = (cell.attachment ?? {}) as Partial<AttachmentInfo>;

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
            <PreparePromptDialog cellId={cell.id} datasetId={datasetId} version={cell.version} />
            <Button
              aria-label={`New dataset from ${cell.version}`}
              className="h-full rounded-none"
              disabled={!editable}
              onClick={(e) => {
                e.stopPropagation();
                actions.onNewDataset();
              }}
              size="icon-xs"
              title="New dataset from this version"
              type="button"
              variant="ghost"
            >
              <Icon.datasetAdd />
            </Button>
            <ExportDialog
              columns={columns}
              onExport={actions.onExport}
              rows={cell.rows}
              title={`${cell.version} ${title}`}
            />
          </span>
        </span>
        {cell.frozen && (
          <span
            className="inline-flex items-center gap-1 text-xs text-muted-foreground"
            title="A training job, evaluation or optimiser run read this version."
          >
            <Icon.lock className="size-3" />
            Used
          </span>
        )}
      </div>
      <div
        className={cn(
          "w-full min-w-0 rounded-md border bg-card pt-2.5 transition-colors duration-200",
          active
            ? "border-[1.5px] border-success/60"
            : selected
              ? "border-primary/60"
              : "border-border"
        )}
        data-cell-frame
      >
        <div className="nodrag flex min-h-9 flex-wrap items-center gap-1 border-b border-border/70 px-2.5 pt-1 pb-1.5">
          {active ? (
            <>
              <span className="inline-flex h-6 shrink-0 items-center gap-1 whitespace-nowrap rounded-sm border border-success/40 bg-success/10 px-1.5 text-xs text-success">
                <Icon.success className="size-3" />
                Active
              </span>
              {decision ? (
                <DecisionChip
                  intent={intent}
                  onIntent={editable ? actions.onIntent : undefined}
                  present={new Set(columnsOf(cell).map((c) => c.name))}
                  report={decision}
                />
              ) : (
                <FitChip
                  actions={editable ? actions : undefined}
                  capabilities={capabilities}
                  capabilityName={capabilityName}
                  cell={cell}
                  intent={intent}
                />
              )}
              <span className="flex-1" />
              {decision && intent === "train" && (
                <Button
                  className={CONSUMER_BUTTON}
                  disabled={!decision.ok}
                  onClick={() => actions.onUse("decision")}
                  size="xs"
                  variant="outline"
                >
                  <Icon.training />
                  Train a decision model
                </Button>
              )}
              {!decision && intent === "train" && (
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
              {!decision && intent === "eval" && (
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
                    Score a training job
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
        </div>
        {source ? (
          <SourceDetails projectId={projectId} spec={sourceSpec} />
        ) : (
          <>
            {cell.script ? (
              <SectionHeader
                label="Script"
                meta={cell.seconds ? `${cell.seconds.toFixed(1)}s` : undefined}
                onToggle={() => setScriptOpen((open) => !open)}
                open={scriptOpen}
              />
            ) : (
              <p className="flex items-center gap-1.5 px-2.5 pt-2 pb-1 text-xs text-muted-foreground">
                <Icon.ai className="size-3 shrink-0" />
                Rows written by your coding agent
              </p>
            )}
            {cell.script && scriptOpen && (
              <div className="px-3 pt-1 pb-2.5">
                <ScriptCode aria-label={`Script of ${cell.version}`} source={cell.script} />
              </div>
            )}
            {attachment.filename && (
              <p className="flex items-center gap-1.5 px-2.5 pb-2.5 text-xs text-muted-foreground">
                <Icon.file className="size-3 shrink-0" />
                <span className="shrink-0">Attached file</span>
                <span aria-hidden>·</span>
                <button
                  className="min-w-0 truncate text-foreground hover:underline"
                  onClick={() =>
                    void downloadAttachment(datasetId, cell.id, attachment.filename!).catch((e) =>
                      notify.error(e, "Download failed")
                    )
                  }
                  type="button"
                >
                  {attachment.filename}
                </button>
                {attachment.rows != null && (
                  <>
                    <span aria-hidden>·</span>
                    <span className="shrink-0 tabular-nums">{plural(attachment.rows, "row")}</span>
                  </>
                )}
              </p>
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
            meta={`${plural(cell.rows, "row")} × ${plural(columns, "column")}`}
            onToggle={() => setTableOpen((open) => !open)}
            open={tableOpen}
          />
          {tableOpen && (
            <div className="nodrag">
              <RowsGrid cellId={cell.id} datasetId={datasetId} diff={!source} />
            </div>
          )}
        </div>
        {hasAnalytics(analytics) && (
          <div className="border-t border-border/70">
            <SectionHeader
              label="Analytics"
              meta={<AnalyticsSummary analytics={analytics} />}
              onToggle={() => setAnalyticsOpen((open) => !open)}
              open={analyticsOpen}
            />
            {analyticsOpen && <AnalyticsDetails analytics={analytics} />}
          </div>
        )}
      </div>
    </article>
  );
}
