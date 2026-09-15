import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Panel, PanelGroup, PanelResizeHandle } from "react-resizable-panels";

import { useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate } from "@tanstack/react-router";

import { NotebookCell, type UsePurpose } from "@/components/datasets/notebook/cell";
import { DatasetChat, type LiveTurn } from "@/components/datasets/notebook/chat";
import { NotebookOutline } from "@/components/datasets/notebook/outline";
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
  const capabilities = capabilitiesQuery.data?.results ?? [];

  const patch = usePatchDatasetMutation(datasetId);
  const editCell = useEditCellMutation(datasetId);
  const removeCell = useRemoveCellMutation(datasetId);
  const acceptCell = useAcceptCellMutation(datasetId);
  const run = useRunMutation(datasetId);
  const chat = useChatMutation(datasetId);

  const [selectedId, setSelectedId] = useState<string | null>(cellParam ?? null);
  const [live, setLive] = useState<LiveTurn | null>(null);
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
        else setLive(null);
        refresh();
        break;
      case "chat_delta":
        setLive((prev) => ({
          cells: prev?.cells ?? [],
          steps: prev?.steps ?? [],
          text: (prev?.text ?? "") + event.text,
        }));
        break;
      case "chat_step": {
        const { type: _type, ...part } = event;
        setLive((prev) => ({
          cells: prev?.cells ?? [],
          steps: [...(prev?.steps ?? []), { ...part, type: "activity" }],
          text: prev?.text ?? "",
        }));
        break;
      }
      case "chat_cell":
        setLive((prev) => ({
          cells: [...(prev?.cells ?? []), { action: event.action, id: event.cell_id }],
          steps: prev?.steps ?? [],
          text: prev?.text ?? "",
        }));
        refresh();
        break;
      case "chat_failed":
        setLive(null);
        notify.error(new Error(event.error), "The agent stopped");
        refresh();
        break;
      case "replay.done":
        break;
      default:
        refresh();
    }
  });

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
    column.scrollTo({ behavior: "smooth", top: column.scrollTop + top - 8 });
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
    <div className="flex h-full min-h-0 flex-col">
      <PanelGroup className="min-h-0 flex-1" direction="horizontal">
        <Panel className="flex flex-col" defaultSize={66} id="notebook" minSize={40} order={1}>
          <div className="relative flex min-h-0 flex-1">
            <NotebookOutline
              activeId={active?.id ?? null}
              cells={cells}
              onSelect={scrollTo}
              selectedId={selectedId}
            />
            <div className="min-h-0 min-w-0 flex-1 overflow-y-auto pt-2 pb-6 pl-10" ref={cellsRef}>
              {cells.length === 0 ? (
                <p className="p-3 text-xs text-muted-foreground">
                  {dataset.state === "landing" ? "Landing the source…" : "Nothing landed."}
                </p>
              ) : (
                cells.map((cell) => (
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
                      onTitle: guard((title: string) =>
                        editCell.mutate({ cellId: cell.id, title })
                      ),
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
                  />
                ))
              )}
            </div>
          </div>
        </Panel>
        <PanelResizeHandle className="relative w-px bg-border/60 transition-colors hover:bg-foreground/40 data-[resize-handle-state='drag']:bg-foreground/60" />
        <Panel defaultSize={34} id="chat" minSize={22} order={2}>
          <DatasetChat
            busy={busy}
            cells={all}
            error={dataset.state === "error" ? dataset.error || undefined : undefined}
            live={live}
            onAccept={guard((id: string) => acceptCell.mutate(id))}
            onDiscard={guard((id: string) => removeCell.mutate(id))}
            onSelect={scrollTo}
            onSend={guard((message: string) => chat.mutate(message))}
            turns={chatOf(dataset)}
          />
        </Panel>
      </PanelGroup>
    </div>
  );
}
