import { useState } from "react";

import { Handle, type NodeProps, Position } from "@xyflow/react";

import { getModelProviderInfo } from "@/components/model-provider";
import { getProviderIcon, ModelProviderChip } from "@/components/model-provider-chip";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Icon } from "@/components/ui/icons";
import { cn } from "@/lib/utils";
import type { EvalMatrix } from "@/openapi";
import type {
  ActionGroupNodeData,
  ActionNodeData,
  BranchControls,
  EntryNodeData,
  EvaluationScopesNodeData,
  OutcomeNodeData,
  StepNodeData,
  TerminalNodeData,
} from "./buildTrajectoryGraph";
import { TrajectoryEvaluationList } from "./TrajectoryEvaluationList";
import styles from "./TrajectoryNodes.module.css";
import { TrajectoryScoredCount } from "./TrajectoryRunScores";

const HANDLE_CLASS =
  "!size-2 !rounded-xs !border !border-background !shadow-none transition-colors";

const NODE_TONES = {
  model: "bg-cat-4/15 text-cat-4",
  outcome: "bg-cat-5/15 text-cat-5",
  result: "bg-cat-2/15 text-cat-2",
  step: "bg-cat-1/15 text-cat-1",
  task: "bg-primary/15 text-primary",
  tool: "bg-cat-3/15 text-cat-3",
};

const TERMINAL_META: Record<
  string,
  { label: string; variant: "success" | "neutral" | "warning" | "error" }
> = {
  emits_record: { label: "Emits record", variant: "success" },
  error_exit: { label: "Error exit", variant: "error" },
  escalates: { label: "Escalates", variant: "warning" },
  returns_empty: { label: "Returns empty", variant: "neutral" },
};

function NodeCard({
  icon,
  controls,
  title,
  subtitle,
  headerDetail,
  footer,
  width,
  tone = "step",
  dotClass,
  hasTarget = true,
  hasSource = true,
  sourcePosition = Position.Right,
  targetPosition = Position.Left,
  actionHandle = false,
  returnTarget = false,
  returnSource = false,
  selected,
  children,
  evaluations,
  evaluationLabel,
  emptyEvaluationLabel,
}: {
  icon?: React.ReactNode;
  controls?: React.ReactNode;
  title: string;
  subtitle?: string;
  headerDetail?: React.ReactNode;
  footer?: React.ReactNode;
  width: number;
  tone?: keyof typeof NODE_TONES;
  dotClass: string;
  hasTarget?: boolean;
  hasSource?: boolean;
  sourcePosition?: Position;
  targetPosition?: Position;
  actionHandle?: boolean;
  returnTarget?: boolean;
  returnSource?: boolean;
  selected?: boolean;
  children?: React.ReactNode;
  evaluations?: EvalMatrix[];
  evaluationLabel?: string;
  emptyEvaluationLabel?: string;
}) {
  const [evaluationsOpen, setEvaluationsOpen] = useState(false);
  return (
    <Collapsible
      className="relative"
      onOpenChange={setEvaluationsOpen}
      open={evaluationsOpen}
      style={{ width }}
    >
      <Card
        className={cn(
          "group relative flex flex-col overflow-hidden text-left transition-colors",
          // Shadows are off system-wide (`--shadow-*: none`), so selection reads
          // through border + ring.
          selected && "border-ring ring-1 ring-ring"
        )}
        style={{ width }}
      >
        {hasTarget && (
          <Handle className={cn(HANDLE_CLASS, dotClass)} position={targetPosition} type="target" />
        )}
        {hasSource && sourcePosition !== Position.Bottom && (
          <Handle className={cn(HANDLE_CLASS, dotClass)} position={sourcePosition} type="source" />
        )}

        {actionHandle && (
          <Handle
            className={cn(HANDLE_CLASS, dotClass)}
            id="actions"
            position={Position.Bottom}
            type="source"
          />
        )}

        {returnTarget && (
          <Handle
            className={cn(HANDLE_CLASS, dotClass)}
            id="retry"
            position={Position.Top}
            type="target"
          />
        )}
        {returnTarget && (
          <Handle
            className={cn(HANDLE_CLASS, dotClass)}
            id="return"
            position={Position.Bottom}
            type="target"
          />
        )}
        {returnSource && (
          <Handle
            className={cn(HANDLE_CLASS, dotClass)}
            id="return"
            position={Position.Right}
            type="source"
          />
        )}

        <header className={cn(styles.header, "space-y-3 bg-wash-raised px-4 py-3")}>
          <div className="flex items-center gap-3">
            {icon && (
              <span
                className={cn(
                  "flex size-8 shrink-0 items-center justify-center rounded-sm border border-current/20",
                  NODE_TONES[tone]
                )}
              >
                {icon}
              </span>
            )}
            <div className="min-w-0 flex-1">
              <div className="break-words text-sm font-semibold leading-5 text-foreground">
                {title}
              </div>
              {subtitle && (
                <p className="mt-1 truncate text-xs text-muted-foreground" title={subtitle}>
                  {subtitle}
                </p>
              )}
            </div>
            {footer}
            {controls && <div className="shrink-0">{controls}</div>}
          </div>
          {headerDetail}
        </header>
        {children ? <div className="space-y-3 p-4 text-left">{children}</div> : null}

        {evaluations && !evaluations.length && (
          <p
            className="flex items-center gap-2 border-t border-border/70 bg-wash-subtle px-4 py-3 text-xs text-muted-foreground"
            title={emptyEvaluationLabel}
          >
            <Icon.evaluations className="size-3" />0 evaluations
          </p>
        )}
        {!!evaluations?.length && (
          <CollapsibleTrigger
            aria-label={`${evaluationsOpen ? "Hide" : "Show"} ${evaluations.length} evaluation${evaluations.length === 1 ? "" : "s"}`}
            className="nodrag nopan flex w-full items-center gap-2 border-t border-border/70 bg-wash-subtle px-4 py-3 text-left text-xs text-muted-foreground hover:bg-accent hover:text-foreground focus-visible:outline focus-visible:outline-2 focus-visible:outline-ring focus-visible:-outline-offset-2"
            onClick={(event) => event.stopPropagation()}
          >
            <Icon.evaluations className="size-3.5" />
            <span className="flex-1">
              {evaluations.length} evaluation{evaluations.length === 1 ? "" : "s"}
            </span>
            <TrajectoryScoredCount names={evaluations.map((row) => row.name)} />
            {evaluationsOpen ? (
              <Icon.chevronUp className="size-3.5" />
            ) : (
              <Icon.chevronDown className="size-3.5" />
            )}
          </CollapsibleTrigger>
        )}
      </Card>
      {!!evaluations?.length && (
        <CollapsibleContent className="mt-2">
          <TrajectoryEvaluationList
            label={evaluationLabel ?? "On span completion"}
            rows={evaluations}
          />
        </CollapsibleContent>
      )}
      {hasSource && sourcePosition === Position.Bottom && (
        <Handle
          className={cn(HANDLE_CLASS, dotClass)}
          id="outcomes"
          position={sourcePosition}
          type="source"
        />
      )}
    </Collapsible>
  );
}

