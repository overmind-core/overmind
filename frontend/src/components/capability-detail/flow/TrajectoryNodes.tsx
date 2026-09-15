import { Handle, type NodeProps, Position } from "@xyflow/react";

import { FileRef } from "@/components/capability-detail/file-ref";
import { Badge } from "@/components/ui/badge";
import { Icon } from "@/components/ui/icons";
import { PROSE } from "@/lib/typography";
import { cn } from "@/lib/utils";
import type {
  CapabilitiesNodeData,
  ClusterNodeData,
  EntryNodeData,
  StepCapability,
  StepNodeData,
  TerminalNodeData,
} from "./buildTrajectoryGraph";

const HANDLE_CLASS =
  "!size-2.5 !rounded-xs !border-2 !border-background !shadow-none transition-colors";

const TERMINAL_META: Record<
  string,
  { label: string; variant: "success" | "neutral" | "warning" | "error"; icon: string }
> = {
  emits_record: { icon: "text-success", label: "Emits record", variant: "success" },
  error_exit: { icon: "text-destructive", label: "Error exit", variant: "error" },
  escalates: { icon: "text-warning", label: "Escalates", variant: "warning" },
  returns_empty: { icon: "text-muted-foreground", label: "Returns empty", variant: "neutral" },
};

/** How each trajectory claim class reads on a card. */
const CLAIM_META: Record<string, { label: string; title: string }> = {
  code_path: {
    label: "Code path",
    title: "Control flow in the source determines this route",
  },
  decision_surface: {
    label: "Runtime-decided",
    title: "A model chooses the route at runtime; the map records the decision point",
  },
  declared_task: {
    label: "Declared task",
    title: "The builder's own architecture or prompt names this unit of work",
  },
};

function NodeCard({
  icon,
  title,
  subtitle,
  footer,
  width,
  iconClass,
  dotClass,
  hasTarget = true,
  hasSource = true,
  selected,
  children,
}: {
  icon: React.ReactNode;
  title: string;
  subtitle?: string;
  footer: React.ReactNode;
  width: number;
  iconClass: string;
  dotClass: string;
  hasTarget?: boolean;
  hasSource?: boolean;
  selected?: boolean;
  children?: React.ReactNode;
}) {
  return (
    <div
      className={cn(
        "group relative flex cursor-pointer flex-col overflow-hidden rounded-md border border-border bg-card text-left text-card-foreground transition-colors",
        // Shadows are off system-wide (`--shadow-*: none`), so selection reads
        // through border + ring.
        selected && "border-ring ring-1 ring-ring",
        "hover:border-border hover:bg-accent/40"
      )}
      style={{ width }}
    >
      {hasTarget && (
        <Handle className={cn(HANDLE_CLASS, dotClass)} position={Position.Left} type="target" />
      )}
      {hasSource && (
        <Handle className={cn(HANDLE_CLASS, dotClass)} position={Position.Right} type="source" />
      )}

      <header
        className={cn(
          "flex items-start gap-2 bg-wash-raised px-3 py-2.5",
          children && "border-b border-border/70"
        )}
      >
        <span className={cn("flex h-5 w-4 shrink-0 items-center justify-center", iconClass)}>
          {icon}
        </span>
        <div className="min-w-0 flex-1">
          <p className="text-sm font-semibold leading-5 text-foreground" title={title}>
            {title}
          </p>
          {subtitle ? (
            <p className="truncate text-xs leading-4 text-muted-foreground" title={subtitle}>
              {subtitle}
            </p>
          ) : null}
        </div>
        <span className="shrink-0 leading-5">{footer}</span>
      </header>

      {children ? <div className="flex flex-col gap-2.5 p-3">{children}</div> : null}
    </div>
  );
}

function PixelFooter({ children }: { children: React.ReactNode }) {
  return <span className="pixel-label text-xs leading-5 text-muted-foreground">{children}</span>;
}

function EntryNode({ data, selected }: NodeProps) {
  const d = data as unknown as EntryNodeData;
  return (
    <NodeCard
      dotClass="!bg-primary/80"
      footer={<PixelFooter>Entry</PixelFooter>}
      hasTarget={false}
      icon={<Icon.agent className="size-4" />}
      iconClass="text-primary"
      selected={selected}
      subtitle={d.task || undefined}
      title={d.capabilityName || "Capability"}
      width={240}
    >
      <p className="text-xs text-muted-foreground">
        {d.pathCount} mapped execution path{d.pathCount === 1 ? "" : "s"}
      </p>
    </NodeCard>
  );
}

function IoSection({
  label,
  entries,
}: {
  label: string;
  entries: { label: string; condition?: string }[];
}) {
  if (entries.length === 0) return null;
  return (
    <section>
      <p className="pixel-label mb-1 text-xs text-muted-foreground">{label}</p>
      <div className="flex flex-col gap-1">
        {entries.map((entry, i) => (
          <div key={`${entry.label}-${i}`}>
            <p className="text-xs leading-snug text-foreground">{entry.label}</p>
            {entry.condition ? (
              <p className={cn(PROSE, "text-xs leading-snug text-muted-foreground")}>
                when {entry.condition}
              </p>
            ) : null}
          </div>
        ))}
      </div>
    </section>
  );
}

