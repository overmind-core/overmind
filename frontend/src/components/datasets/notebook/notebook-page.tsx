import { useCallback, useMemo, useRef, useState } from "react";

import { useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate } from "@tanstack/react-router";

import { NewDatasetDialog } from "@/components/datasets/new-dataset-dialog";
import { type Analytics, sameAnalytics } from "@/components/datasets/notebook/analytics";
import { NotebookControls } from "@/components/datasets/notebook/canvas-controls";
import {
  type CapabilityChoice,
  type UsePurpose,
  VersionCard,
} from "@/components/datasets/notebook/cell";
import { type FlowFocus, VersionFlow } from "@/components/datasets/notebook/cell-flow";
import { isSplit, SplitSummary } from "@/components/datasets/notebook/preparation";
import { RenameDataset } from "@/components/datasets/notebook/settings";
import { WorkshopStatus } from "@/components/datasets/workshop-status";
import { Button } from "@/components/ui/button";
import { Icon } from "@/components/ui/icons";
import { Spinner } from "@/components/ui/spinner";
import {
  activeCellOf,
  cellsOf,
  type DatasetEvent,
  type DatasetPatch,
  datasetDisplayName,
  downloadExport,
  intentOf,
  invalidateDataset,
  isBusy,
  rankOf,
  useCancelExecutionMutation,
  useCancelImportMutation,
  useDatasetEvents,
  useDatasetQuery,
  usePatchDatasetMutation,
  useResumeImportMutation,
} from "@/hooks/use-datasets";
import { useProjectCapabilitiesQuery } from "@/hooks/use-evaluations";
import { useGuestGate } from "@/hooks/use-guest-gate";
import { usePersistedState } from "@/hooks/use-persisted-state";
import { notify } from "@/lib/notify";
import { cn } from "@/lib/utils";
import type { Cell } from "@/openapi";

const MCP_DOCS = "https://docs.overmindlab.ai/platform/mcp";