function PixelFooter({ children }: { children: React.ReactNode }) {
  return (
    <span className="shrink-0 rounded-sm bg-card px-2 py-1 text-xs leading-4 text-muted-foreground">
      {children}
    </span>
  );
}

function BranchToggle({ id, data, label }: { id: string; data: BranchControls; label?: string }) {
  if (!data.childCount) return null;
  return (
    <Button
      aria-expanded={!data.collapsed}
      aria-label={label || `${data.collapsed ? "Expand" : "Collapse"} branches from ${id}`}
      className="nodrag nopan"
      onClick={(event) => {
        event.stopPropagation();
        data.onToggle?.(id);
      }}
      size="icon-sm"
      variant="ghost"
    >
      {data.collapsed ? <Icon.chevronRight /> : <Icon.chevronDown />}
    </Button>
  );
}

function EntryNode({ data, id, selected }: NodeProps) {
  const d = data as unknown as EntryNodeData;
  return (
    <NodeCard
      controls={(d.childCount ?? 0) > 1 ? <BranchToggle data={d} id={id} /> : undefined}
      dotClass="!bg-primary/80"
      emptyEvaluationLabel="No saved whole-run evaluations"
      evaluationLabel="Whole-run evaluations · on completion"
      evaluations={d.evaluations}
      footer={<PixelFooter>Entry</PixelFooter>}
      hasTarget={false}
      icon={<Icon.agent className="size-4" />}
      selected={selected}
      subtitle={d.entrypoint || undefined}
      title={d.capabilityName || "Capability"}
      tone="task"
      width={320}
    >
      <Preview text={d.task} />
      <section className="text-xs text-muted-foreground">
        {d.pathCount} mapped execution path{d.pathCount === 1 ? "" : "s"}
      </section>
    </NodeCard>
  );
}