function ContractRow({ label, text }: { label: string; text: string }) {
  if (!text) return null;
  return (
    <section>
      <p className="pixel-label mb-1 text-xs text-muted-foreground">{label}</p>
      <p className={cn(PROSE, "text-xs leading-snug text-foreground")}>{text}</p>
    </section>
  );
}

function MayUseList({ capabilities }: { capabilities: StepCapability[] }) {
  if (capabilities.length === 0) return null;
  return (
    <section>
      <p className="pixel-label mb-1 text-xs text-muted-foreground">May use</p>
      <div
        aria-label={`Conditional tools, ${capabilities.length}`}
        className="nodrag nowheel -mr-1 flex max-h-56 flex-col gap-1.5 overflow-y-auto pr-1 focus-visible:outline focus-visible:outline-2 focus-visible:outline-ring"
        role="group"
        // biome-ignore lint/a11y/noNoninteractiveTabindex: scrollable region must be focusable so keyboard users can scroll it (WAI keyboard guidance)
        tabIndex={0}
      >
        {capabilities.map((cap) => (
          <div key={cap.tool}>
            <p className="inline-flex items-center gap-1 font-mono text-xs leading-snug text-foreground">
              <Icon.tool className="size-3 shrink-0" />
              {cap.tool}
            </p>
            {cap.when ? (
              <p className={cn(PROSE, "text-xs leading-snug text-muted-foreground")}>
                when {cap.when}
              </p>
            ) : null}
          </div>
        ))}
      </div>
    </section>
  );
}

function StepNode({ data, selected }: NodeProps) {
  const d = data as unknown as StepNodeData;
  const shared = d.pathNames.length > 1;
  const branches = d.outputs.some((o) => o.condition);
  const invocation = d.actor === "model";
  return (
    <NodeCard
      dotClass={invocation ? "!bg-primary/80" : "!bg-cat-4/80"}
      footer={
        <PixelFooter>{invocation ? "Model call" : shared ? "Shared step" : "Step"}</PixelFooter>
      }
      icon={
        invocation ? (
          <Icon.model className="size-4" />
        ) : branches || d.condition ? (
          <Icon.gitBranch className="size-4" />
        ) : (
          <Icon.algorithm className="size-4" />
        )
      }
      iconClass={invocation ? "text-primary" : "text-cat-4"}
      selected={selected}
      subtitle={shared ? undefined : d.pathNames[0]}
      title={d.label}
      width={280}
    >
      {shared ? (
        <div className="flex flex-wrap gap-1">
          {d.pathNames.map((name) => (
            <span
              className="rounded-sm border border-border/60 bg-wash-raised px-1.5 py-0.5 text-xs leading-4 text-muted-foreground"
              key={name}
            >
              {name}
            </span>
          ))}
        </div>
      ) : null}
      {d.contract ? (
        <>
          <ContractRow label="In" text={d.contract.input} />
          <ContractRow label="Action" text={d.contract.action} />
          <ContractRow label="Out" text={d.contract.output} />
        </>
      ) : (
        <>
          <IoSection entries={d.inputs.map((label) => ({ label }))} label="In" />
          <IoSection entries={d.outputs} label="Out" />
        </>
      )}
      {!invocation && <MayUseList capabilities={d.mayUse} />}
      {d.decisionNote ? (
        <p
          className={cn(
            PROSE,
            "rounded-sm border border-dashed border-border bg-wash-raised px-2 py-1.5 text-xs leading-snug text-muted-foreground"
          )}
        >
          {d.decisionNote}
        </p>
      ) : null}
      {d.condition ? (
        <section>
          <p className="pixel-label mb-1 text-xs text-muted-foreground">When</p>
          <p className={cn(PROSE, "text-xs leading-snug text-foreground")}>{d.condition}</p>
        </section>
      ) : null}
    </NodeCard>
  );
}

function ClusterNode({ data, selected }: NodeProps) {
  const d = data as unknown as ClusterNodeData;
  return (
    <NodeCard
      dotClass="!bg-cat-2/80"
      footer={
        <Badge className="h-5 px-1.5 text-xs font-medium" variant="neutral">
          {d.tools.length} tool{d.tools.length === 1 ? "" : "s"}
        </Badge>
      }
      icon={<Icon.tool className="size-4" />}
      iconClass="text-cat-2"
      selected={selected}
      subtitle={d.sideEffectMix || undefined}
      title={d.name}
      width={300}
    >
      <div
        aria-label={`${d.name} tools, ${d.tools.length}`}
        className="nodrag nowheel -mr-1 flex max-h-40 flex-col gap-1.5 overflow-y-auto pr-1 focus-visible:outline focus-visible:outline-2 focus-visible:outline-ring"
        role="group"
        // biome-ignore lint/a11y/noNoninteractiveTabindex: scrollable region must be focusable so keyboard users can scroll it (WAI keyboard guidance)
        tabIndex={0}
      >
        {d.tools.map((tool) => (
          <div key={tool.label}>
            <p className="inline-flex items-center gap-1 font-mono text-xs leading-snug text-foreground">
              <Icon.tool className="size-3 shrink-0" />
              {tool.label}
            </p>
            {tool.purpose ? (
              <p className="truncate text-xs text-muted-foreground" title={tool.purpose}>
                {tool.purpose}
              </p>
            ) : null}
          </div>
        ))}
      </div>
    </NodeCard>
  );
}

