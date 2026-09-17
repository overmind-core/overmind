import { useEffect, useState } from "react";

import { RowsGrid } from "@/components/datasets/notebook/rows-grid";
import { ScriptCode } from "@/components/datasets/notebook/script-code";
import { Button } from "@/components/ui/button";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
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
import { Spinner } from "@/components/ui/spinner";
import { type CellState, columnsOf, fitOf } from "@/hooks/use-datasets";
import { cn } from "@/lib/utils";
import type { Cell } from "@/openapi";

/** `train_eval`: the eval dataset that scores a training job. */
export type UsePurpose = "train" | "train_eval" | "optimise";

export interface CellActions {
  onTitle: (title: string) => void;
  onScript: (script: string) => void;
  onRemove: () => void;
  onActivate: () => void;
  onRun: () => void;
  onUse: (purpose: UsePurpose) => void;
  onExport: (fmt: "jsonl" | "csv") => void;
  /** Ask the agent to make the one failing contract hold, nothing more. */
  onFix: (problem: string) => void;
  onIntent: (intent: "train" | "eval") => void;
  onCapability: (id: string | null) => void;
}

interface Suggestion {
  key: string;
  icon: IconName;
  title: string;
  hint: string;
  run: () => void;
  primary?: boolean;
}

export interface CapabilityChoice {
  id: string;
  name: string;
  /** Landing's score for this table, when it ranked the capability. */
  score?: number;
}

function TitleField({
  value,
  disabled,
  onSave,
}: {
  value: string;
  disabled: boolean;
  onSave: (next: string) => void;
}) {
  const [draft, setDraft] = useState(value);
  useEffect(() => setDraft(value), [value]);
  const commit = () => {
    const next = draft.trim();
    if (next && next !== value) onSave(next);
    else setDraft(value);
  };
  return (
    <input
      aria-label="Cell title"
      className="w-auto min-w-4 max-w-64 truncate bg-transparent text-xs text-foreground outline-none field-sizing-content focus-visible:underline"
      disabled={disabled}
      maxLength={255}
      onBlur={commit}
      onChange={(e) => setDraft(e.target.value)}
      onClick={(e) => e.stopPropagation()}
      onKeyDown={(e) => {
        if (e.key === "Enter") e.currentTarget.blur();
        if (e.key === "Escape") {
          setDraft(value);
          e.currentTarget.blur();
        }
      }}
      value={draft}
    />
  );
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
      <span className="pixel-label">{label}</span>
      <span className="flex-1" />
      {meta && <span className="font-mono tabular-nums">{meta}</span>}
    </button>
  );
}

const STATE_LABEL: Record<CellState, string> = {
  failed: "failed",
  ok: "",
  proposed: "proposed",
  queued: "queued",
  running: "running",
};

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

