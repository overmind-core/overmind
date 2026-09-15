import { useState } from "react";

import { toast } from "sonner";

import { CreateEvaluatorDialog } from "@/components/evaluations/create-evaluator-dialog";
import { EVALUATOR_KIND_LABEL } from "@/components/evaluations/evaluator-kind";
import { Button } from "@/components/ui/button";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Icon } from "@/components/ui/icons";
import { LoadingState } from "@/components/ui/spinner";
import { useDeleteEvaluatorMutation, useEvaluatorDetailQuery } from "@/hooks/use-evaluations";
import { notify } from "@/lib/notify";
import { PROSE } from "@/lib/typography";
import { cn } from "@/lib/utils";
import type { Evaluator } from "@/openapi";

const JUDGE_KINDS = new Set(["llm_judge", "agentic"]);

type Target = { phrase: string };

function scopeTarget(scope: string | undefined): Target {
  switch (scope) {
    case "turn":
      return { phrase: "each conversation turn" };
    case "step":
      return { phrase: "each step of the trace" };
    case "trajectory":
      return { phrase: "the capability's tool-call path" };
    case "dataset":
      return { phrase: "the whole dataset" };
    case "final_output":
      return { phrase: "the model's final answer" };
    default:
      return { phrase: "the model output" };
  }
}

function methodPhrase(evaluator: Evaluator): string {
  const config = (evaluator.config ?? {}) as Record<string, unknown>;
  switch (evaluator.kind) {
    case "deterministic":
      return "with a deterministic rule";
    case "statistical":
      return "as a corpus-level statistic";
    case "trajectory":
      return config.mode === "judge"
        ? "with an LLM judge"
        : "by matching its tool calls to a reference";
    default:
      return JUDGE_KINDS.has(evaluator.kind) ? "with an LLM judge" : "with a scoring rule";
  }
}

function summarize(evaluator: Evaluator): string {
  return `Grades ${scopeTarget(evaluator.scope).phrase} ${methodPhrase(evaluator)}.`;
}

function scoringSummary(evaluator: Evaluator): string {
  const kind = evaluator.kind;
  const config = (evaluator.config ?? {}) as Record<string, unknown>;
  const choices = asArray<{ label?: string }>(evaluator.choices);
  const thresholdPct =
    evaluator.passThreshold != null ? Math.round(evaluator.passThreshold * 100) : null;

  if (kind === "deterministic") {
    return "Scores 1 (pass) or 0 (fail) per sample; passes when the check succeeds.";
  }
  if (kind === "statistical") {
    return `${String(config.metric ?? "Metric")}, computed once across the whole run.`;
  }
  if (choices.length > 0) {
    return `Categorical: ${choices.map((c) => c.label ?? "?").join(", ")}.`;
  }
  if (JUDGE_KINDS.has(kind)) {
    return thresholdPct != null
      ? `0 to 100%, passes at ≥ ${thresholdPct}%.`
      : "0 to 100%, recorded without a pass/fail cutoff.";
  }
  const range = `${evaluator.scoreMin ?? 0} to ${evaluator.scoreMax ?? 1}, higher is better`;
  return thresholdPct != null ? `${range}; passes at ≥ ${thresholdPct}%.` : `${range}.`;
}

function asArray<T>(value: unknown): T[] {
  return Array.isArray(value) ? (value as T[]) : [];
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="flex flex-col gap-2">
      <h3 className="text-sm font-semibold text-muted-foreground">{title}</h3>
      {children}
    </section>
  );
}

function ChipList({ items }: { items: string[] }) {
  return (
    <div className="flex flex-wrap gap-1">
      {items.map((item) => (
        <code className="rounded-sm bg-muted px-1.5 py-0.5 font-mono text-xs" key={item}>
          {item}
        </code>
      ))}
    </div>
  );
}

