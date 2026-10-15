import { type ReactNode, useMemo, useState } from "react";

import { useNavigate } from "@tanstack/react-router";

import {
  activationPending,
  ModelActivationStatus,
} from "@/components/finetuning/model-activation-status";
import {
  BaseModelCell,
  CopyableModelRef,
  FineTunedModelIdentity,
  TrainingJobChip,
} from "@/components/inference/model-identity";
import { ServingStatusBadge } from "@/components/inference/serving-status";
import { DeployedModelStatusBadge } from "@/components/inference/status-badge";
import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Icon } from "@/components/ui/icons";
import { ListToolbar } from "@/components/ui/list-toolbar";
import { QueryError } from "@/components/ui/query-error";
import { SearchInput } from "@/components/ui/search-input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { SortableHeader } from "@/components/ui/sortable-header";
import { LoadingState, Spinner } from "@/components/ui/spinner";
import { Table, TableBody, TableCell, TableHeader, TableRow } from "@/components/ui/table";
import { TableSectionHeader } from "@/components/ui/table-section-header";
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip";
import { blockedReason, useSetActiveModel } from "@/hooks/use-capability-active-model";
import { useGuestGate } from "@/hooks/use-guest-gate";
import {
  useDeployedModelQuery,
  useDeployedModelsQuery,
  useModelLiveQuery,
} from "@/hooks/use-inference";
import { useTableSort } from "@/hooks/use-table-sort";
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
 * Keep routing identity and the copyable alias attached to the model table,
 * including when the active deployment is outside the current page.
 */
function LiveModelSummary({
  alias,
  live,
  liveId,
  capability,
  models,
}: {
  capability: Capability;
  models: DeployedModel[];
  alias: string;
  live: DeployedModel | null;
  liveId: string | null;
}) {
  // The live deployment need not be on this page: a fine-tune can be pinned across
  // sibling capabilities, and the table shows one page of 100.
  const { data: fetched } = useDeployedModelQuery(liveId ?? undefined);
  const served = fetched ?? live ?? null;
  const { data: liveStats } = useModelLiveQuery(served?.id, served?.status === "ready");

  return (
    <div className="space-y-3 px-4 py-4">
      {served && served.status !== "ready" ? (
        <Alert variant="destructive">
          <span>This deployment isn't ready, so the alias is failing for every caller.</span>
        </Alert>
      ) : null}

      <div className="grid gap-3 md:grid-cols-2">
        <div className="min-w-0 space-y-2 rounded-md bg-wash-subtle px-3 py-3">
          <div className="flex items-center gap-2">
            <p className="pixel-label text-xs text-muted-foreground">Serving now</p>
            {served ? <ServingStatusBadge live={liveStats} status={served.status} /> : null}
          </div>
          {served ? (
            <>
              <FineTunedModelIdentity
                baseModelId={served.baseModelId}
                capabilityName={served.capabilityName}
                displayName={served.finetuningJobName}
                modelId={served.modelId}
                size="lg"
              />
              <div className="flex min-w-0 flex-wrap items-center gap-2">
                <span className="text-xs text-muted-foreground">Model reference</span>
                <CopyableModelRef modelId={served.modelId} />
                {served.maxModelLen > 0 ? (
                  <span className="text-xs text-muted-foreground">
                    {formatNumber(served.maxModelLen)} token context
                  </span>
                ) : null}
              </div>
            </>
          ) : liveId ? (
            <p className={cn(PROSE, "text-sm text-muted-foreground")}>
              A model is serving from another deployment.
            </p>
          ) : (
            <p className={cn(PROSE, "text-sm text-muted-foreground")}>No model is serving yet.</p>
          )}
        </div>

        <div className="min-w-0 space-y-2 rounded-md bg-wash-subtle px-3 py-3">
          <p className="pixel-label text-xs text-muted-foreground">API alias</p>
          <CopyableModelRef modelId={alias} />
          <p className={cn(PROSE, "text-xs text-muted-foreground")}>
            Use this value for the model field in an OpenAI-compatible request.
          </p>
        </div>
      </div>
      <ModelActivationStatus capability={capability} models={models} showTraffic={false} />
    </div>
  );
}

