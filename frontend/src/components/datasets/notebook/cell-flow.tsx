import { type ReactNode, useEffect, useMemo, useRef, useState } from "react";

import {
  Background,
  BackgroundVariant,
  BaseEdge,
  ControlButton,
  Controls,
  type Edge,
  EdgeLabelRenderer,
  type EdgeProps,
  getSmoothStepPath,
  MiniMap,
  type Node,
  type NodeProps,
  ReactFlow,
  ReactFlowProvider,
  useNodesInitialized,
  useNodesState,
  useReactFlow,
  type XYPosition,
} from "@xyflow/react";
import ELK from "elkjs/lib/elk.bundled.js";

import { Icon } from "@/components/ui/icons";
import { usePersistedState } from "@/hooks/use-persisted-state";
import { notify } from "@/lib/notify";
import type { Cell } from "@/openapi";
import { type CellMotion, CellMotionFrame, type CompleteCellMotion } from "./cell-motion";

import "@xyflow/react/dist/style.css";

type CellNode = Node<
  {
    content: ReactNode;
    motion?: CellMotion;
    ready: boolean;
    complete: CompleteCellMotion;
  },
  "cell"
>;
const GRID: [number, number] = [20, 20];
const elk = new ELK();

function CellFrame({ id, data }: NodeProps<CellNode>) {
  return (
    <CellMotionFrame complete={data.complete} id={id} motion={data.motion} ready={data.ready}>
      {data.content}
    </CellMotionFrame>
  );
}

const NODE_TYPES = { cell: CellFrame };

function CellConnector(props: EdgeProps) {
  const [path] = getSmoothStepPath({ ...props, borderRadius: 0, offset: 0 });
  return (
    <>
      <BaseEdge id={props.id} path={path} style={props.style} />
      {props.label && (
        <EdgeLabelRenderer>
          <div
            className="absolute max-w-80 truncate rounded-sm border border-input bg-card px-2 py-1 text-xs text-foreground"
            style={{
              transform: `translate(-50%, -100%) translate(${props.targetX}px, ${props.targetY - 32}px)`,
            }}
            title={String(props.label)}
          >
            {props.label}
          </div>
        </EdgeLabelRenderer>
      )}
    </>
  );
}

const EDGE_TYPES = { cell: CellConnector };

type Props = {
  cells: Cell[];
  edges: Edge[];
  storageKey: string;
  showMinimap: boolean;
  focus: { id: string; request: number } | null;
  renderCell: (cell: Cell) => ReactNode;
  motions: Map<string, CellMotion>;
  onMotionComplete: CompleteCellMotion;
};

