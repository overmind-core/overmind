import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate } from "@tanstack/react-router";

import { NotebookControls } from "@/components/datasets/notebook/canvas-controls";
import { NotebookCell, type UsePurpose } from "@/components/datasets/notebook/cell";
import { CellFlow } from "@/components/datasets/notebook/cell-flow";
import { useCellMotion } from "@/components/datasets/notebook/cell-motion";
import { ContaminationReport } from "@/components/datasets/notebook/preparation";
import {
  extractionStatus,
  SourceDetails,
  SourceLanding,
} from "@/components/datasets/notebook/source";
import { Button } from "@/components/ui/button";
import { Spinner } from "@/components/ui/spinner";
import {
  activeCellOf,
  type DatasetEvent,
  datasetDisplayName,
  downloadExport,
  intentOf,
  invalidateDataset,
  isBusy,
  rankOf,
  useCancelDatasetMutation,
  useDatasetEvents,
  useDatasetPreparationQuery,
  useDatasetQuery,
  usePatchDatasetMutation,
  useResumeDatasetImportMutation,
} from "@/hooks/use-datasets";
import { useProjectCapabilitiesQuery } from "@/hooks/use-evaluations";
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
  const preparationQuery = useDatasetPreparationQuery(datasetId, cellParam);
  const capabilitiesQuery = useProjectCapabilitiesQuery(projectId);
  const capabilities = capabilitiesQuery.data?.results ?? [];

  const patch = usePatchDatasetMutation(datasetId);
  const cancel = useCancelDatasetMutation(datasetId);
  const resumeImport = useResumeDatasetImportMutation(datasetId);

  const [showMinimap, setShowMinimap] = usePersistedState("workshopFlow:showMinimap", false);
  const [selectedId, setSelectedId] = useState<string | null>(cellParam ?? null);
  const [focus, setFocus] = useState<{ id: string; request: number } | null>(
    cellParam ? { id: cellParam, request: 0 } : null
  );
  const refreshTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const refresh = useCallback(() => {
    if (refreshTimer.current) return;
    refreshTimer.current = setTimeout(() => {
      refreshTimer.current = null;
      invalidateDataset(qc, datasetId);
    }, 250);
  }, [qc, datasetId]);

  useDatasetEvents(datasetId, (_event: DatasetEvent, meta) => {
    if (meta.live) refresh();
  });

  const nodes = preparationQuery.data?.nodes;
  const cells = useMemo(() => nodes?.map((node) => node.cell) ?? [], [nodes]);
  const active = activeCellOf(dataset);
  const edges = useMemo(
    () =>
      (preparationQuery.data?.edges ?? []).map((edge) => ({
        ...edge,
        id: `${edge.source}:${edge.target}`,
        style: { stroke: "var(--muted-foreground)", strokeWidth: 1.5 },
        type: "cell",
      })),
    [preparationQuery.data]
  );
  const visibleIds = useMemo(() => new Set(cells.map((cell) => cell.id)), [cells]);
  const { motions, complete } = useCellMotion(nodes ? cells : undefined, visibleIds);
  const busy = isBusy(dataset);
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
      } else if (purpose === "train_eval") {
        void navigate({
          search: { capabilityId, evalDatasetId: datasetId, projectId, train: true },
          to: "/training",
        });
      } else {
        void navigate({
          search: { capabilityId, datasetId, optimize: true, projectId },
          to: "/optimiser",
        });
      }
    },
    [dataset?.capability, datasetId, navigate, projectId]
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

  const rank = rankOf(dataset);
  const capabilityChoices = [
    ...rank.map((r) => ({ id: r.capability_id, name: r.name, score: r.score })),
    ...capabilities.map((c) => ({ id: c.id, name: c.name })),
  ].filter((c, i, all) => all.findIndex((o) => o.id === c.id) === i);

  const renderCell = (cell: Cell) => {
    const node = nodes?.find((node) => node.cell.id === cell.id);
    const owner = node?.dataset ?? datasetId;
    const local = owner === datasetId;
    return (
      <div>
        {node && (node.role || !local) && (
          <div className="mb-2 text-sm text-muted-foreground">
            <Link params={{ datasetId: owner }} search={{ projectId }} to="/datasets/$datasetId">
              {node.role || node.datasetName}
            </Link>
          </div>
        )}
        <NotebookCell
          actions={{
            onCapability: guard((id: string | null) => patch.mutate({ capability: id })),
            onExport: (fmt) =>
              void downloadExport(
                owner,
                cell.id,
                fmt,
                `${datasetDisplayName(dataset)}-${cell.version || "source"}`
              ).catch((e) => notify.error(e, "Export failed")),
            onIntent: guard((next: "train" | "eval") => patch.mutate({ intent: next })),
            onUse: guard(handOff),
          }}
          active={cell.id === active?.id}
          capabilities={capabilityChoices}
          capabilityName={dataset.capabilityName ?? ""}
          cell={cell}
          datasetId={owner}
          editable={editable && local}
          intent={node?.intent ?? intent}
          key={cell.id}
          onSelect={() => setSelectedId(cell.id)}
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
        />
      </div>
    );
  };

  return (
    <div className="relative flex h-full min-h-0 flex-col">
      {busy && (
        <div className="flex items-center justify-between border-b border-border/70 px-4 py-2 text-xs">
          <span>Workshop operation running</span>
          <Button
            disabled={cancel.isPending}
            onClick={() => cancel.mutate()}
            size="sm"
            variant="secondary"
          >
            Stop operation
          </Button>
        </div>
      )}
      <ContaminationReport spec={dataset.sourceSpec} />
      {preparationQuery.isError && (
        <p className="px-4 text-sm text-destructive" role="alert">
          Preparation could not be loaded.
          <Button onClick={() => void preparationQuery.refetch()} size="sm" variant="secondary">
            Retry
          </Button>
        </p>
      )}
      {dataset.error && (
        <p className="px-4 text-sm text-destructive" role="alert">
          {dataset.error}
        </p>
      )}
      {dataset.operation?.source_import?.can_resume === true && (
        <div className="px-4 py-2">
          <Button
            disabled={resumeImport.isPending}
            onClick={guard(() => resumeImport.mutate())}
            size="sm"
            variant="secondary"
          >
            Resume import
          </Button>
        </div>
      )}
      <div className="relative flex min-h-0 flex-1 flex-col">
        <NotebookControls
          onToggleMinimap={cells.length ? () => setShowMinimap(!showMinimap) : undefined}
          showMinimap={showMinimap}
        />
        {preparationQuery.isPending ? (
          <Spinner />
        ) : cells.length === 0 ? (
          <div className="overflow-y-auto pl-10">
            <SourceLanding
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
            motions={motions}
            onMotionComplete={complete}
            renderCell={renderCell}
            showMinimap={showMinimap}
            storageKey={`workshop-flow:layers:${projectId}:${datasetId}`}
          />
        )}
      </div>
    </div>
  );
}
