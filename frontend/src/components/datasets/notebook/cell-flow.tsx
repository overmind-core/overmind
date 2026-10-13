import { type ReactNode, useCallback, useEffect, useMemo, useRef, useState } from "react";

import {
  Background,
  BackgroundVariant,
  ControlButton,
  Controls,
  type Edge,
  Handle,
  MiniMap,
  type Node,
  type NodeProps,
  Position,
  ReactFlow,
  ReactFlowProvider,
  useNodesState,
  useReactFlow,
} from "@xyflow/react";
import ELK from "elkjs/lib/elk.bundled.js";

import {
  type Motion,
  useVersionMotion,
  VersionMotionFrame,
} from "@/components/datasets/notebook/cell-motion";
import { Icon } from "@/components/ui/icons";
import { usePersistedState } from "@/hooks/use-persisted-state";
import { notify } from "@/lib/notify";
import type { Cell } from "@/openapi";

import "@xyflow/react/dist/style.css";

const GRID = 20;
const GAP = 80;
const MIN_WIDTH = 520;
const MAX_WIDTH = 1200;
const TOP = 24;

type VersionData = {
  cell: Cell;
  width: number;
  placed: boolean;
  motion: Motion | undefined;
  settle: (cellId: string, motionId: number) => void;
  render: (cell: Cell) => ReactNode;
};
type VersionNode = Node<VersionData, "version">;
type Point = { x: number; y: number };

const elk = new ELK();
const snap = (value: number) => Math.round(value / GRID) * GRID;

async function layout(cells: Cell[], heights: Map<string, number>, width: number) {
  const ids = new Set(cells.map((cell) => cell.id));
  const graph = await elk.layout({
    children: cells.map((cell) => ({ height: heights.get(cell.id) ?? 400, id: cell.id, width })),
    edges: cells
      .filter((cell) => cell.parent && ids.has(cell.parent))
      .map((cell) => ({
        id: `${cell.parent}-${cell.id}`,
        sources: [cell.parent!],
        targets: [cell.id],
      })),
    id: "versions",
    layoutOptions: {
      "elk.algorithm": "layered",
      "elk.direction": "DOWN",
      "elk.layered.nodePlacement.bk.fixedAlignment": "LEFTUP",
      "elk.layered.spacing.nodeNodeBetweenLayers": String(GAP),
      "elk.spacing.nodeNode": String(GAP),
    },
  });
  return new Map(
    (graph.children ?? []).map((node) => [node.id, { x: snap(node.x ?? 0), y: snap(node.y ?? 0) }])
  );
}

function VersionNodeView({ data }: NodeProps<VersionNode>) {
  return (
    <div style={{ width: data.width }}>
      <Handle className="!invisible" isConnectable={false} position={Position.Top} type="target" />
      <VersionMotionFrame
        cellId={data.cell.id}
        motion={data.motion}
        placed={data.placed}
        settle={data.settle}
      >
        {data.render(data.cell)}
      </VersionMotionFrame>
      <Handle
        className="!invisible"
        isConnectable={false}
        position={Position.Bottom}
        type="source"
      />
    </div>
  );
}

const NODE_TYPES = { version: VersionNodeView };
const EDGE_STYLE = { stroke: "var(--muted-foreground)", strokeWidth: 1.5 };

export interface FlowFocus {
  id: string;
  request: number;
}