function Preview({ text, label }: { text: string; label?: string }) {
  if (!text) return null;
  return (
    <section>
      {label && <p className="mb-1 text-xs text-muted-foreground">{label}</p>}
      <p
        className="nodrag nopan nowheel max-h-28 overflow-y-auto whitespace-pre-wrap break-words text-xs leading-5 text-foreground focus-visible:outline focus-visible:outline-2 focus-visible:outline-ring"
        // biome-ignore lint/a11y/noNoninteractiveTabindex: keyboard users must be able to scroll long source text on the canvas.
        tabIndex={0}
      >
        {text}
      </p>
    </section>
  );
}

function FieldPreview({ label, text }: { label: string; text: string }) {
  if (!text) return null;
  return (
    <div className="space-y-1 text-xs leading-5">
      <span className="block text-muted-foreground">{label}</span>
      <p
        className="nodrag nopan nowheel max-h-24 overflow-y-auto whitespace-pre-wrap break-words text-foreground focus-visible:outline focus-visible:outline-2 focus-visible:outline-ring"
        // biome-ignore lint/a11y/noNoninteractiveTabindex: keyboard users must be able to scroll long source text on the canvas.
        tabIndex={0}
      >
        {text}
      </p>
    </div>
  );
}

function SystemPrompt({ text, excerpt }: { text: string; excerpt: boolean }) {
  const label = excerpt ? "System prompt (excerpt)" : "System prompt";
  if (!text) return null;
  return (
    <Dialog>
      <DialogTrigger asChild>
        <button
          aria-label={`Expand ${label.toLowerCase()}`}
          className="nodrag nopan w-full min-w-0 rounded-sm text-left focus-visible:outline focus-visible:outline-2 focus-visible:outline-ring"
          onClick={(event) => event.stopPropagation()}
          type="button"
        >
          <span className="mb-2 flex items-center justify-between gap-2 text-xs text-muted-foreground">
            {excerpt ? "Instructions · excerpt" : "Instructions"}
            <Icon.expand className="size-3 shrink-0" />
          </span>
          <span className="block rounded-sm bg-code-surface p-3">
            <span className="line-clamp-3 whitespace-pre-wrap break-words text-xs leading-5 text-foreground">
              {text}
            </span>
          </span>
        </button>
      </DialogTrigger>
      <DialogContent
        className="nodrag nopan nowheel"
        onClick={(event) => event.stopPropagation()}
        size="lg"
      >
        <DialogHeader>
          <DialogTitle>{label}</DialogTitle>
          <DialogDescription>
            {excerpt
              ? "Only an excerpt was captured from the capability source."
              : "Captured from the capability source."}
          </DialogDescription>
        </DialogHeader>
        <DialogBody>
          <pre className="whitespace-pre-wrap break-words font-mono text-xs leading-relaxed">
            {text}
          </pre>
        </DialogBody>
      </DialogContent>
    </Dialog>
  );
}

function StepNode({ data, id, selected }: NodeProps) {
  const d = data as unknown as StepNodeData;
  const shared = d.pathNames.length > 1;
  const invocation = d.actor === "model";
  const displayedModel = d.model || d.modelResolution?.defaultModel || "";
  const provider = getModelProviderInfo(displayedModel);
  const ProviderIcon = getProviderIcon(provider.id);
  return (
    <NodeCard
      actionHandle={d.hasActions}
      controls={(d.childCount ?? 0) > 1 ? <BranchToggle data={d} id={id} /> : undefined}
      dotClass={invocation ? "!bg-cat-4" : "!bg-cat-1"}
      evaluationLabel="Step evaluations · on completion"
      evaluations={d.evaluations ?? []}
      footer={invocation ? undefined : <PixelFooter>{shared ? "Shared step" : "Step"}</PixelFooter>}
      headerDetail={
        invocation ? (
          <div className="space-y-2" title={d.modelResolution?.selection}>
            {displayedModel ? (
              <ModelProviderChip
                className="w-full h-8 rounded-sm border-border/60 bg-card px-3 [&>span]:text-foreground"
                model={displayedModel}
                showProvider={false}
              />
            ) : (
              <span className="flex items-center gap-2 text-xs text-muted-foreground">
                <Icon.model className="size-3.5" />
                {d.modelResolution?.selection ? "Selected at runtime" : "Model not captured"}
              </span>
            )}
            {!d.model && displayedModel && (
              <span className="text-xs text-muted-foreground">Source default</span>
            )}
          </div>
        ) : undefined
      }
      icon={
        invocation ? (
          ProviderIcon ? (
            <ProviderIcon className="size-5" />
          ) : (
            <Icon.model className="size-4" />
          )
        ) : (
          <Icon.algorithm className="size-4" />
        )
      }
      returnTarget
      selected={selected}
      title={
        invocation ? (provider.id === "unknown" ? "Model call" : provider.providerLabel) : d.label
      }
      tone={invocation ? "model" : "step"}
      width={320}
    >
      {invocation && d.modelResolution?.selection && (
        <Preview label="Model selection" text={d.modelResolution.selection} />
      )}
      <Preview text={invocation ? d.label : d.contract?.action || ""} />
      <section className="space-y-3 rounded-sm bg-wash-subtle p-3">
        <FieldPreview label="Input" text={d.contract?.input || d.inputs.join(", ")} />
        <FieldPreview
          label="Output"
          text={d.contract?.output || d.outputs.map((output) => output.label).join(", ")}
        />
      </section>
      {invocation && d.systemPrompt && (
        <section>
          <SystemPrompt excerpt={d.promptIsExcerpt} text={d.systemPrompt} />
        </section>
      )}
      {d.condition && (
        <section>
          <FieldPreview label="When" text={d.condition} />
        </section>
      )}
    </NodeCard>
  );
}

