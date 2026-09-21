import type { ReactNode } from "react";

import { useNavigate } from "@tanstack/react-router";

import {
  BaseModelCell,
  CopyableModelRef,
  FineTunedModelIdentity,
  TrainingJobChip,
} from "@/components/inference/model-identity";
import { DeployedModelStatusBadge } from "@/components/inference/status-badge";
import { Alert } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Icon } from "@/components/ui/icons";
import { QueryError } from "@/components/ui/query-error";
import { SectionCard } from "@/components/ui/section-card";
import { LoadingState, Spinner } from "@/components/ui/spinner";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip";
import { blockedReason, useSetActiveModel } from "@/hooks/use-capability-active-model";
import { useGuestGate } from "@/hooks/use-guest-gate";
import { useDeployedModelQuery, useDeployedModelsQuery } from "@/hooks/use-inference";
import { featureFlags } from "@/lib/feature-flags";
import { formatNumber } from "@/lib/formatters";
import { PROSE } from "@/lib/typography";
import { cn } from "@/lib/utils";
import type { Capability, DeployedModel } from "@/openapi";

interface ModelsTabProps {
  capability: Capability;
  capabilityId: string;
  projectId: string | undefined;
}

const DASH = <span className="text-muted-foreground">—</span>;

/**
 * A block-level flex box, not `inline-flex`: an inline control sits on the text
 * baseline, which drifts the 16px box off the cell's optical centre.
 */
function SelectionCell({ children }: { children: ReactNode }) {
  return (
    <TableCell>
      <div className="flex h-4 items-center justify-center leading-none">{children}</div>
    </TableCell>
  );
}

/**
 * Stays enabled so the reason is reachable by keyboard: `disabled` drops a control
 * out of the tab order and `title` never fires on focus, so the reason has to be the
 * accessible name. A dash, not a greyed radio — the ghost box's `border-border/70`
 * measures 1.19:1, under the floor `check:contrast` enforces.
 */
function BlockedSelection({ reason }: { reason: string }) {
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <button
          aria-label={reason}
          // `after:-inset-1.5` grows the 16px box to the documented 28px target
          // without changing the row height.
          className="relative flex size-4 cursor-not-allowed items-center justify-center rounded-sm text-muted-foreground after:absolute after:-inset-1.5 after:content-[''] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/60"
          onClick={(e) => e.stopPropagation()}
          type="button"
        >
          —
        </button>
      </TooltipTrigger>
      <TooltipContent>{reason}</TooltipContent>
    </Tooltip>
  );
}

/**
 * A `<button>` with `aria-pressed`, not a radio group: arrow keys would move
 * selection inside a real group, which would retarget traffic in the Live column. An
 * orphan `role="radio"` would need the group on `<TableBody>`, clobbering its
 * `rowgroup` role.
 */
function ModelSelection({
  selected,
  isPending,
  label,
  onSelect,
}: {
  selected: boolean;
  isPending: boolean;
  label: string;
  onSelect: () => void;
}) {
  return (
    <button
      aria-label={label}
      aria-pressed={selected}
      className={cn(
        // `after:-inset-1.5` grows the 16px box to the documented 28px target.
        "relative inline-flex size-4 items-center justify-center rounded-sm border transition-colors after:absolute after:-inset-1.5 after:content-[''] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/60",
        selected
          ? "border-primary bg-primary/10"
          : "border-input hover:border-primary hover:bg-primary/10"
      )}
      disabled={selected || isPending}
      // The row navigates to the model's inference page; picking a model must not.
      onClick={(e) => {
        e.stopPropagation();
        onSelect();
      }}
      type="button"
    >
      {isPending ? <Spinner className="size-3" size="sm" /> : null}
      {!isPending && selected ? <span className="size-2 rounded-xs bg-primary" /> : null}
    </button>
  );
}