function Canvas({
  cells,
  storageKey,
  focus,
  showMinimap,
  renderVersion,
}: {
  cells: Cell[];
  storageKey: string;
  focus: FlowFocus | null;
  showMinimap: boolean;
  renderVersion: (cell: Cell) => ReactNode;
}) {
  const wrapper = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(0);
  const [dragged, setDragged] = usePersistedState<Record<string, Point>>(storageKey, {});
  const [laid, setLaid] = useState<Map<string, Point> | null>(null);
  const [heights, setHeights] = useState<Map<string, number>>(() => new Map());
  const [nodes, setNodes, onNodesChange] = useNodesState<VersionNode>([]);
  const { getNodes, getViewport, setViewport } = useReactFlow<VersionNode>();
  // The card whose size changed last, held in place when the layout moves it.
  const anchor = useRef<string | null>(null);
  const previousLaid = useRef<Map<string, Point> | null>(null);
  // Until the person pans, zooms or clicks, each new layout re-frames the focus; cards
  // grow as their rows load, so the first layout is not the last.
  const touched = useRef(false);
  const { motions, settle } = useVersionMotion(cells);
  const nodeWidth = Math.min(MAX_WIDTH, Math.max(Math.min(MIN_WIDTH, width - 24), width - 2 * GAP));

  useEffect(() => {
    const element = wrapper.current;
    if (!element) return;
    const observer = new ResizeObserver(() => setWidth(element.clientWidth));
    observer.observe(element);
    setWidth(element.clientWidth);
    return () => observer.disconnect();
  }, []);

  const ids = cells.map((cell) => cell.id).join(",");
  const heightKey = cells.map((cell) => snap(heights.get(cell.id) ?? 0)).join(",");
  // biome-ignore lint/correctness/useExhaustiveDependencies: ids and heightKey stand for cells and heights
  useEffect(() => {
    if (!width || cells.some((cell) => !heights.has(cell.id))) return;
    let cancelled = false;
    layout(cells, heights, nodeWidth)
      .then((positions) => !cancelled && setLaid(positions))
      .catch((error) => notify.error(error, "Flow layout unavailable"));
    return () => {
      cancelled = true;
    };
  }, [ids, heightKey, nodeWidth, width]);

  useEffect(() => {
    setNodes((current) => {
      const previous = new Map(current.map((node) => [node.id, node]));
      return cells.map((cell, index) => {
        const placed = laid?.get(cell.id);
        return {
          data: {
            cell,
            motion: motions.get(cell.id),
            placed: !!placed,
            render: renderVersion,
            settle,
            width: nodeWidth,
          },
          dragHandle: ".version-drag-handle",
          id: cell.id,
          position: dragged[cell.id] ??
            placed ??
            previous.get(cell.id)?.position ?? { x: 0, y: index * 600 },
          style: placed ? undefined : { visibility: "hidden" as const },
          type: "version" as const,
        };
      });
    });
  }, [cells, laid, dragged, motions, nodeWidth, renderVersion, settle, setNodes]);

  const edges = useMemo<Edge[]>(() => {
    const ids = new Set(cells.map((cell) => cell.id));
    return cells
      .filter((cell) => cell.parent && ids.has(cell.parent))
      .map((cell) => ({
        id: `${cell.parent}-${cell.id}`,
        pathOptions: { borderRadius: 0 },
        source: cell.parent!,
        style: EDGE_STYLE,
        target: cell.id,
        type: "smoothstep",
      }));
  }, [cells]);

  const frame = useCallback(
    (id: string | undefined, animate: boolean) => {
      const node = getNodes().find((candidate) => candidate.id === id);
      if (!node || !wrapper.current) return;
      const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
      void setViewport(
        {
          x: wrapper.current.clientWidth / 2 - (node.position.x + nodeWidth / 2),
          y: TOP - node.position.y,
          zoom: 1,
        },
        { duration: animate && !reduced ? 180 : 0 }
      );
    },
    [getNodes, nodeWidth, setViewport]
  );

  useEffect(() => {
    if (!laid || touched.current) return;
    requestAnimationFrame(() => frame(focus?.id ?? cells[0]?.id, false));
  }, [laid, focus, cells, frame]);

  // biome-ignore lint/correctness/useExhaustiveDependencies: only a new layout moves the view
  useEffect(() => {
    const before = previousLaid.current;
    previousLaid.current = laid;
    const id = anchor.current;
    anchor.current = null;
    if (!laid || !before || !id || dragged[id] || !touched.current) return;
    const from = before.get(id);
    const to = laid.get(id);
    if (!from || !to || (from.x === to.x && from.y === to.y)) return;
    const view = getViewport();
    void setViewport({
      x: view.x - (to.x - from.x) * view.zoom,
      y: view.y - (to.y - from.y) * view.zoom,
      zoom: view.zoom,
    });
  }, [laid]);

  // biome-ignore lint/correctness/useExhaustiveDependencies: a new request is the trigger
  useEffect(() => {
    if (focus && laid) frame(focus.id, true);
  }, [focus?.request]);

  return (
    <div
      className="h-full w-full"
      onPointerDownCapture={() => {
        touched.current = true;
      }}
      onWheelCapture={() => {
        touched.current = true;
      }}
      ref={wrapper}
    >
      <ReactFlow<VersionNode>
        aria-label="Dataset versions"
        colorMode="dark"
        deleteKeyCode={null}
        edges={edges}
        elementsSelectable={false}
        maxZoom={1.4}
        minZoom={0.25}
        nodes={nodes}
        nodesConnectable={false}
        nodeTypes={NODE_TYPES}
        onNodeDragStop={(_, node) =>
          setDragged((saved) => ({
            ...saved,
            [node.id]: { x: snap(node.position.x), y: snap(node.position.y) },
          }))
        }
        onNodesChange={(changes) => {
          onNodesChange(changes);
          const measured = changes.filter((change) => change.type === "dimensions");
          if (!measured.length) return;
          setHeights((current) => {
            const next = new Map(current);
            for (const change of measured) {
              if (change.type !== "dimensions" || !change.dimensions) continue;
              const before = current.get(change.id);
              if (before !== undefined && Math.abs(before - change.dimensions.height) > 1)
                anchor.current = change.id;
              next.set(change.id, change.dimensions.height);
            }
            return next;
          });
        }}
        panOnScroll
        proOptions={{ hideAttribution: true }}
        zoomOnDoubleClick={false}
        zoomOnPinch
        zoomOnScroll={false}
      >
        <Background
          bgColor="var(--card)"
          color="var(--border)"
          gap={GRID}
          size={1.5}
          variant={BackgroundVariant.Dots}
        />
        <Controls
          className="!rounded-sm !border !border-border !bg-card !shadow-none [&>button]:!border-border/60 [&>button]:!bg-card [&>button:hover]:!bg-accent [&_svg]:!fill-foreground"
          position="bottom-right"
          showInteractive={false}
        >
          <ControlButton
            aria-label="Reset layout"
            onClick={() => {
              setDragged({});
              requestAnimationFrame(() => frame(cells[0]?.id, true));
            }}
            title="Reset layout"
          >
            <Icon.home />
          </ControlButton>
        </Controls>
        {showMinimap && (
          <MiniMap
            ariaLabel="Dataset versions minimap"
            className="!rounded-sm !border !border-border !bg-card"
            maskColor="color-mix(in oklab, var(--muted) 70%, transparent)"
            nodeColor="var(--muted-foreground)"
            nodeStrokeWidth={0}
            pannable
            position="bottom-left"
            zoomable
          />
        )}
      </ReactFlow>
    </div>
  );
}

export function VersionFlow(props: {
  cells: Cell[];
  storageKey: string;
  focus: FlowFocus | null;
  showMinimap: boolean;
  renderVersion: (cell: Cell) => ReactNode;
}) {
  return (
    <ReactFlowProvider key={props.storageKey}>
      <Canvas {...props} />
    </ReactFlowProvider>
  );
}