function ActionGroupNode({ data, id, selected }: NodeProps) {
  const d = data as unknown as ActionGroupNodeData;
  return (
    <div
      className={cn(
        styles.header,
        "relative w-[320px] rounded-md border border-border bg-wash-raised",
        selected && "border-ring ring-1 ring-ring"
      )}
    >
      <Handle className={cn(HANDLE_CLASS, "!bg-cat-3")} position={Position.Top} type="target" />
      <Handle className={cn(HANDLE_CLASS, "!bg-cat-3")} position={Position.Bottom} type="source" />
      <Handle
        className={cn(HANDLE_CLASS, "!bg-cat-3")}
        id="return"
        position={Position.Bottom}
        style={{ left: "calc(100% - 16px)" }}
        type="source"
      />
      <button
        aria-expanded={!d.collapsed}
        aria-label={`${d.collapsed ? "Expand" : "Collapse"} ${d.label} tools`}
        className="nodrag nopan flex w-full items-center gap-3 rounded-sm px-4 py-3 text-left hover:bg-accent/40 focus-visible:outline focus-visible:outline-2 focus-visible:outline-ring"
        onClick={(event) => {
          event.stopPropagation();
          d.onToggle?.(id);
        }}
        type="button"
      >
        <span className="flex size-8 shrink-0 items-center justify-center rounded-sm border border-cat-3/20 bg-cat-3/15 text-cat-3">
          <Icon.tool className="size-4" />
        </span>
        <span className="min-w-0 flex-1">
          <span className="block text-sm font-semibold">{d.label}</span>
          <span className="mt-1 block text-xs text-muted-foreground">
            {d.childCount ?? 0} available tool{d.childCount === 1 ? "" : "s"}
          </span>
        </span>
        {d.collapsed ? (
          <Icon.chevronRight className="size-4 text-muted-foreground" />
        ) : (
          <Icon.chevronDown className="size-4 text-muted-foreground" />
        )}
      </button>
    </div>
  );
}

function ActionNode({ data, id, selected }: NodeProps) {
  const d = data as unknown as ActionNodeData;
  return (
    <NodeCard
      dotClass="!bg-cat-3"
      evaluationLabel="Action evaluations · on completion"
      evaluations={d.evaluations}
      footer={<PixelFooter>Tool</PixelFooter>}
      icon={<Icon.tool className="size-4" />}
      returnSource
      selected={selected}
      sourcePosition={Position.Bottom}
      title={d.tool.name}
      tone="tool"
      width={320}
    >
      <Preview text={d.tool.purpose} />
      <section className="space-y-3 rounded-sm bg-wash-subtle p-3">
        <FieldPreview
          label="Input"
          text={
            d.tool.args ||
            d.tool.arguments?.map((argument) => argument.name).join(", ") ||
            "Not captured"
          }
        />
        <FieldPreview label="Returns" text={d.tool.returns || "Not captured"} />
      </section>
      <section className="space-y-2">
        {d.when ? (
          <FieldPreview label="When" text={d.when} />
        ) : (
          <p className="flex items-center gap-1.5 text-xs text-muted-foreground">
            {d.modelSelected ? <Icon.model className="size-3" /> : <Icon.help className="size-3" />}
            {d.modelSelected ? "Chosen by model" : "Selection condition not captured"}
          </p>
        )}
      </section>
      <section className="!p-0">
        <button
          aria-expanded={d.expanded}
          aria-label={`${d.expanded ? "Hide" : "Show"} outcomes for ${d.tool.name}`}
          className="nodrag nopan flex w-full items-center gap-2 rounded-sm bg-wash-raised px-3 py-2 text-left text-xs text-muted-foreground hover:bg-accent hover:text-foreground focus-visible:outline focus-visible:outline-2 focus-visible:outline-ring"
          onClick={(event) => {
            event.stopPropagation();
            d.onToggle?.(id);
          }}
          type="button"
        >
          <Icon.gitBranch className="size-3 shrink-0" />
          <span className="flex-1">
            {d.tool.branches?.length
              ? `${d.tool.branches.length} outcome${d.tool.branches.length === 1 ? "" : "s"}`
              : "Conditions unmapped"}
          </span>
          {d.expanded ? (
            <Icon.chevronDown className="size-3" />
          ) : (
            <Icon.chevronRight className="size-3" />
          )}
        </button>
      </section>
    </NodeCard>
  );
}

