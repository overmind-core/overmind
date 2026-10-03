import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate } from "@tanstack/react-router";

import { NotebookCell, type UsePurpose } from "@/components/datasets/notebook/cell";
import { DatasetChat, type LiveTurn } from "@/components/datasets/notebook/chat";
import { NotebookOutline } from "@/components/datasets/notebook/outline";
import { PilotRequest } from "@/components/datasets/notebook/pilot-request";
import {
  ContaminationReport,
  PreparationPlanDetails,
} from "@/components/datasets/notebook/preparation";
import {
  extractionStatus,
  SourceDetails,
  SourceLanding,
} from "@/components/datasets/notebook/source";
import { Button } from "@/components/ui/button";
import { Spinner } from "@/components/ui/spinner";
import {
  activeCellOf,
  cellsOf,
  chatOf,
  type DatasetEvent,
  datasetDisplayName,
  downloadExport,
  intentOf,
  invalidateDataset,
  isBusy,
  rankOf,
  useAcceptCellMutation,
  useCancelDatasetMutation,
  useChatMutation,
  useDatasetEvents,
  useDatasetQuery,
  useEditCellMutation,
  usePatchDatasetMutation,
  useRemoveCellMutation,
  useRunMutation,
} from "@/hooks/use-datasets";
import { useProjectCapabilitiesQuery } from "@/hooks/use-evaluations";
import { useGuestGate } from "@/hooks/use-guest-gate";
import { notify } from "@/lib/notify";
import type { Cell } from "@/openapi";

