import { type ReactNode, useMemo, useState } from "react";

import { useSearch } from "@tanstack/react-router";

import { TOOL_ICONS, toolDetail } from "@/components/agent-activity/activity-timeline";
import { TraceExecutionFlow } from "@/components/traces/trace-execution-flow";
import { ScoreReasonChip } from "@/components/traces/trace-score-chips";
import { ElbowGroupHeading, type ElbowItem, ElbowList } from "@/components/ui/elbow-list";
import { Icon } from "@/components/ui/icons";
import {
  Sheet,
  SheetBody,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import { Skeleton } from "@/components/ui/skeleton";
import { Spinner } from "@/components/ui/spinner";
import {
  deliveryLabel,
  type ExecutionStepResult,
  executionSessionScore,
  executionStepResults,
  gatedStep,
  hasCapabilityHandoff,
  outcomeDelivery,
  scoredStepResults,
  scoreRationale,
  sortExecutionsByStart,
  stepActionLabel,
  stepRoleResults,
  stepVerdictLabel,
  taskStateLabel,
  turnFailureLine,
  useConversationTurns,
} from "@/hooks/use-task-executions";
import { scoreTone, TONE_CHIP, TONE_FILL } from "@/lib/colors";
import { PROSE } from "@/lib/typography";
import { cn, scorePct } from "@/lib/utils";
import type { ConversationTurn, TaskExecutionList } from "@/openapi";

const CHIP = "chip-label inline-flex h-5 items-center rounded-sm border px-1.5 text-xs font-medium";
const SECTION_LABEL = "text-xs font-medium text-muted-foreground";

function askText(turn: ConversationTurn | undefined, fallback: TaskExecutionList): string {
  return turn?.intent?.current || turn?.intent?.text || fallback.behaviourKey || "Turn";
}

function actionTone(step: Pick<ExecutionStepResult, "score" | "passed">) {
  if (typeof step.score === "number") return scoreTone(scorePct(step.score));
  if (step.passed === true) return "success" as const;
  if (step.passed === false) return "error" as const;
  return "neutral" as const;
}

function ActionScore({
  name,
  step,
}: {
  name: string;
  step: Pick<ExecutionStepResult, "score" | "passed" | "rationale">;
}) {
  const label = stepVerdictLabel(step);
  if (label === "—" && !step.rationale?.trim()) {
    return <span className="text-xs text-muted-foreground">—</span>;
  }
  return (
    <ScoreReasonChip
      ariaLabel={`${name} ${label}`}
      label={label}
      rationale={step.rationale}
      tone={actionTone(step)}
    />
  );
}

function toolElbowItems(turn: ConversationTurn | undefined): ElbowItem[] {
  return (turn?.tools ?? []).map((tool) => ({
    detail: toolDetail({ input: tool.input, ok: !tool.failed, output: tool.output }),
    failed: tool.failed,
    icon: TOOL_ICONS[tool.name] ?? "tool",
    id: tool.id,
    label: tool.title,
  }));
}

function oneLine(text: string): string {
  return text.split(/\r?\n/, 1)[0] ?? "";
}

function ExpandableIo({
  leading,
  preview,
  body,
  previewClassName,
  ariaCurrent,
  onClick,
}: {
  leading?: ReactNode;
  preview: string;
  body: string;
  previewClassName?: string;
  ariaCurrent?: "true";
  onClick?: () => void;
}) {
  const [open, setOpen] = useState(false);
  const expandable = Boolean(body.trim());
  const Chevron = open ? Icon.chevronUp : Icon.chevronDown;
  return (
    <div className="flex min-w-0 flex-1 flex-col gap-0.5">
      <button
        aria-current={ariaCurrent}
        aria-expanded={expandable ? open : undefined}
        className="group flex min-w-0 items-center gap-2 text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/50"
        onClick={() => {
          onClick?.();
          if (expandable) setOpen((prev) => !prev);
        }}
        type="button"
      >
        {leading}
        <span className={cn("min-w-0 flex-1 truncate", previewClassName)}>{preview}</span>
        {expandable ? (
          <Chevron aria-hidden className="size-3 shrink-0 text-muted-foreground/70" />
        ) : null}
      </button>
      {expandable && open ? (
        <div className="pb-1.5 pl-1 motion-safe:animate-in motion-safe:fade-in-0 motion-safe:duration-150">
          <pre className="max-h-64 overflow-auto whitespace-pre-wrap font-mono text-xs leading-relaxed text-muted-foreground">
            {body}
          </pre>
        </div>
      ) : null}
    </div>
  );
}

function TurnActions({
  steps,
  toolItems,
}: {
  steps: ExecutionStepResult[];
  toolItems: ElbowItem[];
}) {
  const roleSteps = stepRoleResults(steps);
  const host = gatedStep(steps) ?? roleSteps.at(-1);
  if (roleSteps.length === 0 && !toolItems.length) return null;
  return (
    <div className="flex flex-col gap-1">
      {roleSteps.map((step, index) => {
        const name = stepActionLabel(step);
        const failed = gatedStep([step]) != null;
        const tools = step === host ? toolItems : [];
        const rationale = failed ? step.rationale?.trim() : "";
        return (
          <div className="flex flex-col gap-0.5" key={`${step.evaluator ?? "step"}-${index}`}>
            <ElbowGroupHeading
              failed={failed}
              label={name}
              trailing={<ActionScore name={name} step={step} />}
            />
            {rationale ? <p className={cn(PROSE, "px-2 text-sm")}>{rationale}</p> : null}
            {tools.length > 0 ? <ElbowList ariaLabel={name} items={tools} /> : null}
          </div>
        );
      })}
      {roleSteps.length === 0 && toolItems.length > 0 ? (
        <ElbowList ariaLabel="Tools" items={toolItems} />
      ) : null}
    </div>
  );
}

export function ConversationDetailSheet({
  conversationId,
  turns,
  capabilityNameById,
  focusedTurnId,
  onClose,
  onFocusTurn,
  onOpenTrace,
}: {
  conversationId: string | null;
  turns: TaskExecutionList[];
  capabilityNameById: Map<string, string>;
  focusedTurnId: string | null;
  onClose: () => void;
  onFocusTurn: (id: string) => void;
  onOpenTrace: (traceId: string) => void;
}) {
  return (
    <Sheet
      modal={false}
      onOpenChange={(next) => {
        if (!next) onClose();
      }}
      open={!!conversationId}
    >
      <SheetContent
        onEscapeKeyDown={onClose}
        onInteractOutside={(e) => e.preventDefault()}
        showOverlay={false}
        side="right"
        size="md"
      >
        {conversationId && (
          <ConversationDetailBody
            capabilityNameById={capabilityNameById}
            conversationId={conversationId}
            focusedTurnId={focusedTurnId}
            onFocusTurn={onFocusTurn}
            onOpenTrace={onOpenTrace}
            turns={turns}
          />
        )}
      </SheetContent>
    </Sheet>
  );
}

function ConversationDetailBody({
  conversationId,
  turns,
  capabilityNameById,
  focusedTurnId,
  onFocusTurn,
  onOpenTrace,
}: {
  conversationId: string;
  turns: TaskExecutionList[];
  capabilityNameById: Map<string, string>;
  focusedTurnId: string | null;
  onFocusTurn: (id: string) => void;
  onOpenTrace: (traceId: string) => void;
}) {
  const { projectId } = useSearch({ from: "/_auth" });
  const turnsQuery = useConversationTurns(conversationId, projectId);
  const byId = useMemo(
    () => new Map((turnsQuery.data ?? []).map((turn) => [turn.id, turn])),
    [turnsQuery.data]
  );
  const loading = turnsQuery.isLoading && !turnsQuery.data;
  const head = turns[0];
  const sessionScore = executionSessionScore(head);
  const taskState = byId.get(turns[turns.length - 1]?.id ?? "")?.taskState ?? null;
  const status = taskState?.status || "";
  const outstanding = taskState?.outstandingAsks[0] || taskState?.asked.at(-1) || "";
  const focused = turns.find((row) => row.id === focusedTurnId) ?? null;
  const focusedSteps = executionStepResults(focused ? byId.get(focused.id)?.stepResults : null);
  const focusedOutcome = scoredStepResults(focusedSteps).find((step) => step.role === "outcome");
  const focusedGate = gatedStep(focusedSteps);
  const focusedTraceExecutions = focused
    ? sortExecutionsByStart(turns.filter((row) => row.traceId === focused.traceId))
    : [];

  return (
    <>
      <SheetHeader>
        <SheetTitle>Conversation</SheetTitle>
        <SheetDescription>
          {turns.length} {turns.length === 1 ? "turn" : "turns"}
        </SheetDescription>
      </SheetHeader>
      <SheetBody className="flex flex-col gap-5">
        <div className="flex flex-col gap-2">
          <span className={SECTION_LABEL}>Task</span>
          <div className="flex flex-wrap items-center gap-1.5">
            {sessionScore != null && (
              <ScoreReasonChip
                ariaLabel={`Session ${scorePct(sessionScore)}%`}
                label={`Session ${scorePct(sessionScore)}%`}
                rationale={head.sessionRationale}
                tone={scoreTone(scorePct(sessionScore))}
              />
            )}
            {status && (
              <span
                className={cn(CHIP, status === "outstanding" ? TONE_CHIP.error : TONE_CHIP.success)}
              >
                {taskStateLabel(status)}
              </span>
            )}
          </div>
          {outstanding && status === "outstanding" && <p className="text-sm">{outstanding}</p>}
          {(taskState?.reason || head?.sessionRationale) && (
            <p className="text-xs text-muted-foreground">
              {taskState?.reason || head.sessionRationale}
            </p>
          )}
        </div>

        <div className="flex flex-col gap-1">
          <span className={SECTION_LABEL}>Thread</span>
          {loading ? (
            <Skeleton className="h-24 w-full" />
          ) : (
            <ol className="flex flex-col">
              {turns.map((row, index) => {
                const turn = byId.get(row.id);
                const steps = executionStepResults(turn?.stepResults);
                const delivery = outcomeDelivery(steps);
                const label = deliveryLabel(delivery);
                const score = row.successScore;
                const scoringPending = score == null && row.scoringPending;
                const active = row.id === focusedTurnId;
                const failure = turnFailureLine(steps);
                const gate = gatedStep(steps);
                const toolItems = toolElbowItems(turn);
                const ask = askText(turn, row);
                const input = turn?.inputText ?? "";
                const output = (turn?.outputText ?? "").trim();
                const outputPending = !output && turnsQuery.isLoading;
                return (
                  <li className="relative flex gap-3" key={row.id}>
                    <div className="flex flex-col items-center">
                      <span
                        aria-hidden
                        className={cn(
                          "mt-2 size-2 shrink-0 rounded-xs",
                          score == null ? TONE_FILL.neutral : TONE_FILL[scoreTone(scorePct(score))]
                        )}
                      />
                      {index < turns.length - 1 && (
                        <span aria-hidden className="w-px flex-1 bg-border" />
                      )}
                    </div>
                    <div
                      className={cn(
                        "mb-2 flex min-w-0 flex-1 flex-col gap-1.5 rounded-sm border border-transparent px-1.5 py-1",
                        "hover:bg-wash-subtle",
                        active && "border-border bg-wash-subtle"
                      )}
                    >
                      <div className="flex items-start gap-2">
                        <ExpandableIo
                          ariaCurrent={active ? "true" : undefined}
                          body={input || ask}
                          leading={
                            <span className="text-xs tabular-nums text-muted-foreground">
                              T{index + 1}
                            </span>
                          }
                          onClick={() => onFocusTurn(row.id)}
                          preview={ask}
                          previewClassName="text-sm"
                        />
                        {score != null && (
                          <ScoreReasonChip
                            ariaLabel={`Turn ${scorePct(score)}%`}
                            label={`${scorePct(score)}%`}
                            onClick={() => onFocusTurn(row.id)}
                            rationale={gate?.rationale || scoreRationale(steps)}
                            tone={scoreTone(scorePct(score))}
                          />
                        )}
                        {scoringPending && (
                          <span className="flex h-5 items-center" role="status">
                            <Spinner size="sm" />
                            <span className="sr-only">Scoring</span>
                          </span>
                        )}
                      </div>
                      {label ? (
                        <span className="text-xs text-muted-foreground">{label}</span>
                      ) : null}
                      {failure ? <p className={cn(PROSE, "text-sm")}>{failure}</p> : null}
                      <TurnActions steps={steps} toolItems={toolItems} />
                      {outputPending && !output ? <Skeleton className="h-7 w-full" /> : null}
                      {output ? (
                        <ExpandableIo
                          body={output}
                          leading={<span className={SECTION_LABEL}>Output</span>}
                          preview={oneLine(output)}
                          previewClassName="text-sm text-muted-foreground"
                        />
                      ) : null}
                    </div>
                  </li>
                );
              })}
            </ol>
          )}
        </div>

        {focused && (
          <div className="flex flex-col gap-2 border-t border-border/70 pt-4">
            <span className={SECTION_LABEL}>Turn evidence</span>
            {focusedGate?.rationale && (
              <p className="whitespace-pre-wrap break-words text-sm">{focusedGate.rationale}</p>
            )}
            {focusedOutcome?.rationale &&
              focusedOutcome.rationale.trim() !== focusedGate?.rationale?.trim() && (
                <p className="whitespace-pre-wrap break-words text-sm">
                  {focusedOutcome.rationale}
                </p>
              )}
            {hasCapabilityHandoff(focusedTraceExecutions) && (
              <div className="flex flex-col gap-1">
                <span className={SECTION_LABEL}>Trace flow</span>
                <TraceExecutionFlow
                  capabilityNameById={capabilityNameById}
                  currentId={focused.id}
                  executions={focusedTraceExecutions}
                  onSelect={(row) => onFocusTurn(row.id)}
                />
              </div>
            )}
            {focused.traceId && (
              <button
                className="inline-flex items-center gap-1.5 text-xs text-muted-foreground hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/50"
                onClick={() => onOpenTrace(focused.traceId)}
                type="button"
              >
                Open trace
                <Icon.chevronRight aria-hidden className="size-3.5" />
              </button>
            )}
          </div>
        )}
      </SheetBody>
    </>
  );
}