function JudgePrompt({ evaluator }: { evaluator: Evaluator }) {
  if (!evaluator.judgePrompt) {
    return (
      <p className={cn(PROSE, "text-xs text-muted-foreground")}>
        Judged against this evaluator&apos;s rubric and checklist.
      </p>
    );
  }
  return (
    <pre className="max-h-80 overflow-auto whitespace-pre-wrap rounded-md border bg-wash-subtle p-3 font-mono text-xs leading-relaxed text-foreground">
      {evaluator.judgePrompt}
    </pre>
  );
}

function DeterministicLogic({ config }: { config: Record<string, unknown> }) {
  const check = String(config.check ?? "");
  const pattern = typeof config.pattern === "string" ? config.pattern : "";
  const requiredKeys = asArray<string>(config.required_keys);
  const expectedTools = asArray<string>(config.expected_tools);
  // Tier-0 negated-regex form ^(?:(?!X)[\s\S])*$ — surface the inner X to a human.
  const negated = pattern.match(/^\^\(\?:\(\?!([\s\S]*)\)\[\\s\\S\]\)\*\$$/);

  return (
    <div className="flex flex-col gap-2">
      <p className={cn(PROSE, "text-xs")}>
        Runs the <code className="font-mono font-medium">{check || "comparison"}</code> check on
        every sample. No model is involved.
      </p>
      {pattern && (
        <div className="flex flex-col gap-1">
          <p className={cn(PROSE, "text-xs text-muted-foreground")}>
            {negated
              ? "Passes only when this pattern never matches:"
              : "Passes when this pattern matches:"}
          </p>
          <code className="block overflow-x-auto whitespace-pre rounded-sm bg-muted px-2 py-1.5 font-mono text-xs">
            {negated ? negated[1] : pattern}
          </code>
        </div>
      )}
      {requiredKeys.length > 0 && (
        <div className="flex flex-col gap-1">
          <p className="text-xs text-muted-foreground">Required keys:</p>
          <ChipList items={requiredKeys} />
        </div>
      )}
      {expectedTools.length > 0 && (
        <div className="flex flex-col gap-1">
          <p className="text-xs text-muted-foreground">Expected tools:</p>
          <ChipList items={expectedTools} />
        </div>
      )}
    </div>
  );
}

function StatisticalLogic({ config }: { config: Record<string, unknown> }) {
  const metric = String(config.metric ?? "accuracy");
  return (
    <p className={cn(PROSE, "text-xs")}>
      Computes <code className="font-mono font-medium">{metric}</code> once across every sample in
      the run, producing a single corpus-level number rather than a per-sample score. No model is
      involved.
    </p>
  );
}

function TrajectoryLogic({ config }: { config: Record<string, unknown> }) {
  const mode = String(config.mode ?? "match");
  if (mode === "judge") {
    return (
      <p className={cn(PROSE, "text-xs")}>
        An LLM judge rates the trajectory&apos;s coherence and efficiency, reference-free.
      </p>
    );
  }
  const matchMode = String(config.trajectory_match_mode ?? "strict");
  const overrides = (config.tool_args_match_overrides ?? {}) as Record<string, unknown>;
  return (
    <div className="flex flex-col gap-2">
      <p className={cn(PROSE, "text-xs")}>
        Compares the capability&apos;s tool-call sequence against a reference using{" "}
        <code className="font-mono font-medium">{matchMode}</code> matching. No model is involved.
      </p>
      {Object.keys(overrides).length > 0 && (
        <div className="flex flex-col gap-1">
          <p className="text-xs text-muted-foreground">Per-tool argument matching:</p>
          <ChipList items={Object.entries(overrides).map(([t, m]) => `${t}: ${String(m)}`)} />
        </div>
      )}
    </div>
  );
}

function HowItWorks({ evaluator }: { evaluator: Evaluator }) {
  const kind = evaluator.kind;
  const config = (evaluator.config ?? {}) as Record<string, unknown>;
  return (
    <Section title={JUDGE_KINDS.has(kind) ? "Judge prompt" : "How it works"}>
      {JUDGE_KINDS.has(kind) && <JudgePrompt evaluator={evaluator} />}
      {kind === "deterministic" && <DeterministicLogic config={config} />}
      {kind === "statistical" && <StatisticalLogic config={config} />}
      {kind === "trajectory" && <TrajectoryLogic config={config} />}
    </Section>
  );
}