export function ModelsTab({ capability, capabilityId, projectId }: ModelsTabProps) {
  const { sort, onSort, sortRows } = useTableSort<
    "live" | "model" | "baseModelId" | "finetuningJobName" | "modelId" | "status"
  >();
  const navigate = useNavigate();
  const guard = useGuestGate();
  const {
    data: deployed,
    isLoading,
    error,
    refetch,
  } = useDeployedModelsQuery({ capability: capabilityId, pageSize: 100, projectId });
  const models = deployed?.results ?? [];
  const [modelSearch, setModelSearch] = useState("");
  const [modelStatus, setModelStatus] = useState("all");
  const filteredModels = useMemo(() => {
    const query = modelSearch.trim().toLowerCase();
    return models.filter((model) => {
      const matchesSearch =
        !query ||
        [model.modelId, model.finetuningJobName, model.baseModelId, model.status]
          .filter(Boolean)
          .join(" ")
          .toLowerCase()
          .includes(query);
      return matchesSearch && (modelStatus === "all" || model.status === modelStatus);
    });
  }, [modelSearch, modelStatus, models]);
  const hasModelFilters = !!modelSearch || modelStatus !== "all";
  const clearModelFilters = () => {
    setModelSearch("");
    setModelStatus("all");
  };

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
  const pendingId = activationPending(capability)
    ? capability.activation?.target
    : isPending
      ? variables?.id
      : null;
  const liveId = capability.activeModel ?? null;
  const live = models.find((m) => m.id === liveId) ?? null;

  return (
    <TooltipProvider>
      <div className="space-y-4">
        <TableSectionHeader
          actions={
            <div className="flex items-center gap-2">
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
          count={rowCount}
          description="Models available to this capability and their deployment status."
          icon={
            <Icon.inferenceTitle
              aria-hidden
              className="size-6 shrink-0 [image-rendering:pixelated] invert"
            />
          }
          title="Models"
          toolbar={
            <ListToolbar
              filters={
                <Select onValueChange={setModelStatus} value={modelStatus}>
                  <SelectTrigger
                    aria-label="Filter models by status"
                    className="w-36"
                    size="default"
                  >
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="all">All statuses</SelectItem>
                    <SelectItem value="queued">Queued</SelectItem>
                    <SelectItem value="quantizing">Quantizing</SelectItem>
                    <SelectItem value="deploying">Deploying</SelectItem>
                    <SelectItem value="warming">Warming</SelectItem>
                    <SelectItem value="ready">Ready</SelectItem>
                    <SelectItem value="failed">Failed</SelectItem>
                    <SelectItem value="deleting">Deleting</SelectItem>
                  </SelectContent>
                </Select>
              }
              hasActiveFilters={hasModelFilters}
              onClearFilters={clearModelFilters}
              search={
                <SearchInput
                  className="min-w-[220px] flex-1"
                  label="Search models"
                  onChange={(e) => setModelSearch(e.target.value)}
                  onClear={() => setModelSearch("")}
                  placeholder="Search models…"
                  value={modelSearch}
                />
              }
            />
          }
        />
        <div className="overflow-hidden rounded-md border border-border bg-card">
          <LiveModelSummary
            alias={`overmind/${capabilityId}`}
            capability={capability}
            live={live}
            liveId={liveId}
            models={models}
          />
          <Table className="table-fixed">
            <TableHeader>
              <TableRow>
                <SortableHeader
                  className="w-[6%] text-center"
                  label="Live"
                  onSort={onSort}
                  sort={sort}
                  sortKey="live"
                />
                <SortableHeader
                  className="w-[34%]"
                  label="Model"
                  onSort={onSort}
                  sort={sort}
                  sortKey="model"
                />
                <SortableHeader
                  className="w-[16%] text-center"
                  label="Base model"
                  onSort={onSort}
                  sort={sort}
                  sortKey="baseModelId"
                />
                <SortableHeader
                  className="w-[18%] text-center"
                  label="Training job"
                  onSort={onSort}
                  sort={sort}
                  sortKey="finetuningJobName"
                />
                <SortableHeader
                  className="w-[16%] text-center"
                  label="Reference"
                  onSort={onSort}
                  sort={sort}
                  sortKey="modelId"
                />
                <SortableHeader
                  className="w-[10%] text-center"
                  label="Status"
                  onSort={onSort}
                  sort={sort}
                  sortKey="status"
                />
              </TableRow>
            </TableHeader>
            <TableBody>
              {sortRows(filteredModels, (m, key) =>
                key === "live"
                  ? m.id === liveId
                  : key === "model"
                    ? m.finetuningJobName || m.modelId
                    : m[key]
              ).map((m) => (
                <FineTunedRow
                  isLive={m.id === liveId}
                  isPending={!!pendingId && pendingId === m.id}
                  key={m.id}
                  model={m}
                  onSetLive={guard((model: DeployedModel) => {
                    if (!pendingId) setLive(model, live);
                  })}
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

              {filteredModels.length === 0 && models.length > 0 && (
                <TableRow>
                  <TableCell
                    className="py-10 text-center text-sm text-muted-foreground"
                    colSpan={6}
                  >
                    No models match these filters.
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
        </div>

        {narrowingConfirm}
      </div>
    </TooltipProvider>
  );
}
