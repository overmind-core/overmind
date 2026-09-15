import { EntityRef } from "@/components/entity-ref";
import {
  finetunedChipLabel,
  isFinetunedServingId,
} from "@/components/finetuning/finetuned-serving-id";
import { ModelProviderChip } from "@/components/model-provider-chip";
import { Badge } from "@/components/ui/badge";
import { Icon } from "@/components/ui/icons";
import { cn } from "@/lib/utils";

type FinetuningModelChipProps = {
  model: string;
  projectId: string;
  /** DeployedModel UUID for `/inference/$modelId` — omit when not deployed yet. */
  deployedModelUuid?: string | null;
  className?: string;
  compact?: boolean;
};

export function FinetuningModelChip({
  model,
  projectId,
  deployedModelUuid,
  className,
  compact = false,
}: FinetuningModelChipProps) {
  if (!isFinetunedServingId(model)) {
    return <ModelProviderChip className={className} compact={compact} model={model} />;
  }

  const label = finetunedChipLabel(model);
  const chipClass = cn(
    "inline-flex min-w-0 max-w-full items-center gap-1.5 overflow-hidden border border-border bg-wash-raised px-1.5 py-0.5 text-xs font-medium text-foreground",
    compact ? "h-6" : "h-7",
    className
  );

  const body = (
    <>
      <Icon.overmind className="size-3.5 shrink-0" />
      <span className="min-w-0 truncate">{label}</span>
    </>
  );

  if (deployedModelUuid) {
    return (
      <EntityRef
        className={className}
        id={deployedModelUuid}
        kind="model"
        name={label}
        projectId={projectId}
      />
    );
  }

  return (
    <Badge className={chipClass} title={model} variant="outline">
      {body}
    </Badge>
  );
}