function Canvas({
  cells,
  edges,
  storageKey,
  showMinimap,
  focus,
  renderCell,
  motions,
  onMotionComplete,
}: Props) {
  const wrapper = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(960);
  const [inset, setInset] = useState(40);
  const [positions, savePositions] = usePersistedState<Record<string, XYPosition>>(storageKey, {});
  const [nodes, setNodes, onNodesChange] = useNodesState<CellNode>([]);
  const { getNodes, getViewport, setViewport } = useReactFlow<CellNode>();
  const initialized = useNodesInitialized();
  const measurements = nodes.map((node) => `${node.id}:${node.measured?.height ?? 500}`).join(",");
  const topology = JSON.stringify([
    cells.map((cell) => cell.id),
    edges.map((edge) => [edge.source, edge.target]),
  ]);
  const positioned = useRef(false);
  const viewportAnchor = useRef<{
    id: string;
    point: XYPosition;
    width: number;
    inset: number;
  } | null>(null);
  const focusRequest = useRef("");

  useEffect(() => {
    const element = wrapper.current;
    if (!element) return;
    const observer = new ResizeObserver(() => {
      const container = Math.min(1280, element.clientWidth);
      setWidth(Math.max(1, container - 40 - (window.innerWidth >= 640 ? 16 : 8)));
      setInset(40 + (element.clientWidth - container) / 2);
    });
    observer.observe(element);
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    setNodes((existing) =>
      cells.map((cell, index) => {
        const previous = existing.find((node) => node.id === cell.id);
        return {
          ...previous,
          ariaLabel: cell.title,
          data: {
            complete: onMotionComplete,
            content: renderCell(cell),
            motion: motions.get(cell.id),
            ready: previous?.data.ready ?? false,
          },
          dragHandle: ".workshop-cell-node article > div:first-of-type",
          id: cell.id,
          position: previous?.position ?? { x: 0, y: index * 580 },
          style: { width },
          type: "cell",
        };
      })
    );
  }, [cells, width, renderCell, setNodes, motions, onMotionComplete]);

  // biome-ignore lint/correctness/useExhaustiveDependencies: topology and measurements describe layout inputs without refitting for content-only updates.
  useEffect(() => {
    if (!initialized) return;
    let cancelled = false;
    const current = getNodes();
    const ids = new Set(current.map((node) => node.id));
    if (cells.some((cell) => !ids.has(cell.id)) || current.some((node) => !node.measured?.height))
      return;
    elk
      .layout({
        children: current.map((node) => ({
          height: node.measured?.height ?? 500,
          id: node.id,
          width,
        })),
        edges: edges
          .filter((edge) => ids.has(edge.source) && ids.has(edge.target))
          .map((edge) => ({ id: edge.id, sources: [edge.source], targets: [edge.target] })),
        id: "workshop",
        layoutOptions: {
          "elk.algorithm": "layered",
          "elk.direction": "DOWN",
          "elk.layered.nodePlacement.bk.fixedAlignment": "LEFTUP",
          "elk.layered.spacing.nodeNodeBetweenLayers": "80",
          "elk.spacing.nodeNode": "80",
        },
      })
      .then((layout) => {
        if (cancelled) return;
        const placedPosition = (id: string): XYPosition => {
          const saved = positions[id];
          const placed = layout.children?.find((node) => node.id === id);
          const point =
            saved && Number.isFinite(saved.x) && Number.isFinite(saved.y) ? saved : placed;
          return {
            x: Math.round((point?.x ?? 0) / 20) * 20,
            y: Math.round((point?.y ?? 0) / 20) * 20,
          };
        };
        setNodes((latest) =>
          latest.map((node) => ({
            ...node,
            data: { ...node.data, ready: ids.has(node.id) },
            position: placedPosition(node.id),
          }))
        );
        const key = focus ? `${focus.id}:${focus.request}` : "";
        const requested =
          focus && focusRequest.current !== key && current.some((node) => node.id === focus.id);
        const frame = requested ? focus.id : !positioned.current ? current[0]?.id : undefined;
        if (frame) {
          positioned.current = true;
          if (requested) focusRequest.current = key;
          const point = placedPosition(frame);
          viewportAnchor.current = { id: frame, inset, point, width };
          void setViewport(
            { x: inset - point.x, y: 12 - point.y, zoom: 1 },
            {
              duration:
                !requested || window.matchMedia("(prefers-reduced-motion: reduce)").matches
                  ? 0
                  : 180,
            }
          );
        } else if (viewportAnchor.current) {
          const anchor = viewportAnchor.current;
          const point = placedPosition(anchor.id);
          if (anchor.width !== width || anchor.inset !== inset) {
            const viewport = getViewport();
            void setViewport({
              x: viewport.x + (anchor.point.x - point.x) * viewport.zoom + inset - anchor.inset,
              y: viewport.y + (anchor.point.y - point.y) * viewport.zoom,
              zoom: viewport.zoom,
            });
          }
          viewportAnchor.current = { ...anchor, inset, point, width };
        }
      })
      .catch((error) => {
        if (!cancelled) notify.error(error, "Flow layout unavailable");
      });
    return () => {
      cancelled = true;
    };
  }, [
    initialized,
    topology,
    measurements,
    width,
    inset,
    positions,
    focus,
    getNodes,
    getViewport,
    setNodes,
    setViewport,
  ]);

  const visibleEdges = useMemo(() => {
    const ids = new Set(cells.map((cell) => cell.id));
    return edges.filter((edge) => ids.has(edge.source) && ids.has(edge.target));
  }, [cells, edges]);

  return (
    <div className="h-full min-h-0" ref={wrapper}>
      <ReactFlow<CellNode>
        aria-label="Dataset transformation flow"
        colorMode="dark"
        deleteKeyCode={null}
        edges={visibleEdges}
        edgesReconnectable={false}
        edgeTypes={EDGE_TYPES}
        maxZoom={1.4}
        minZoom={0.25}
        nodes={nodes}
        nodesConnectable={false}
        nodeTypes={NODE_TYPES}
        onNodesChange={(changes) => {
          onNodesChange(changes);
          const moved = changes.filter(
            (change) => change.type === "position" && change.position && !change.dragging
          );
          if (moved.length)
            savePositions((previous) => {
              const next = { ...previous };
              for (const change of moved)
                if (change.type === "position" && change.position)
                  next[change.id] = change.position;
              return next;
            });
        }}
        panOnScroll
        proOptions={{ hideAttribution: true }}
        snapGrid={GRID}
        snapToGrid
        zoomOnDoubleClick={false}
        zoomOnPinch
        zoomOnScroll
      >
        <Background
          bgColor="var(--card)"
          color="var(--border)"
          gap={20}
          size={1}
          variant={BackgroundVariant.Dots}
        />
        <Controls
          className="!rounded-sm !border !border-border !bg-card !shadow-none [&>button]:!border-border/60 [&>button]:!bg-card [&>button:hover]:!bg-accent [&_svg]:!fill-foreground"
          fitViewOptions={{ maxZoom: 1, minZoom: 0.25, padding: 0.18 }}
          showInteractive={false}
        >
          <ControlButton
            aria-label="Reset view"
            onClick={() => {
              const first = getNodes()[0];
              if (first) {
                viewportAnchor.current = { id: first.id, inset, point: first.position, width };
                void setViewport({
                  x: inset - first.position.x,
                  y: 12 - first.position.y,
                  zoom: 1,
                });
              }
            }}
            title="Reset view"
          >
            <Icon.home />
          </ControlButton>
        </Controls>
        {showMinimap && (
          <MiniMap
            ariaLabel="Dataset transformation minimap"
            className="!rounded-sm !border !border-border !bg-card"
            maskColor="color-mix(in oklab, var(--muted) 70%, transparent)"
            nodeColor="var(--muted-foreground)"
            nodeStrokeWidth={0}
            pannable
            zoomable
          />
        )}
      </ReactFlow>
    </div>
  );
}

export function CellFlow(props: Props) {
  return (
    <ReactFlowProvider key={props.storageKey}>
      <Canvas {...props} />
    </ReactFlowProvider>
  );
}
