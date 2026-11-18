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

import { Field } from "@/components/datasets/notebook/field";
import { Icon } from "@/components/ui/icons";
import { SPLIT_LABEL } from "@/hooks/use-datasets";
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
const elk = new ELK();

function CellFrame({ id, data }: NodeProps<CellNode>) {
  return (
    <CellMotionFrame complete={data.complete} id={id} motion={data.motion} ready={data.ready}>
      {data.content}
    </CellMotionFrame>
  );
}

const NODE_TYPES = { cell: CellFrame };

const EDGE_LABEL: Record<string, string> = {
  attachment: "Added files",
  derived: "Derived",
};

/** What a connection does: a branch condition, a partition role, a derivation. */
function EdgeTag({ kind, value }: { kind: string; value: string }) {
  if (kind === "condition")
    return (
      <Field label="If" title={value}>
        <span className="truncate font-mono">{value}</span>
      </Field>
    );
  if (kind === "split") return <Field label="Split">{SPLIT_LABEL[value] ?? value}</Field>;
  const label = EDGE_LABEL[kind];
  return label ? (
    <span className="inline-flex h-6 items-center rounded-sm border border-border/70 bg-card px-2 text-xs text-muted-foreground">
      {label}
    </span>
  ) : null;
}

function CellConnector(props: EdgeProps) {
  const [path] = getSmoothStepPath({ ...props, borderRadius: 0, offset: 0 });
  const data = props.data as { kind: string; value: string } | undefined;
  return (
    <>
      <BaseEdge id={props.id} path={path} style={props.style} />
      {data && data.kind !== "step" && (
        <EdgeLabelRenderer>
          <div
            className="absolute max-w-80"
            style={{
              transform: `translate(-50%, -100%) translate(${props.targetX}px, ${props.targetY - 32}px)`,
            }}
          >
            <EdgeTag kind={data.kind} value={data.value} />
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
  showMinimap: boolean;
  focus: { id: string; request: number } | null;
  renderCell: (cell: Cell) => ReactNode;
  motions: Map<string, CellMotion>;
  onMotionComplete: CompleteCellMotion;
};

function Canvas({
  cells,
  edges,
  showMinimap,
  focus,
  renderCell,
  motions,
  onMotionComplete,
}: Props) {
  const wrapper = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(960);
  const [inset, setInset] = useState(16);
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
      const padding = window.innerWidth >= 640 ? 16 : 8;
      setWidth(Math.max(1, container - 40 - padding));
      setInset(padding + (element.clientWidth - container) / 2);
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
    let animationFrame = 0;
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
        const anchor = current.find((node) => node.data.ready);
        const anchorLayout = layout.children?.find((node) => node.id === anchor?.id);
        // The layout owns every position; a relayout keeps the first placed cell still.
        const offset =
          anchor && anchorLayout
            ? {
                x: anchor.position.x - (anchorLayout.x ?? 0),
                y: anchor.position.y - (anchorLayout.y ?? 0),
              }
            : { x: 0, y: 0 };
        const placedPosition = (id: string): XYPosition => {
          const placed = layout.children?.find((node) => node.id === id);
          return {
            x: Math.round((placed?.x ?? 0) + offset.x),
            y: Math.round((placed?.y ?? 0) + offset.y),
          };
        };
        const key = focus ? `${focus.id}:${focus.request}` : "";
        const requested =
          focus && focusRequest.current !== key && current.some((node) => node.id === focus.id);
        const frame = requested ? focus.id : !positioned.current ? current[0]?.id : undefined;
        const moved = current.some((node) => {
          const target = placedPosition(node.id);
          return node.data.ready && (node.position.x !== target.x || node.position.y !== target.y);
        });
        const animate =
          moved &&
          positioned.current &&
          !requested &&
          viewportAnchor.current?.width === width &&
          document.visibilityState === "visible" &&
          !window.matchMedia("(prefers-reduced-motion: reduce)").matches;
        const starts = new Map(
          getNodes().map((node) => [
            node.id,
            node.data.ready ? node.position : placedPosition(node.id),
          ])
        );
        const started = performance.now();
        const place = (now: number) => {
          if (cancelled) return;
          const progress = animate ? Math.min(1, (now - started) / 200) : 1;
          const eased = 1 - (1 - progress) ** 3;
          // Move graph coordinates so connectors follow their cells on every frame.
          setNodes((latest) =>
            latest.map((node) => {
              if (!ids.has(node.id)) return node;
              const target = placedPosition(node.id);
              const start = starts.get(node.id) ?? target;
              return {
                ...node,
                data: node.data.ready ? node.data : { ...node.data, ready: true },
                position: {
                  x: start.x + (target.x - start.x) * eased,
                  y: start.y + (target.y - start.y) * eased,
                },
              };
            })
          );
          if (progress < 1) animationFrame = requestAnimationFrame(place);
        };
        place(started);
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
      cancelAnimationFrame(animationFrame);
    };
  }, [
    initialized,
    topology,
    measurements,
    width,
    inset,
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
        // Cells are placed by the layout and never deleted from the canvas.
        ariaLabelConfig={{
          "node.a11yDescription.default": "Press enter or space to select a cell.",
          "node.a11yDescription.keyboardDisabled": "Press enter or space to select a cell.",
        }}
        colorMode="dark"
        deleteKeyCode={null}
        edges={visibleEdges}
        edgesReconnectable={false}
        edgeTypes={EDGE_TYPES}
        maxZoom={1.4}
        minZoom={0.25}
        nodes={nodes}
        nodesConnectable={false}
        nodesDraggable={false}
        nodeTypes={NODE_TYPES}
        onNodesChange={onNodesChange}
        panOnScroll
        proOptions={{ hideAttribution: true }}
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
          className="!right-1.5 !bottom-3 !m-0 !rounded-sm !border !border-border !bg-card !shadow-none [&>button]:!border-border/60 [&>button]:!bg-card [&>button:hover]:!bg-accent [&_svg]:!fill-foreground"
          fitViewOptions={{ maxZoom: 1, minZoom: 0.25, padding: 0.18 }}
          position="bottom-right"
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
            className="!mr-12 !rounded-sm !border !border-border !bg-card"
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
    <ReactFlowProvider>
      <Canvas {...props} />
    </ReactFlowProvider>
  );
}