function EvaluatorDetail({ evaluator }: { evaluator: Evaluator }) {
  const explanation = evaluator.description?.trim() || summarize(evaluator);
  return (
    <DialogBody className="flex flex-col gap-5">
      <p className={cn(PROSE, "text-sm leading-relaxed text-foreground")}>{explanation}</p>
      <HowItWorks evaluator={evaluator} />
      <Section title="Scoring">
        <p className="text-xs">{scoringSummary(evaluator)}</p>
      </Section>
    </DialogBody>
  );
}

export function EvaluatorDetailDialog({
  evaluatorId,
  open,
  onOpenChange,
}: {
  evaluatorId: string | undefined;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const { data, isLoading } = useEvaluatorDetailQuery(evaluatorId, open);
  const ownerLabel = data
    ? data.isGeneric
      ? "Generic"
      : data.capabilityName || "Capability"
    : null;
  const kindLabel = data ? (EVALUATOR_KIND_LABEL[data.kind] ?? data.kind) : null;
  const subtitle = [ownerLabel, kindLabel].filter(Boolean).join(" · ");

  const [editTarget, setEditTarget] = useState<Evaluator | null>(null);
  const canEdit = !!data && data.kind === "llm_judge" && !data.isManaged;
  const canDelete = !!data && !data.isManaged;

  const deleteEvaluator = useDeleteEvaluatorMutation(data?.project ?? undefined);

  const handleEdit = () => {
    if (!data) return;
    setEditTarget(data);
    onOpenChange(false);
  };

  const handleDelete = async () => {
    if (!data) return;
    try {
      await deleteEvaluator.mutateAsync(data.id);
      toast.success(`Deleted "${data.displayName?.trim() || data.name}"`);
      onOpenChange(false);
    } catch (e) {
      notify.error(e, "Failed to delete evaluator");
    }
  };

  return (
    <>
      <Dialog onOpenChange={onOpenChange} open={open}>
        <DialogContent size="md">
          <DialogHeader
            end={
              (canEdit || canDelete) && (
                <div className="flex items-center gap-2">
                  {canEdit && (
                    <Button onClick={handleEdit} size="sm" variant="secondary">
                      <Icon.edit /> Edit
                    </Button>
                  )}
                  {canDelete && (
                    <ConfirmDialog
                      confirmLabel="Delete evaluator"
                      description="This permanently removes the evaluator, its score history, and any eval-set memberships."
                      destructive
                      isPending={deleteEvaluator.isPending}
                      onConfirm={handleDelete}
                      title={`Delete "${data?.displayName?.trim() || data?.name}"?`}
                      trigger={
                        <Button size="sm" variant="destructive">
                          <Icon.delete /> Delete
                        </Button>
                      }
                    />
                  )}
                </div>
              )
            }
          >
            <DialogTitle title={data?.name}>
              {data?.displayName?.trim() || data?.name || "Evaluation"}
            </DialogTitle>
            {subtitle ? <p className="text-xs text-muted-foreground">{subtitle}</p> : null}
            <DialogDescription className="sr-only">
              Read-only explanation of what this evaluator does and how it scores.
            </DialogDescription>
          </DialogHeader>

          {isLoading && (
            <DialogBody>
              <LoadingState label="Loading evaluator config…" />
            </DialogBody>
          )}
          {!isLoading && !data && (
            <DialogBody>
              <p className="text-xs text-muted-foreground">Could not load the evaluator config.</p>
            </DialogBody>
          )}
          {!isLoading && data && <EvaluatorDetail evaluator={data} />}
        </DialogContent>
      </Dialog>

      {editTarget && (
        <CreateEvaluatorDialog
          editEvaluator={editTarget}
          onOpenChange={(next) => {
            if (!next) setEditTarget(null);
          }}
          open={!!editTarget}
        />
      )}
    </>
  );
}