function FineTunedRow({
  isLive,
  isPending,
  model,
  onSetLive,
  projectId,
}: {
  isLive: boolean;
  isPending: boolean;
  model: DeployedModel;
  onSetLive: (model: DeployedModel) => void;
  projectId: string | undefined;
}) {
  const navigate = useNavigate();
  const guard = useGuestGate();
  const clickable = model.status !== "deleting" && model.status !== "deleted";
  const reason = blockedReason(model.status);

  return (
    <TableRow
      className={cn(
        clickable ? "cursor-pointer" : "opacity-60",
        // The live tint is the only row-level signal of what serves production, so
        // hover deepens it rather than replacing it.
        isLive ? "bg-primary/10 hover:bg-primary/15" : clickable && "hover:bg-wash-raised"
      )}
      onClick={
        clickable
          ? guard(() =>
              navigate({
                params: { modelId: model.id },
                search: { projectId },
                to: "/inference/$modelId",
              })
            )
          : undefined
      }
    >
      <SelectionCell>
        {isLive || !reason ? (
          <ModelSelection
            isPending={isPending}
            label={isLive ? `${model.modelId} is live` : `Make ${model.modelId} live`}
            onSelect={() => onSetLive(model)}
            selected={isLive}
          />
        ) : (
          <BlockedSelection reason={reason} />
        )}
      </SelectionCell>
      <TableCell className="align-middle text-foreground">
        <FineTunedModelIdentity
          baseModelId={model.baseModelId}
          capabilityName={model.capabilityName}
          displayName={model.finetuningJobName}
          modelId={model.modelId}
        />
      </TableCell>
      <TableCell className="align-middle text-center">
        <BaseModelCell baseModelId={model.baseModelId} />
      </TableCell>
      <TableCell className="align-middle text-center">
        <TrainingJobChip
          baseModelId={model.baseModelId}
          capabilityName={model.capabilityName}
          jobId={model.finetuningJobId}
          jobName={model.finetuningJobName}
          projectId={projectId}
        />
      </TableCell>
      <TableCell className="align-middle text-center">
        <CopyableModelRef modelId={model.modelId} />
      </TableCell>
      <TableCell className="align-middle text-center">
        <DeployedModelStatusBadge status={model.status} />
      </TableCell>
    </TableRow>
  );
}

/**
 * `overmind/<capability-uuid>` has no second, memorable form, so this card is the only
 * place a customer can obtain the string their code needs — hence copyable.
 */
function LiveModelCard({
  alias,
  live,
  liveId,
}: {
  alias: string;
  live: DeployedModel | null;
  liveId: string | null;
}) {
  // The live deployment need not be on this page: a fine-tune can be pinned across
  // sibling capabilities, and the table shows one page of 100.
  const { data: fetched } = useDeployedModelQuery(live ? undefined : (liveId ?? undefined));
  const served = live ?? fetched ?? null;

  return (
    <SectionCard
      description="The deployment this capability's alias answers with. Switching takes effect on the next request."
      headerEnd={served ? <DeployedModelStatusBadge status={served.status} /> : null}
      icon={<Icon.server className="size-3" />}
      title="Live model"
    >
      <div className="space-y-4">
        {served && served.status !== "ready" ? (
          <Alert variant="destructive">
            <span>This deployment isn't ready, so the alias is failing for every caller.</span>
          </Alert>
        ) : null}

        <div className="grid gap-4 sm:grid-cols-2">
          <div className="min-w-0 space-y-1.5">
            <p className="pixel-label text-xs text-muted-foreground">Serving now</p>
            {served ? (
              <>
                <FineTunedModelIdentity
                  baseModelId={served.baseModelId}
                  capabilityName={served.capabilityName}
                  displayName={served.finetuningJobName}
                  modelId={served.modelId}
                  size="lg"
                />
                {/* A sibling, not a suffix — inside the truncated id it truncates
                    away first. */}
                <div className="flex min-w-0 items-baseline gap-2">
                  <p
                    className="min-w-0 truncate font-mono text-xs text-muted-foreground"
                    title={served.modelId}
                  >
                    {served.modelId}
                  </p>
                  {served.maxModelLen > 0 ? (
                    <p className="shrink-0 text-xs text-muted-foreground">
                      {formatNumber(served.maxModelLen)} token context
                    </p>
                  ) : null}
                </div>
              </>
            ) : liveId ? (
              <p className={cn(PROSE, "text-sm text-muted-foreground")}>
                A deployment from elsewhere in this project — not listed below.
              </p>
            ) : (
              <p className={cn(PROSE, "text-sm text-muted-foreground")}>
                Nothing yet. Train a fine-tune, then make it live here.
              </p>
            )}
          </div>

          <div className="min-w-0 space-y-1.5">
            <p className="pixel-label text-xs text-muted-foreground">Alias for your code</p>
            <CopyableModelRef modelId={alias} />
            <p className={cn(PROSE, "text-xs text-muted-foreground")}>
              Send it as the model field from any OpenAI-compatible client.
            </p>
          </div>
        </div>
      </div>
    </SectionCard>
  );
}