function OutcomeNode({ data, selected }: NodeProps) {
  const d = data as unknown as OutcomeNodeData;
  const branch = d.branch;
  return (
    <NodeCard
      dotClass="!bg-cat-5"
      footer={d.recordedInvocations ? undefined : <PixelFooter>Outcome</PixelFooter>}
      hasSource={!!d.continues}
      headerDetail={
        d.recordedInvocations && d.recordedInvocations > 1 ? (
          <p className="text-xs leading-5 text-muted-foreground">
            Latest of {d.recordedInvocations} captured results
          </p>
        ) : undefined
      }
      icon={<Icon.gitBranch className="size-4" />}
      selected={selected}
      sourcePosition={Position.Bottom}
      targetPosition={Position.Top}
      title={d.recordedInvocations ? "Captured result" : branch?.action || "Local outcome"}
      tone="outcome"
      width={320}
    >
      <Preview text={branch?.outcome || d.returns || "Outcome not captured"} />
      {!d.recordedInvocations && (
        <section className="space-y-3 rounded-sm bg-wash-subtle p-3">
          <FieldPreview label="Then" text={branch?.next || "Next step not captured"} />
          {branch?.transitions?.map((transition, index) => (
            <FieldPreview
              key={`${transition.target}-${index}`}
              label="When"
              text={transition.condition}
            />
          ))}
        </section>
      )}
    </NodeCard>
  );
}

function EvaluationScopesNode({ data }: NodeProps) {
  const d = data as unknown as EvaluationScopesNodeData;
  return (
    <div className="w-[320px] space-y-4 rounded-md border border-border bg-card p-4">
      <p className="text-sm font-medium">Other evaluation scopes</p>
      {d.broad.length > 0 && (
        <TrajectoryEvaluationList label="Across matching spans" rows={d.broad} />
      )}
      {d.unmapped.length > 0 && (
        <div className="space-y-2">
          <TrajectoryEvaluationList label="Not mapped to a step" rows={d.unmapped} />
          <p className="text-xs leading-snug text-muted-foreground">
            No unique step anchor matches these spans.
          </p>
        </div>
      )}
    </div>
  );
}

function TerminalNode({ data, selected }: NodeProps) {
  const d = data as unknown as TerminalNodeData;
  const meta = TERMINAL_META[d.terminalKind] ?? {
    label: d.terminalKind || "unknown",
    variant: "neutral" as const,
  };
  return (
    <NodeCard
      dotClass="!bg-cat-2"
      footer={
        <Badge className="h-5 px-1.5 text-xs font-medium" variant={meta.variant}>
          {meta.label}
        </Badge>
      }
      hasSource={false}
      icon={
        d.terminalKind === "error_exit" ? (
          <Icon.failed className="size-4" />
        ) : d.terminalKind === "escalates" ? (
          <Icon.send className="size-4" />
        ) : (
          <Icon.target className="size-4" />
        )
      }
      selected={selected}
      title="Result"
      tone="result"
      width={320}
    >
      <Preview
        label={d.recordedOutput === undefined ? undefined : "Captured output"}
        text={d.recordedOutput ?? d.terminalDescription}
      />
    </NodeCard>
  );
}

export const trajectoryNodeTypes = {
  action: ActionNode,
  actionGroup: ActionGroupNode,
  entry: EntryNode,
  evaluations: EvaluationScopesNode,
  outcome: OutcomeNode,
  step: StepNode,
  terminal: TerminalNode,
} as const;
