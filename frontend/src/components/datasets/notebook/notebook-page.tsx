import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate } from "@tanstack/react-router";

import { AssistantDock } from "@/components/assistant/assistant-dock";
import { NotebookControls } from "@/components/datasets/notebook/canvas-controls";
import { NotebookCell, type UsePurpose } from "@/components/datasets/notebook/cell";
import { CellFlow } from "@/components/datasets/notebook/cell-flow";
import { useCellMotion } from "@/components/datasets/notebook/cell-motion";
import { ContaminationReport } from "@/components/datasets/notebook/preparation";
import { PreparationActions } from "@/components/datasets/notebook/preparation-actions";
import {
  extractionStatus,
  SourceDetails,
  SourceLanding,
} from "@/components/datasets/notebook/source";
import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { EmptyState } from "@/components/ui/empty-state";
import { LoadingState, Spinner } from "@/components/ui/spinner";
import { useAssistantThread } from "@/hooks/use-assistant-thread";
import {
  activeCellOf,
  type DatasetEvent,
  datasetDisplayName,
  downloadExport,
  intentOf,
  invalidateDataset,
  isBusy,
  SPLIT_LABEL,
  useCancelDatasetMutation,
  useDatasetEvents,
  useDatasetPreparationQuery,
  useDatasetQuery,
  useDatasetWorkbenchQuery,
  usePatchDatasetMutation,
  useResumeDatasetImportMutation,
  workText,
} from "@/hooks/use-datasets";
import { useGuestGate } from "@/hooks/use-guest-gate";
import { usePersistedState } from "@/hooks/use-persisted-state";
import { notify } from "@/lib/notify";
import type { Cell } from "@/openapi";

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
  const workbenchQuery = useDatasetWorkbenchQuery(datasetId);
  const preparationQuery = useDatasetPreparationQuery(
    datasetId,
    cellParam,
    dataset?.updatedAt?.getTime()
  );
  const assistant = useAssistantThread(datasetId);

  const patch = usePatchDatasetMutation(datasetId);
  const cancel = useCancelDatasetMutation(datasetId);
  const resumeImport = useResumeDatasetImportMutation(datasetId);

  const [showMinimap, setShowMinimap] = usePersistedState("workshopFlow:showMinimap", false);
  const [selectedId, setSelectedId] = useState<string | null>(cellParam ?? null);
  const [focus, setFocus] = useState<{ id: string; request: number } | null>(
    cellParam ? { id: cellParam, request: 0 } : null
  );
  const refreshTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(
    () => () => {
      if (refreshTimer.current) clearTimeout(refreshTimer.current);
      refreshTimer.current = null;
    },
    []
  );

  const refresh = useCallback(() => {
    if (refreshTimer.current) return;
    refreshTimer.current = setTimeout(() => {
      refreshTimer.current = null;
      invalidateDataset(qc, datasetId);
    }, 250);
  }, [qc, datasetId]);

  const [stamp, setStamp] = useState<number | null>(null);
  const read = dataset?.updatedAt?.getTime();
  useEffect(() => {
    if (stamp === null || read === undefined) return;
    setStamp(null);
    if (stamp !== read) refresh();
  }, [stamp, read, refresh]);

  useDatasetEvents(datasetId, (event: DatasetEvent) => {
    if (event.type === "assistant" || event.type === "assistant.draft") {
      assistant.handle(event);
      return;
    }
    if (event.type === "connected") {
      // The conversation reads after the events it holds, so every connection catches up
      // on it. A dropped connection may have missed dataset changes; a first one only
      // those made after the page read the dataset, compared once that read has arrived.
      assistant.handle(event);
      if (event.again) {
        refresh();
      } else {
        setStamp(event.updated_at ? new Date(event.updated_at).getTime() : Number.NaN);
      }
      return;
    }
    // Detail polling picks up landing counters while the dataset is busy.
    if (event.type !== "land_progress") refresh();
  });

  const nodes = preparationQuery.data?.nodes;
  const cells = useMemo(() => nodes?.map((node) => node.cell) ?? [], [nodes]);
  const active = activeCellOf(dataset);
  const edges = useMemo(
    () =>
      (preparationQuery.data?.edges ?? []).map((edge) => ({
        data: { kind: edge.kind, value: edge.value },
        id: `${edge.source}:${edge.target}`,
        source: edge.source,
        style: { stroke: "var(--muted-foreground)", strokeWidth: 1.5 },
        target: edge.target,
        type: "cell",
      })),
    [preparationQuery.data]
  );
  const visibleIds = useMemo(() => new Set(cells.map((cell) => cell.id)), [cells]);
  const { motions, complete } = useCellMotion(nodes ? cells : undefined, visibleIds);
  const busy = isBusy(dataset);
  // A first landing shows its stage in the source cell; added files show it here.
  const busyText =
    dataset?.state === "landing"
      ? ((cells.some((cell) => cell.state === "ok")
          ? extractionStatus(dataset.sourceSpec)
          : undefined) ?? "Importing")
      : dataset?.work
        ? workText(dataset.work)
        : "Running";
  const editable = !!dataset && !busy;
  const intent = intentOf(dataset);

  const scrollTo = useCallback((id: string) => {
    setSelectedId(id);
    setFocus((previous) => ({ id, request: (previous?.request ?? 0) + 1 }));
  }, []);
  useEffect(() => {
    const id = cellParam;
    if (id) scrollTo(id);
  }, [cellParam, scrollTo]);

  const handOff = useCallback(
    (purpose: UsePurpose) => {
      const capabilityId = dataset?.capability ?? undefined;
      if (purpose === "train") {
        void navigate({
          search: { capabilityId, datasetId, projectId, train: true },
          to: "/training",
        });
      } else {
        void navigate({
          search: { capabilityId, evalDatasetId: datasetId, projectId, train: true },
          to: "/training",
        });
      }
    },
    [dataset?.capability, datasetId, navigate, projectId]
  );

  if (datasetQuery.isPending) {
    return (
      <div className="flex h-full items-center justify-center">
        <LoadingState fullPage label="Loading dataset" />
      </div>
    );
  }
  if (!dataset) {
    return (
      <div className="flex h-full flex-col items-center justify-center gap-2 text-sm text-muted-foreground">
        {datasetQuery.isError ? "Dataset could not be loaded." : "This dataset no longer exists."}
        {datasetQuery.isError && (
          <Button onClick={() => void datasetQuery.refetch()} size="sm" variant="secondary">
            Retry
          </Button>
        )}
        <Button asChild size="sm" variant="secondary">
          <Link search={{ projectId }} to="/datasets">
            All datasets
          </Link>
        </Button>
      </div>
    );
  }

  const latestRun = workbenchQuery.data?.runs[0];
  const repairSource = latestRun?.state === "failed" ? latestRun.sourceCell : undefined;
  const actionCellId = cells.some((cell) => cell.id === repairSource) ? repairSource : active?.id;
  const preparationControls = (cell?: Cell) =>
    !busy ? (
      <div className="nodrag nopan pointer-events-auto">
        <PreparationActions cell={cell} dataset={dataset} run={latestRun}>
          {dataset.operation?.source_import?.can_resume === true && (
            <Button
              disabled={resumeImport.isPending}
              onClick={guard(() => resumeImport.mutate())}
              size="sm"
              variant="secondary"
            >
              Resume import
            </Button>
          )}
        </PreparationActions>
      </div>
    ) : undefined;

  const renderCell = (cell: Cell) => {
    const node = nodes?.find((node) => node.cell.id === cell.id);
    const owner = node?.dataset ?? datasetId;
    const local = owner === datasetId;
    return (
      <div>
        <NotebookCell
          actions={{
            onExport: (fmt) =>
              void downloadExport(
                owner,
                cell.id,
                fmt,
                `${datasetDisplayName(dataset)}-${cell.version || "source"}`
              ).catch((e) => notify.error(e, "Export failed")),
            onIntent: guard((next: "train" | "eval", format?: "sft" | "decision") =>
              patch.mutate({ intent: next, trainFormat: format ?? "" })
            ),
            onUse: guard(handOff),
          }}
          active={cell.id === active?.id}
          capabilityName={dataset.capabilityName ?? ""}
          cell={cell}
          datasetId={owner}
          editable={editable && local}
          intent={node?.intent ?? intent}
          // The server locks intent on the edited dataset's own used versions.
          intentLocked={
            nodes?.some(
              (other) => other.cell.frozen && (other.dataset ?? datasetId) === datasetId
            ) ?? false
          }
          key={cell.id}
          member={
            node && !local
              ? { datasetId: owner, label: SPLIT_LABEL[node.role] || node.role || node.datasetName }
              : undefined
          }
          onSelect={() => setSelectedId(cell.id)}
          preparationActions={cell.id === actionCellId ? preparationControls(cell) : undefined}
          selected={cell.id === selectedId}
          sourceDetails={
            local ? (
              <SourceDetails
                datasetId={datasetId}
                kind={dataset.sourceKind}
                spec={dataset.sourceSpec}
              />
            ) : undefined
          }
          trainFormat={node ? node.trainFormat : (dataset.trainFormat ?? "")}
        />
      </div>
    );
  };

  return (
    <div className="relative flex h-full min-h-0 flex-col">
      <div className="assistant-notices pointer-events-none absolute bottom-16 z-10 max-h-48 overflow-y-auto">
        <div className="pointer-events-auto space-y-2 rounded-sm bg-card">
          {/* Mounted while idle too, so the first status is announced. */}
          <span className="sr-only" role="status">
            {busy ? busyText : ""}
          </span>
          {busy && (
            <div className="flex items-center justify-between gap-3 rounded-sm border border-border px-3 py-2 text-xs">
              <span className="flex min-w-0 items-center gap-2">
                <Spinner size="sm" />
                <span className="truncate tabular-nums">{busyText}</span>
              </span>
              <Button
                disabled={cancel.isPending}
                onClick={() => cancel.mutate()}
                size="sm"
                variant="secondary"
              >
                Stop
              </Button>
            </div>
          )}
          <ContaminationReport spec={dataset.sourceSpec} />
          {workbenchQuery.isError && (
            <Alert variant="destructive">
              <div className="flex flex-wrap items-center gap-2">
                Preparation status could not be loaded.
                <Button onClick={() => void workbenchQuery.refetch()} size="sm" variant="secondary">
                  Retry status
                </Button>
              </div>
            </Alert>
          )}

          {preparationQuery.isError && preparationQuery.data && (
            <p className="px-4 text-sm text-muted-foreground" role="alert">
              Preparation could not be loaded.
              <Button onClick={() => void preparationQuery.refetch()} size="sm" variant="secondary">
                Retry
              </Button>
            </p>
          )}
        </div>
      </div>
      <div className="relative flex min-h-0 flex-1 flex-col">
        <NotebookControls
          onToggleMinimap={cells.length ? () => setShowMinimap(!showMinimap) : undefined}
          showMinimap={showMinimap}
        />
        {preparationQuery.isPending ? (
          <LoadingState fullPage label="Loading preparation" />
        ) : preparationQuery.isError && !preparationQuery.data ? (
          <EmptyState
            action={
              <Button onClick={() => void preparationQuery.refetch()} size="sm" variant="secondary">
                Retry
              </Button>
            }
            title="Dataset contents could not be loaded"
          />
        ) : cells.length === 0 ? (
          <div className="overflow-y-auto pr-10">
            <SourceLanding
              actions={preparationControls()}
              brief={dataset.brief ?? ""}
              busy={busy}
              datasetId={datasetId}
              landing={dataset.state === "landing"}
              landingStatus={extractionStatus(dataset.sourceSpec)}
            />
          </div>
        ) : (
          <CellFlow
            cells={cells}
            edges={edges}
            focus={focus}
            key={datasetId}
            motions={motions}
            onMotionComplete={complete}
            renderCell={renderCell}
            showMinimap={showMinimap}
          />
        )}
        <AssistantDock
          assistant={assistant}
          cells={cells}
          datasetId={datasetId}
          onSelectCell={scrollTo}
          projectId={projectId}
          selectedCell={selectedId}
        />
      </div>
    </div>
  );
}