function CapabilitiesNode({ data, selected }: NodeProps) {
  const d = data as unknown as CapabilitiesNodeData;
  return (
    <NodeCard
      dotClass="!bg-cat-2/80"
      footer={
        <Badge className="h-5 px-1.5 text-xs font-medium" variant="neutral">
          Model action
        </Badge>
      }
      icon={<Icon.tool className="size-4" />}
      iconClass="text-cat-2"
      selected={selected}
      subtitle={`${d.tools.length} tool${d.tools.length === 1 ? "" : "s"} the model may select at "${d.forLabel}"`}
      title="May use"
      width={300}
    >
      <div
        aria-label={`Conditional tools, ${d.tools.length}`}
        className="nodrag nowheel -mr-1 flex max-h-72 flex-col gap-2 overflow-y-auto pr-1 focus-visible:outline focus-visible:outline-2 focus-visible:outline-ring"
        role="group"
        // biome-ignore lint/a11y/noNoninteractiveTabindex: scrollable region must be focusable so keyboard users can scroll it (WAI keyboard guidance)
        tabIndex={0}
      >
        {d.tools.map((cap) => (
          <div key={cap.tool}>
            <p className="inline-flex items-center gap-1 font-mono text-xs leading-snug text-foreground">
              <Icon.tool className="size-3 shrink-0" />
              {cap.tool}
            </p>
            {cap.when ? (
              <p className={cn(PROSE, "text-xs leading-snug text-muted-foreground")}>
                when {cap.when}
              </p>
            ) : null}
          </div>
        ))}
      </div>
    </NodeCard>
  );
}

function TerminalNode({ data, selected }: NodeProps) {
  const d = data as unknown as TerminalNodeData;
  const meta = TERMINAL_META[d.terminalKind] ?? {
    icon: "text-muted-foreground",
    label: d.terminalKind || "unknown",
    variant: "neutral" as const,
  };
  return (
    <NodeCard
      dotClass="!bg-cat-5/80"
      footer={
        <Badge className="h-5 px-1.5 text-xs font-medium" variant={meta.variant}>
          {meta.label}
        </Badge>
      }
      hasSource={false}
      icon={<Icon.target className="size-4" />}
      iconClass={meta.icon}
      selected={selected}
      subtitle={d.terminalDescription || undefined}
      title={d.pathName}
      width={300}
    >
      {CLAIM_META[d.claim] ? (
        <div className="flex flex-wrap items-center gap-1.5">
          <span
            className="rounded-sm border border-border/60 bg-wash-raised px-1.5 py-0.5 text-xs leading-4 text-muted-foreground"
            title={CLAIM_META[d.claim].title}
          >
            {CLAIM_META[d.claim].label}
          </span>
          {d.verified ? (
            <span
              className="inline-flex items-center gap-1 rounded-sm border border-border/60 bg-wash-raised px-1.5 py-0.5 text-xs leading-4 text-success"
              title="Anchors verified against the source call graph"
            >
              <Icon.success className="size-3" />
              Verified
            </span>
          ) : null}
        </div>
      ) : null}
      {d.routing ? (
        <section>
          <p className="pixel-label mb-1 text-xs text-muted-foreground">Selected when</p>
          <p className={cn(PROSE, "text-xs leading-snug text-foreground")}>{d.routing}</p>
        </section>
      ) : null}
      {d.promptQuote ? (
        <section>
          <p className="pixel-label mb-1 text-xs text-muted-foreground">Declared in prompt</p>
          <p
            className={cn(
              PROSE,
              "border-l-2 border-border pl-2 text-xs italic leading-snug text-muted-foreground"
            )}
          >
            "{d.promptQuote}"
          </p>
        </section>
      ) : null}
      {d.tools.length > 0 ? (
        <div className="flex flex-wrap gap-1.5">
          {d.tools.map((tool) => (
            <span
              className="inline-flex items-center gap-1 rounded-sm border border-border bg-wash-raised px-1.5 py-0.5 font-mono text-xs leading-4 text-muted-foreground"
              key={tool}
            >
              <Icon.tool className="size-3" />
              {tool}
            </span>
          ))}
        </div>
      ) : null}
      {d.provenance.length > 0 ? (
        <div className="flex flex-wrap gap-1.5">
          {d.provenance.map((reference) => (
            <FileRef key={reference} reference={reference} />
          ))}
        </div>
      ) : null}
    </NodeCard>
  );
}

export const trajectoryNodeTypes = {
  capabilities: CapabilitiesNode,
  cluster: ClusterNode,
  entry: EntryNode,
  step: StepNode,
  terminal: TerminalNode,
} as const;
