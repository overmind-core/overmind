import { Link } from "@tanstack/react-router";

import { Badge, badgeVariants } from "@/components/ui/badge";
import { Icon } from "@/components/ui/icons";
import type { ActivityAsset } from "@/openapi";

const chipClass = `${badgeVariants({ size: "chip", variant: "outline" })} min-w-0 max-w-full gap-1.5 align-middle hover:bg-accent`;

export function ActivityAssetChip({
  asset,
  projectId,
}: {
  asset: ActivityAsset;
  projectId: string;
}) {
  const content = (
    <>
      <AssetIcon kind={asset.kind} />
      <span className="truncate">{asset.label}</span>
    </>
  );
  if (asset.kind === "training_job")
    return (
      <Link
        className={chipClass}
        search={{ groupId: asset.id, jobId: asset.id, projectId }}
        to="/training"
      >
        {content}
      </Link>
    );
  if (asset.kind === "dataset")
    return (
      <Link
        className={chipClass}
        params={{ datasetId: asset.id }}
        search={{ projectId }}
        to="/datasets/$datasetId"
      >
        {content}
      </Link>
    );
  if (asset.kind === "capability")
    return (
      <Link
        className={chipClass}
        params={{ capabilityId: asset.id }}
        search={{ projectId }}
        to="/capabilities/$capabilityId"
      >
        {content}
      </Link>
    );
  return (
    <Link className={chipClass} search={{ projectId, view: "matrix" }} to="/evaluations">
      {content}
    </Link>
  );
}

function AssetIcon({ kind }: { kind: string }) {
  if (kind === "training_job") return <Icon.finetuning className="size-3" />;
  if (kind === "dataset") return <Icon.dataset className="size-3" />;
  if (kind === "capability") return <Icon.capability className="size-3" />;
  return <Icon.evaluations className="size-3" />;
}

export function ActivityTaskChip({
  kind,
  id,
  projectId,
}: {
  kind: string;
  id: string;
  projectId: string;
}) {
  if (kind === "dataset_run")
    return (
      <Link
        className={chipClass}
        params={{ datasetId: id }}
        search={{ projectId }}
        to="/datasets/$datasetId"
      >
        <Icon.dataset className="size-3" />
        Source import
      </Link>
    );
  if (kind === "finetune_job")
    return (
      <Link className={chipClass} search={{ groupId: id, jobId: id, projectId }} to="/training">
        <Icon.finetuning className="size-3" />
        Training task
      </Link>
    );
  if (kind === "eval_run")
    return (
      <Link
        className={chipClass}
        params={{ runId: id }}
        search={{ projectId }}
        to="/evaluations/runs/$runId"
      >
        <Icon.evaluations className="size-3" />
        Evaluation task
      </Link>
    );
  if (kind === "optimizer_experiment")
    return (
      <Link
        className={chipClass}
        params={{ experimentId: id }}
        search={{ projectId }}
        to="/optimiser/$experimentId"
      >
        <Icon.optimiser className="size-3" />
        Optimiser task
      </Link>
    );
  return (
    <Badge className="gap-1.5" size="chip" variant="neutral">
      <Icon.listBox className="size-3" />
      {kind.replaceAll("_", " ")}
    </Badge>
  );
}