export function DatasetNotebook({
  datasetId,
  projectId,
  cellParam,
}: {
  datasetId: string;
  projectId: string;
  cellParam?: string;
}) {
  const qc = useQueryClient();
  const navigate = useNavigate();
  const guard = useGuestGate();
  const datasetQuery = useDatasetQuery(datasetId);
  const dataset = datasetQuery.data;
  const capabilitiesQuery = useProjectCapabilitiesQuery(projectId);
  const patch = usePatchDatasetMutation(datasetId);
  const resume = useResumeImportMutation(datasetId);
  const cancelImport = useCancelImportMutation(datasetId);
  const cancelRun = useCancelExecutionMutation(datasetId);
  const [copyFrom, setCopyFrom] = useState<Cell | null>(null);
  const [showMinimap, setShowMinimap] = usePersistedState("workshopFlow:showMinimap", false);
  const [selectedId, setSelectedId] = useState<string | null>(cellParam ?? null);
  const [focus, setFocus] = useState<FlowFocus | null>(
    cellParam ? { id: cellParam, request: 0 } : null
  );
  const [landing, setLanding] = useState<{ stage?: string; traces?: number }>({});
  const refreshTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const refresh = useCallback(() => {
    if (refreshTimer.current) return;
    refreshTimer.current = setTimeout(() => {
      refreshTimer.current = null;
      invalidateDataset(qc, datasetId);
    }, 250);
  }, [qc, datasetId]);

  useDatasetEvents(datasetId, (event: DatasetEvent, meta) => {
    if (!meta.live && event.type !== "replay.done") return;
    if (event.type === "land_progress") {
      setLanding({ stage: event.stage, traces: event.traces });
      return;
    }
    if (
      event.type === "land_done" ||
      event.type === "land_failed" ||
      event.type === "land_cancelled"
    )
      setLanding({});
    refresh();
  });

  const cells = useMemo(() => cellsOf(dataset), [dataset]);
  const active = activeCellOf(dataset);
  const editable = !!dataset && !isBusy(dataset);
  const intent = intentOf(dataset);
  const capabilities = capabilitiesQuery.data?.results;
  const capabilityChoices = useMemo<CapabilityChoice[]>(
    () =>
      [
        ...rankOf(dataset).map((r) => ({ id: r.capability_id, name: r.name, score: r.score })),
        ...(capabilities ?? []).map((c) => ({ id: c.id, name: c.name })),
      ].filter((c, i, all) => all.findIndex((o) => o.id === c.id) === i),
    [dataset, capabilities]
  );

  const show = useCallback((id: string) => {
    setSelectedId(id);
    setFocus((current) => ({ id, request: (current?.request ?? 0) + 1 }));
  }, []);

  const handOff = useCallback(
    (purpose: UsePurpose) => {
      const capabilityId = dataset?.capability ?? undefined;
      if (purpose === "decision")
        void navigate({
          search: { datasetId, decision: true, models: "decision", projectId },
          to: "/training",
        });
      else if (purpose === "train")
        void navigate({
          search: { capabilityId, datasetId, projectId, train: true },
          to: "/training",
        });
      else if (purpose === "train_eval")
        void navigate({
          search: { capabilityId, evalDatasetId: datasetId, projectId, train: true },
          to: "/training",
        });
      else
        void navigate({
          search: { capabilityId, datasetId, optimize: true, projectId },
          to: "/optimiser",
        });
    },
    [dataset?.capability, datasetId, navigate, projectId]
  );

  const renderVersion = useCallback(
    (cell: Cell) =>
      dataset ? (
        <VersionCard
          actions={{
            onActivate: guard(() =>
              patch.mutate({ active: cell.id, expectedActive: dataset.active ?? undefined })
            ),
            onCapability: guard((id: string | null) => patch.mutate({ capability: id })),
            onExport: (fmt) =>
              void downloadExport(
                datasetId,
                cell.id,
                fmt,
                `${datasetDisplayName(dataset)}-${cell.version}`
              ).catch((e) => notify.error(e, "Export failed")),
            onIntent: guard((next: "train" | "eval") => patch.mutate({ intent: next })),
            onNewDataset: guard(() => setCopyFrom(cell)),
            onUse: guard(handOff),
          }}
          active={cell.id === dataset.active}
          analyticsChanged={
            !sameAnalytics(
              (cell.analytics ?? {}) as Analytics,
              (cells.find((parent) => parent.id === cell.parent)?.analytics ?? {}) as Analytics
            )
          }
          capabilities={capabilityChoices}
          capabilityName={dataset.capabilityName ?? ""}
          cell={cell}
          datasetId={datasetId}
          editable={editable && !patch.isPending}
          intent={intent}
          onSelect={() => setSelectedId(cell.id)}
          projectId={projectId}
          selected={cell.id === selectedId}
          sourceSpec={dataset.sourceSpec}
        />
      ) : null,
    [
      dataset,
      cells,
      guard,
      patch,
      datasetId,
      projectId,
      handOff,
      capabilityChoices,
      editable,
      intent,
      selectedId,
    ]
  );

  if (datasetQuery.isPending) {
    return (
      <div className="flex h-full items-center justify-center">
        <Spinner />
      </div>
    );
  }
  if (!dataset) {
    return (
      <div className="flex h-full flex-col items-center justify-center gap-2 text-sm text-muted-foreground">
        This dataset no longer exists.
        <Button asChild size="sm" variant="secondary">
          <Link search={{ projectId }} to="/datasets">
            All datasets
          </Link>
        </Button>
      </div>
    );
  }

  const landingText =
    landing.stage === "extracting"
      ? "Extracting text…"
      : landing.traces
        ? `Importing… ${landing.traces.toLocaleString()} traces read`
        : "Importing…";
  const settled = dataset.state !== "landing" && !dataset.running;

  return (
    <div className="relative flex h-full min-h-0 flex-col">
      <div className="flex h-10 shrink-0 items-center gap-3 border-b border-border/70 px-3">
        <div className="flex min-w-0 flex-1 items-center gap-3">
          {dataset.state === "landing" && cells.length > 0 && (
            <span className="flex items-center gap-1.5 text-xs text-muted-foreground">
              <Spinner className="size-3" />
              {landingText}
            </span>
          )}
          {cells.length > 0 && <SplitSummary projectId={projectId} spec={dataset.sourceSpec} />}
          {cells.length > 0 && isSplit(dataset.sourceSpec) && cells.length === 1 && settled && (
            <span aria-hidden className="hidden h-4 w-px shrink-0 bg-border sm:block" />
          )}
          {cells.length === 1 && settled && (
            <span
              className={cn(
                "min-w-0 items-center gap-1.5 text-xs text-muted-foreground",
                isSplit(dataset.sourceSpec) ? "hidden sm:flex" : "flex"
              )}
            >
              <span className="shrink-0">No versions yet</span>
              <span aria-hidden>·</span>
              <a
                className="inline-flex min-w-0 items-center gap-1 text-foreground hover:underline"
                href={MCP_DOCS}
                rel="noreferrer"
                target="_blank"
              >
                <span className="truncate">Connect a coding agent to add them</span>
                <Icon.externalLink className="size-3 shrink-0" />
              </a>
            </span>
          )}
        </div>
        {dataset.state === "landing" && (
          <Button
            aria-busy={cancelImport.isPending}
            disabled={cancelImport.isPending}
            onClick={guard(() => cancelImport.mutate())}
            size="xs"
            variant="secondary"
          >
            <Icon.stop />
            Stop import
          </Button>
        )}
        <WorkshopStatus
          dataset={dataset}
          onCancelRun={guard((id: string) => cancelRun.mutate(id))}
          onShowVersion={(label) => {
            const target = cells.find((cell) => cell.version === label);
            if (target) show(target.id);
          }}
        />
        <RenameDataset
          dataset={dataset}
          disabled={!editable || patch.isPending}
          onSave={guard((next: DatasetPatch) => patch.mutate(next))}
        />
        <NotebookControls
          onToggleMinimap={cells.length ? () => setShowMinimap((on) => !on) : undefined}
          showMinimap={showMinimap}
        />
      </div>
      <div className="relative min-h-0 flex-1">
        {!cells.length && (
          <section className="flex h-full items-center justify-center p-6">
            <div className="flex max-w-md flex-col items-center gap-3 text-center">
              {dataset.state === "landing" ? (
                <>
                  <Spinner className="size-5" />
                  <p className="text-sm text-foreground">{landingText}</p>
                </>
              ) : (
                <>
                  {dataset.state === "stopped" ? (
                    <Icon.stop className="size-5 text-muted-foreground" />
                  ) : (
                    <Icon.warning
                      className={cn(
                        "size-5",
                        dataset.state === "error" ? "text-destructive" : "text-muted-foreground"
                      )}
                    />
                  )}
                  <p className="text-sm text-foreground">
                    {dataset.state === "error"
                      ? "The import failed"
                      : dataset.state === "stopped"
                        ? "Import stopped"
                        : "No rows were imported"}
                  </p>
                  {dataset.state === "stopped" && (
                    <p className="text-xs text-muted-foreground">No rows were imported.</p>
                  )}
                  {dataset.error && (
                    <p className="text-xs text-muted-foreground" role="alert">
                      {dataset.error}
                    </p>
                  )}
                  <div className="flex gap-2">
                    {dataset.importBlocked && (
                      <Button
                        aria-busy={resume.isPending}
                        disabled={resume.isPending}
                        onClick={guard(() => resume.mutate())}
                        size="sm"
                      >
                        {resume.isPending ? <Spinner className="size-3" /> : <Icon.refresh />}
                        Retry import
                      </Button>
                    )}
                    <Button
                      asChild
                      size="sm"
                      variant={dataset.state === "stopped" ? "default" : "secondary"}
                    >
                      <Link search={{ create: true, projectId }} to="/datasets">
                        <Icon.datasetAdd />
                        {dataset.state === "stopped" ? "Start again" : "New dataset"}
                      </Link>
                    </Button>
                  </div>
                </>
              )}
            </div>
          </section>
        )}
        {cells.length > 0 && (
          <VersionFlow
            cells={cells}
            focus={focus ?? (active ? { id: active.id, request: 0 } : null)}
            renderVersion={renderVersion}
            showMinimap={showMinimap}
            storageKey={`workshop-flow:positions:${projectId}:${datasetId}`}
          />
        )}
      </div>
      <NewDatasetDialog
        initialCapabilityId={dataset.capability ?? undefined}
        initialVersion={
          copyFrom
            ? {
                cellId: copyFrom.id,
                datasetId,
                datasetName: datasetDisplayName(dataset),
                rows: copyFrom.rows,
                version: copyFrom.version,
              }
            : undefined
        }
        onOpenChange={(open) => !open && setCopyFrom(null)}
        open={!!copyFrom}
        projectId={projectId}
      />
    </div>
  );
}