function intentCheck(intent: string, shape: Report, ran: boolean): Check {
  if (intent !== "train" && intent !== "eval") {
    return {
      columns: [],
      fix: "Tell Overmind in the chat which one the rows are for.",
      found: "train or eval not chosen",
      label: "Intent",
      ok: false,
      requires: "an intent, so the table has a shape to hold",
    };
  }
  const label = intent === "eval" ? "Eval table" : "Train table";
  const requires =
    intent === "eval"
      ? "an input column with a value on every row; expected_output when a reference exists"
      : "a messages column: a list of role and content turns on every row";
  const columns = intent === "eval" ? ["input", "expected_output"] : ["messages"];
  if (!ran)
    return {
      columns,
      fix: "Run the cell.",
      found: "the cell has not run",
      label,
      ok: false,
      requires,
    };
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
  let fix = "Ask the agent to shape the table.";
  if (reason.startsWith("no input column"))
    fix = "Add an input column: rename the prompt column or build it from messages.";
  else if (reason.startsWith("no messages column"))
    fix = "Add a messages column: build it from the prompt and answer columns.";
  else if (reason.startsWith("no expected_output"))
    fix = "Add an expected_output column with the reference answer.";
  else if (reason.includes("empty input")) fix = "Drop the rows whose input is empty.";
  else if (reason.includes("no usable messages"))
    fix = "Drop the rows whose messages are empty or not a list.";
  else if (reason === "no rows") fix = "The table is empty. Land more rows or loosen a filter.";
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
  let fix = `Ask the agent to align the rows with ${name}.`;
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

/** Both contracts on the frame: the chip names the first failure, the hover
 *  card says what each needs, which columns are there, what was found and
 *  what makes it pass. */
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
  /** Absent while the dataset is busy or frozen: the card then only reports. */
  actions?: Pick<CellActions, "onFix" | "onIntent" | "onCapability">;
}) {
  const reports = (cell.intentReport as Record<string, Report> | null) ?? {};
  const shape = (reports[intent] ?? {}) as Report;
  const ran = cell.state === "ok" && !!cell.fingerprint;
  const other: "train" | "eval" | null =
    intent === "train" ? "eval" : intent === "eval" ? "train" : null;
  const otherFits = other ? !!(reports[other] as Report | undefined)?.ok : false;
  const checks = [intentCheck(intent, shape, ran)];

  /** Only the moves that would actually change the verdict, most likely first. */
  function suggest(
    failing: Check,
    acts: Pick<CellActions, "onFix" | "onIntent" | "onCapability">
  ): Suggestion[] {
    const out: Suggestion[] = [];
    const capabilityFails = failing.label.endsWith(" rows");
    const intentFails = !capabilityFails && failing.label !== "Intent";
    if (failing.label === "Intent") {
      for (const choice of ["train", "eval"] as const) {
        if ((reports[choice] as Report | undefined)?.ok) {
          out.push({
            hint: `The ${choice} shape already holds on this version.`,
            icon: "target",
            key: `intent-${choice}`,
            run: () => acts.onIntent(choice),
            title: `Use it for ${choice}`,
          });
        }
      }
      return out;
    }
    if (failing.fixable === false) return out;
    out.push({
      hint: intentFails
        ? `Adds a cell so the table is a ${intent} table.`
        : `Adds a cell so every row matches ${capabilityName}.`,
      icon: "overmind",
      key: "fix",
      primary: true,
      run: () => acts.onFix(`${failing.label}: ${failing.found}`),
      title: "Ask Overmind to fix it",
    });
    if (other && otherFits) {
      out.push({
        hint: `The ${other} shape already holds on this version.`,
        icon: "target",
        key: "intent",
        run: () => acts.onIntent(other),
        title: `Use it for ${other} instead`,
      });
    }
    if (capabilityFails) {
      const current = capabilities.find((c) => c.name === capabilityName)?.score ?? 0;
      const better = capabilities
        .filter((c) => c.name !== capabilityName && (c.score ?? 0) > current)
        .slice(0, 2);
      for (const c of better) {
        out.push({
          hint: `${Math.round((c.score ?? 0) * 100)}% of rows match it, against ${Math.round(current * 100)}% for ${capabilityName}.`,
          icon: "capability",
          key: `cap-${c.id}`,
          run: () => acts.onCapability(c.id),
          title: `Switch to ${c.name}`,
        });
      }
      if (better.length === 0) {
        out.push({
          hint: `No capability matches these rows better. Only the ${intent} shape is checked then.`,
          icon: "close",
          key: "detach",
          run: () => acts.onCapability(null),
          title: "Remove the capability",
        });
      }
    }
    return out;
  }
  if (capabilityName) {
    checks.push(capabilityCheck(capabilityName, (cell.capabilityReport ?? {}) as Report, intent));
  }
  const present = new Set(columnsOf(cell).map((c) => c.name));
  const failing = checks.find((c) => !c.ok);
  const headline = !failing
    ? "Fits"
    : failing.label === "Intent"
      ? "Intent pending"
      : failing.label.endsWith(" rows")
        ? `Does not match ${capabilityName}`
        : `Not a ${intent} table`;
  const detail = failing && failing.found.length <= 48 ? failing.found : null;
  const suggestions = failing && actions ? suggest(failing, actions) : [];
  return (
    <HoverCard openDelay={250}>
      <HoverCardTrigger asChild>
        <span
          className={cn(
            "inline-flex h-6 max-w-96 cursor-default items-center gap-1 rounded-sm border px-1.5 text-xs",
            failing
              ? "border-warning/40 bg-warning/10 text-warning"
              : "border-success/40 bg-success/10 text-success"
          )}
        >
          {failing ? (
            <Icon.warning className="size-3 shrink-0" />
          ) : (
            <Icon.success className="size-3 shrink-0" />
          )}
          <span className="truncate">
            {headline}
            {detail && <span className="text-warning/80"> · {detail}</span>}
          </span>
        </span>
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
        {failing && actions && suggestions.length > 0 && (
          <div className="mt-3 flex flex-col gap-1 border-t border-border/70 pt-2">
            {suggestions.map((item) => {
              const Glyph = Icon[item.icon];
              return (
                <button
                  className={cn(
                    "flex w-full items-start gap-2 rounded-sm border px-2 py-1.5 text-left",
                    item.primary
                      ? "border-primary/40 bg-primary/10 hover:bg-primary/20"
                      : "border-border/70 hover:bg-accent/50"
                  )}
                  key={item.key}
                  onClick={item.run}
                  type="button"
                >
                  <Glyph
                    className={cn(
                      "mt-0.5 size-3.5 shrink-0",
                      item.primary ? "text-primary" : "text-muted-foreground"
                    )}
                  />
                  <span className="flex min-w-0 flex-col">
                    <span
                      className={cn("text-xs", item.primary ? "text-primary" : "text-foreground")}
                    >
                      {item.title}
                    </span>
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

export function NotebookCell({
  datasetId,
  cell,
  intent,
  capabilityName,
  capabilities,
  active,
  editable,
  running,
  selected,
  onSelect,
  actions,
}: {
  datasetId: string;
  cell: Cell;
  intent: string;
  capabilityName: string;
  capabilities: CapabilityChoice[];
  active: boolean;
  editable: boolean;
  running: boolean;
  selected: boolean;
  onSelect: () => void;
  actions: CellActions;
}) {
  const source = cell.position === 0;
  const ran = cell.state === "ok" && !!cell.fingerprint;
  const frozen = !!cell.frozen;
  const canEdit = editable && !frozen && !source;
  const [scriptOpen, setScriptOpen] = useState(!ran);
  const [tableOpen, setTableOpen] = useState(true);
  const [script, setScript] = useState(cell.script);
  useEffect(() => setScript(cell.script), [cell.script]);
  useEffect(() => {
    if (cell.state === "failed" || cell.state === "queued") setScriptOpen(true);
  }, [cell.state]);
  const dirty = script !== cell.script;
  const fit = fitOf(cell);
  const columns = columnsOf(cell).filter((c) => c.name !== "source_row").length;

  const version = cell.version;
  const stateTone =
    cell.state === "failed"
      ? "border-destructive/60"
      : cell.state === "running"
        ? "border-info/60"
        : active
          ? "border-[1.5px] border-success/60"
          : "border-border";

  return (
    <article
      aria-current={selected ? "true" : undefined}
      aria-label={cell.title}
      className="group/cell relative px-3 py-3"
      id={`cell-${cell.id}`}
      onClick={onSelect}
      onKeyDown={(e) => {
        if (e.key === "Enter" && e.target === e.currentTarget) onSelect();
      }}
    >
      {/* The chip sticks to the top of the scroll while its cell is in view; the frame
          starts half a chip lower so the border runs through the chip's middle. */}
      <div className="pointer-events-none sticky top-1 z-20 flex items-center pl-1.5">
        <div className="pointer-events-auto flex items-center gap-1 bg-card px-1">
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
                {version}
              </span>
              {source ? (
                <span>Source</span>
              ) : (
                <TitleField disabled={!canEdit} onSave={actions.onTitle} value={cell.title} />
              )}
            </span>
            <span
              aria-label={`${cell.title} cell`}
              className="flex h-full items-center divide-x divide-border/70 border-l border-border/70"
              role="toolbar"
            >
              {!source && (
                <Button
                  aria-label="Run the chain"
                  className="h-full rounded-none"
                  disabled={!editable || running}
                  onClick={(e) => {
                    e.stopPropagation();
                    if (dirty) actions.onScript(script);
                    else actions.onRun();
                  }}
                  size="icon-xs"
                  type="button"
                  variant="ghost"
                >
                  <Icon.play />
                </Button>
              )}
              {ran && (
                <ExportDialog
                  columns={columns}
                  onExport={actions.onExport}
                  rows={cell.rows}
                  title={`${version} ${cell.title}`}
                />
              )}
              {!source && (
                <ConfirmDialog
                  confirmLabel="Remove"
                  description="Every cell after it runs again without this step. Versions after this one change."
                  destructive
                  onConfirm={actions.onRemove}
                  title={`Remove ${version} ${cell.title}?`}
                  trigger={
                    <Button
                      aria-label="Remove cell"
                      className="h-full rounded-none"
                      disabled={!editable || frozen}
                      onClick={(e) => e.stopPropagation()}
                      size="icon-xs"
                      type="button"
                      variant="ghost"
                    >
                      <Icon.delete />
                    </Button>
                  }
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
          {frozen && !source && (
            <Icon.lock
              aria-label="Used by a run; frozen"
              className="size-3 text-muted-foreground"
            />
          )}
        </div>
      </div>
      <div className={cn("relative -mt-3 w-full min-w-0 rounded-md border", stateTone)}>
        {!source && (
          <div className="pt-3">
            <SectionHeader
              label="Script"
              onToggle={() => setScriptOpen((o) => !o)}
              open={scriptOpen}
            />
          </div>
        )}
        {!source && scriptOpen && (
          <div className="px-3 pt-1 pb-2.5">
            <ScriptCode
              aria-label={`Script of ${cell.title}`}
              onChange={canEdit ? setScript : undefined}
              onRun={() => {
                if (dirty) actions.onScript(script);
                else actions.onRun();
              }}
              source={script}
            />
            {dirty && (
              <div className="mt-1.5 flex justify-end gap-1">
                <Button onClick={() => setScript(cell.script)} size="xs" variant="secondary">
                  Discard
                </Button>
                <Button onClick={() => actions.onScript(script)} size="xs">
                  Save and run
                </Button>
              </div>
            )}
          </div>
        )}
        {source && <div className="h-3" />}

        {cell.state === "failed" && cell.error && (
          <div className="mx-3 mb-2.5 flex flex-col gap-1">
            <span className="pixel-label flex items-center gap-1 text-xs text-destructive">
              <Icon.warning className="size-3" />
              Error
            </span>
            <pre className="whitespace-pre-wrap break-words rounded-sm border border-destructive/40 bg-destructive/10 px-2 py-1 font-mono text-xs text-destructive">
              {cell.error}
            </pre>
          </div>
        )}

        {ran ? (
          <div className={cn(!source && "border-t border-border/70")}>
            <SectionHeader
              label="Data"
              meta={`${cell.rows.toLocaleString()} rows × ${columns}`}
              onToggle={() => setTableOpen((o) => !o)}
              open={tableOpen}
            />
            {tableOpen && (
              <RowsGrid cellId={cell.id} datasetId={datasetId} diff={!source} pageSize={10} />
            )}
            <footer className="flex h-8 items-center gap-1 border-t border-border/70 px-2.5">
              {active ? (
                <>
                  <span className="inline-flex h-6 items-center gap-1 rounded-sm border border-success/40 bg-success/10 px-1.5 text-xs text-success">
                    <Icon.success className="size-3" />
                    Active
                  </span>
                  <FitChip
                    actions={editable && !frozen ? actions : undefined}
                    capabilities={capabilities}
                    capabilityName={capabilityName}
                    cell={cell}
                    intent={intent}
                  />
                  <span className="flex-1" />
                  {intent === "train" && (
                    <Button
                      className="border-primary/40 bg-primary/10 text-primary hover:bg-primary/20"
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
        ) : null}
      </div>
    </article>
  );
}