export function ModelsTab({ capability, capabilityId, projectId }: ModelsTabProps) {
  const navigate = useNavigate();
  const guard = useGuestGate();
  const {
    data: deployed,
    isLoading,
    error,
    refetch,
  } = useDeployedModelsQuery({ capability: capabilityId, pageSize: 100, projectId });
  const models = deployed?.results ?? [];

  // Shared with the run card and the model detail page, so every entry point
  // retargets the alias under the same rules — including the narrowing confirmation.
  const { isPending, narrowingConfirm, setLive, variables } = useSetActiveModel(capabilityId);

  if (isLoading) return <LoadingState />;
  if (error && !deployed) {
    return (
      <QueryError
        error={error}
        fallback="Couldn't load this capability's models."
        onRetry={refetch}
      />
    );
  }

  const cellCls = "align-middle text-center";
  const rowCount = (deployed?.count ?? 0) + (capability.model ? 1 : 0);
  // Shows the pick as taken while the PATCH is in flight; a rejection settles the
  // mutation and the row snaps back to what the server holds.
  const pending = isPending ? (variables ?? null) : null;
  const liveId = pending?.id ?? capability.activeModel ?? null;
  const live = models.find((m) => m.id === liveId) ?? null;

  return (
    <TooltipProvider>
      <div className="space-y-4">
        <LiveModelCard alias={`overmind/${capabilityId}`} live={live} liveId={liveId} />

        <SectionCard
          contentClassName="p-0"
          headerEnd={
            <div className="flex items-center gap-2">
              <Badge variant="secondary">{rowCount}</Badge>
              {featureFlags.finetuning && projectId && (
                <Button
                  onClick={guard(() =>
                    navigate({ search: { capabilityId, projectId, train: true }, to: "/training" })
                  )}
                  size="sm"
                >
                  <Icon.ai />
                  Train model
                </Button>
              )}
            </div>
          }
          icon={<Icon.model className="size-3" />}
          title="Models"
        >
          <Table className="table-fixed">
            <TableHeader>
              <TableRow>
                <TableHead className="w-[6%] text-center">Live</TableHead>
                <TableHead className="w-[34%]">Model</TableHead>
                <TableHead className="w-[16%] text-center">Base model</TableHead>
                <TableHead className="w-[18%] text-center">Training job</TableHead>
                <TableHead className="w-[16%] text-center">Reference</TableHead>
                <TableHead className="w-[10%] text-center">Status</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {models.map((m) => (
                <FineTunedRow
                  isLive={m.id === liveId}
                  isPending={pending?.id === m.id}
                  key={m.id}
                  model={m}
                  onSetLive={guard((model: DeployedModel) => setLive(model, live))}
                  projectId={projectId}
                />
              ))}

              {capability.model && (
                <TableRow>
                  <SelectionCell>
                    <BlockedSelection reason="The alias only routes to deployed fine-tunes." />
                  </SelectionCell>
                  <TableCell className="align-middle text-sm font-medium text-muted-foreground">
                    Codebase incumbent
                  </TableCell>
                  <TableCell className={cellCls}>
                    <BaseModelCell baseModelId={capability.model} />
                  </TableCell>
                  <TableCell className={cellCls}>{DASH}</TableCell>
                  <TableCell className={cellCls}>
                    <CopyableModelRef modelId={capability.model} />
                  </TableCell>
                  <TableCell className={cellCls}>
                    <DeployedModelStatusBadge status="base" />
                  </TableCell>
                </TableRow>
              )}

              {models.length === 0 && !capability.model && (
                <TableRow>
                  <TableCell
                    className="py-10 text-center text-sm text-muted-foreground"
                    colSpan={6}
                  >
                    No models yet.
                  </TableCell>
                </TableRow>
              )}
            </TableBody>
          </Table>
        </SectionCard>

        {narrowingConfirm}
      </div>
    </TooltipProvider>
  );
}