export function DatasetNotebook({
  datasetId,
  projectId,
  cellParam,
  initialRequest,
}: {
  datasetId: string;
  projectId: string;
  cellParam?: string;
  initialRequest?: string;
}) {
  const qc = useQueryClient();
  const navigate = useNavigate();
  const guard = useGuestGate();
  const datasetQuery = useDatasetQuery(datasetId);
  const dataset = datasetQuery.data;
  const capabilitiesQuery = useProjectCapabilitiesQuery(projectId);
  const capabilities = capabilitiesQuery.data?.results ?? [];

  const patch = usePatchDatasetMutation(datasetId);
  const editCell = useEditCellMutation(datasetId);
  const removeCell = useRemoveCellMutation(datasetId);
  const acceptCell = useAcceptCellMutation(datasetId);
  const run = useRunMutation(datasetId);
  const chat = useChatMutation(datasetId);
  const cancel = useCancelDatasetMutation(datasetId);

  const [selectedId, setSelectedId] = useState<string | null>(cellParam ?? null);
  const [live, setLive] = useState<LiveTurn | null>(null);
  const [landedAt, setLandedAt] = useState(0);
  const refreshTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const refresh = useCallback(() => {
    if (refreshTimer.current) return;
    refreshTimer.current = setTimeout(() => {
      refreshTimer.current = null;
      invalidateDataset(qc, datasetId);
    }, 250);
  }, [qc, datasetId]);

  useDatasetEvents(datasetId, (event: DatasetEvent, meta) => {
    if (!meta.live) return;
    switch (event.type) {
      case "chat_turn":
        if (event.role === "user") setLive({ cells: [], steps: [], text: "" });
        // Cleared once the refetched chat carries the turn, so the text never blinks out.
        else setLandedAt(Date.now());
        refresh();
        break;
      case "chat_delta":
        // After a reload, keep the saved turn until a complete progress snapshot arrives.
        setLive(
          (prev) =>
            prev && {
              ...prev,
              cells: prev?.cells ?? [],
              steps: prev?.steps ?? [],
              text: (prev?.text ?? "") + event.text,
            }
        );
        break;
      case "chat_thinking":
        setLive(
          (prev) =>
            prev && {
              ...prev,
              cells: prev?.cells ?? [],
              steps: [
                ...(prev?.steps ?? []),
                { id: event.id, phase: "thinking", text: event.text, type: "activity" },
              ],
              text: prev?.text ?? "",
            }
        );
        break;
      case "chat_step": {
        const { type: _type, ...part } = event;
        setLive(
          (prev) =>
            prev && {
              ...prev,
              cells: prev?.cells ?? [],
              steps: [...(prev?.steps ?? []), { ...part, type: "activity" }],
              text: prev?.text ?? "",
            }
        );
        break;
      }
      case "chat_cell":
        setLive(
          (prev) =>
            prev && {
              ...prev,
              cells: [
                ...(prev?.cells ?? []),
                { action: event.action, id: event.cell_id, text_offset: event.text_offset },
              ],
              steps: prev?.steps ?? [],
              text: prev?.text ?? "",
            }
        );
        refresh();
        break;
      case "chat_progress":
        setLive({
          cells: event.cells,
          progress: event.progress,
          steps: event.steps,
          text: event.text,
        });
        refresh();
        break;
      case "chat_failed":
        setLive(null);
        notify.error(new Error(event.error), "The agent stopped");
        refresh();
        break;
      // Every (re)connect ends its replay here; whatever happened while the
      // stream was down is on the dataset row, so read it once.
      case "replay.done":
        refresh();
        break;
      case "land_progress":
        if ("stage" in event) refresh();
        break;
      default:
        refresh();
    }
  });

  const turns = useMemo(() => chatOf(dataset), [dataset]);
  useEffect(() => {
    if (!landedAt) return;
    if (turns.at(-1)?.role === "agent" && turns.at(-1)?.status !== "running") {
      setLive(null);
      setLandedAt(0);
    }
  }, [turns, landedAt]);

  useEffect(() => {
    if (dataset && !isBusy(dataset)) setLive(null);
  }, [dataset]);

  const all = useMemo(() => cellsOf(dataset), [dataset]);
  // Proposals wait in the chat; the notebook shows only cells that are in the chain.
  const cells = useMemo(() => all.filter((c) => c.state !== "proposed"), [all]);
  const active = activeCellOf(dataset);
  const busy = isBusy(dataset);
  const editable = !!dataset && !busy;
  const intent = intentOf(dataset);

  const cellsRef = useRef<HTMLDivElement>(null);
  // Scrolls the cells column only: scrollIntoView would also shove every
  // overflow-hidden ancestor (the panel, the page card) and leave it stuck.
  const scrollTo = useCallback((id: string) => {
    setSelectedId(id);
    const column = cellsRef.current;
    const target = document.getElementById(`cell-${id}`);
    if (!column || !target) return;
    const top = target.getBoundingClientRect().top - column.getBoundingClientRect().top;
    column.scrollTo({
      behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches
        ? "instant"
        : "smooth",
      top: column.scrollTop + top - 8,
    });
  }, []);
  // biome-ignore lint/correctness/useExhaustiveDependencies: once, when the deep-linked cell is loaded
  useEffect(() => {
    if (cellParam && cells.some((c) => c.id === cellParam)) scrollTo(cellParam);
  }, [cellParam, cells.length]);

  const handOff = useCallback(
    (cell: Cell, purpose: UsePurpose) => {
      const go = () => {
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
      };
      if (dataset?.active !== cell.id) patch.mutate({ active: cell.id }, { onSuccess: go });
      else go();
    },
    [dataset?.active, dataset?.capability, datasetId, navigate, patch, projectId]
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

  return (
    <div className="relative flex h-full min-h-0 flex-col">
      {(busy || dataset.operation?.state === "cancel_pending") && (
        <div className="flex items-center justify-between border-b border-border/70 px-4 py-2 text-xs">
          <span>
            {dataset.operation?.state === "cancel_pending"
              ? "Cancellation pending worker and provider acknowledgement"
              : "Workshop operation running"}
          </span>
          <Button
            disabled={cancel.isPending || dataset.operation?.state === "cancel_pending"}
            onClick={() => cancel.mutate()}
            size="sm"
            variant="secondary"
          >
            Stop operation
          </Button>
        </div>
      )}
      <NotebookOutline
        activeId={active?.id ?? null}
        cells={cells}
        onSelect={scrollTo}
        selectedId={selectedId}
      />
      <DatasetChat
        before={
          <>
            <ContaminationReport spec={dataset.sourceSpec} />
            <PreparationPlanDetails plan={dataset.preparationPlan} />
            {active && (
              <PilotRequest
                cell={active}
                disabled={busy || chat.isPending}
                onRequest={guard((message: string) => chat.mutate(message))}
              />
            )}
            {cells.length === 0 && (
              <SourceLanding
                brief={dataset.brief ?? ""}
                busy={busy}
                datasetId={datasetId}
                landing={dataset.state === "landing"}
                landingStatus={extractionStatus(dataset.sourceSpec)}
              />
            )}
          </>
        }
        busy={busy || chat.isPending || acceptCell.isPending || removeCell.isPending}
        cells={all}
        error={dataset.state === "error" ? dataset.error || undefined : undefined}
        focusedCell={cells.find((cell) => cell.id === selectedId)}
        initialRequest={initialRequest}
        landingStatus={extractionStatus(dataset.sourceSpec)}
        live={live}
        onAccept={guard((id: string) => acceptCell.mutate(id))}
        onChooseIntent={async (intentChoice, intentTurnId) => {
          let allowed = false;
          guard(() => {
            allowed = true;
          })();
          if (!allowed) return false;
          await chat.mutateAsync({ intentChoice, intentTurnId });
          return true;
        }}
        onClearFocus={() => setSelectedId(null)}
        onDiscard={guard((id: string) => removeCell.mutate(id))}
        onSelect={scrollTo}
        onSend={async (message: string, uploads?: string[]) => {
          let allowed = false;
          guard(() => {
            allowed = true;
          })();
          if (!allowed) return false;
          const selected = cells.find((cell) => cell.id === selectedId);
          await chat.mutateAsync({
            message:
              selected && message
                ? `For version ${selected.version} (${selected.title}):\n${message}`
                : message,
            source: uploads?.length ? { uploads } : undefined,
          });
          return true;
        }}
        renderCell={(cell) => (
          <NotebookCell
            actions={{
              onActivate: guard(() => patch.mutate({ active: cell.id })),
              onCapability: guard((id: string | null) => {
                const name = capabilityChoices.find((c) => c.id === id)?.name;
                chat.mutate(
                  `Set the capability to ${name ?? "none"}, then add the fewest cells after ${cell.version} that make both contracts hold.`
                );
              }),
              onExport: (fmt) =>
                void downloadExport(
                  datasetId,
                  cell.id,
                  fmt,
                  `${datasetDisplayName(dataset)}-${cell.version || "source"}`
                ).catch((e) => notify.error(e, "Export failed")),
              onFix: guard((problem: string) =>
                chat.mutate(
                  `Fix one contract on version ${cell.version}: ${problem}. The intent is ${intent} and the capability is ${dataset.capabilityName ?? "not set"}. Add the fewest cells after ${cell.version} that make that contract hold. Do not run the quality checks and do not touch anything else.`
                )
              ),
              onIntent: guard((next: "train" | "eval") =>
                chat.mutate(
                  `Set the intent to ${next}, then add the fewest cells after ${cell.version} that make both contracts hold.`
                )
              ),
              onRemove: guard(() => removeCell.mutate(cell.id)),
              onRun: guard(() => run.mutate()),
              onScript: guard((script: string) =>
                editCell.mutate(
                  { cellId: cell.id, script },
                  { onSuccess: () => cell.state !== "proposed" && run.mutate() }
                )
              ),
              onTitle: guard((title: string) => editCell.mutate({ cellId: cell.id, title })),
              onUse: guard((purpose: UsePurpose) => handOff(cell, purpose)),
            }}
            active={cell.id === active?.id}
            capabilities={capabilityChoices}
            capabilityName={dataset.capabilityName ?? ""}
            cell={cell}
            datasetId={datasetId}
            editable={editable}
            intent={intent}
            key={cell.id}
            onSelect={() => setSelectedId(cell.id)}
            running={dataset.state === "running"}
            selected={cell.id === selectedId}
            sourceDetails={
              <SourceDetails
                brief={dataset.brief}
                datasetId={datasetId}
                spec={dataset.sourceSpec}
              />
            }
          />
        )}
        scrollRef={cellsRef}
        state={dataset.state ?? ""}
        turns={turns}
      />
    </div>
  );
}
