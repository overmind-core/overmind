import { UsedVersionChip } from "@/components/datasets/badges";
import { EntityRef } from "@/components/entity-ref";
import { HeaderStat } from "@/components/finetuning/finetuning-chrome";
import { ModelProviderChip } from "@/components/model-provider-chip";
import { WorkflowState } from "@/components/model-workflows/common";
import { Card } from "@/components/ui/card";
import { CountChip } from "@/components/ui/count-chip";
import { DateTime } from "@/components/ui/datetime";
import { StatusBadge } from "@/lib/job-status";
import type { NativeEvaluation, NativeEvaluationSuite } from "@/openapi";

type Participant = { key: string; name: string; model?: string };

function Suite({
  suite,
  label,
  projectId,
}: {
  suite: NativeEvaluationSuite;
  label: string;
  projectId: string;
}) {
  return (
    <HeaderStat label={label}>
      <div className="flex flex-wrap items-center gap-1.5">
        <EntityRef
          cellId={suite.id}
          id={suite.datasetId}
          kind="dataset"
          name={suite.datasetName}
          projectId={projectId}
        />
        <UsedVersionChip info={suite} />
        <span className="text-xs text-muted-foreground">{suite.rows.toLocaleString()} rows</span>
      </div>
    </HeaderStat>
  );
}

function ParticipantChip({ participant }: { participant: Participant }) {
  return (
    <ModelProviderChip model={participant.model ?? participant.name} showModelId={false}>
      <span className="min-w-0 truncate">{participant.name}</span>
    </ModelProviderChip>
  );
}

export function ProbabilityEvaluationDetails({ plan }: { plan: NativeEvaluation }) {
  const participants = (plan.config?.participants ?? []) as Participant[];
  const baseline = participants.find((participant) => participant.key === plan.config?.baseline);
  const completedAt =
    plan.state === "completed" && plan.calls?.score?.state === "completed"
      ? plan.calls.score.observed_at
      : null;
  const finalSuite = plan.finalSuite;
  return (
    <Card aria-label="Run details" className="flex flex-col" role="region">
      <div className="flex flex-wrap items-start justify-between gap-x-6 gap-y-3 p-4">
        <div className="flex w-full min-w-0 flex-col gap-2 sm:w-auto sm:flex-1">
          <h3 className="inline-flex items-center gap-1.5 text-xs text-foreground">
            {participants.length === 1 ? "Model" : "Models"}
            {participants.length > 1 && <CountChip count={participants.length} />}
          </h3>
          <div className="flex flex-wrap gap-2">
            {participants.map((participant) => (
              <ParticipantChip key={participant.key} participant={participant} />
            ))}
          </div>
        </div>
        {baseline && (
          <div className="flex w-full min-w-0 flex-col gap-2 sm:w-auto">
            <h3 className="text-xs text-foreground">Baseline</h3>
            <ParticipantChip participant={baseline} />
          </div>
        )}
        <dl className="flex shrink-0 flex-col items-end gap-1 text-xs">
          <div className="flex items-baseline gap-2">
            <dt className="text-muted-foreground">Credits used</dt>
            <dd>—</dd>
          </div>
          <div className="flex items-baseline gap-2">
            <dt className="text-muted-foreground">Created</dt>
            <dd>
              <DateTime value={plan.createdAt} />
            </dd>
          </div>
          <div className="flex items-baseline gap-2">
            <dt className="text-muted-foreground">{completedAt ? "Completed" : "Updated"}</dt>
            <dd>
              <DateTime value={completedAt ?? plan.updatedAt} />
            </dd>
          </div>
        </dl>
      </div>
      <div className="flex flex-wrap items-stretch gap-y-3 border-t border-border/70 px-4 py-3.5 [&>*+*]:ml-5 [&>*+*]:border-l [&>*+*]:border-border/60 [&>*+*]:pl-5">
        {finalSuite.capabilityId && (
          <HeaderStat label="Capability">
            <EntityRef
              id={finalSuite.capabilityId}
              kind="capability"
              name={finalSuite.capabilityName}
              projectId={plan.project}
            />
          </HeaderStat>
        )}
        <Suite label="Dataset" projectId={plan.project} suite={finalSuite} />
        {plan.calibrationSuite && (
          <Suite
            label="Calibration dataset"
            projectId={plan.project}
            suite={plan.calibrationSuite}
          />
        )}
        <HeaderStat label="Graded against">Dataset references</HeaderStat>
      </div>
      <div className="flex flex-wrap items-center gap-2.5 border-t border-border/70 px-4 py-3">
        {["draft", "prepared", "waiting_for_checkpoint", "submission_unknown"].includes(
          plan.state
        ) ? (
          <WorkflowState state={plan.state} />
        ) : (
          <StatusBadge fallback="queued" status={plan.state} />
        )}
      </div>
    </Card>
  );
}
