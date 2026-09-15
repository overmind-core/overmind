import { useEffect, useMemo, useRef, useState } from "react";

import {
  Background,
  BackgroundVariant,
  Controls,
  type Edge,
  getNodesBounds,
  MiniMap,
  ReactFlow,
  ReactFlowProvider,
  useEdgesState,
  useNodesInitialized,
  useNodesState,
  useReactFlow,
} from "@xyflow/react";

import { useTheme } from "@/components/theme-provider";
import { Button } from "@/components/ui/button";
import { Icon } from "@/components/ui/icons";
import { usePersistedState } from "@/hooks/use-persisted-state";
import { cn } from "@/lib/utils";
import type { Capability } from "@/openapi";
import {
  buildTrajectoryGraph,
  collectAncestors,
  type TrajectoryNode,
} from "./buildTrajectoryGraph";
import { trajectoryNodeTypes } from "./TrajectoryNodes";

import "@xyflow/react/dist/style.css";

const FIT_VIEW_OPTIONS = { maxZoom: 1, minZoom: 0.3, padding: 0.18 };
const DEFAULT_EDGE_OPTIONS = { type: "smoothstep" as const };

const FLOW_SCROLL_LOCK_KEY = "capabilityFlow:scrollLocked";
const FLOW_MINIMAP_KEY = "capabilityFlow:showMinimap";

function FlowCanvas({
  capability,
  expanded,
  onGraphHeight,
  scrollCaptured,
  showMinimap,
}: {
  capability: Capability;
  expanded: boolean;
  onGraphHeight: (height: number) => void;
  scrollCaptured: boolean;
  showMinimap: boolean;
}) {
  // Keyed on content: the detail query refetches every 15s, and an identity-keyed
  // rebuild would reset dragged node positions and re-fit under the user.
  const flowKey = useMemo(() => JSON.stringify(capability.flow), [capability.flow]);
  // biome-ignore lint/correctness/useExhaustiveDependencies: flowKey is the stable signature of capability.flow.
  const graph = useMemo(() => buildTrajectoryGraph(capability.flow, capability.name), [flowKey]);
  const [nodes, setNodes, onNodesChange] = useNodesState<TrajectoryNode>([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState(graph.edges);
  const { fitView, getNodes } = useReactFlow();
  const { resolvedTheme } = useTheme();
  const nodesInitialized = useNodesInitialized();
  const wrapperRef = useRef<HTMLDivElement>(null);

  const [focusId, setFocusId] = useState<string | null>(null);
  const ancestors = useMemo(
    () => (focusId ? collectAncestors(graph, focusId) : null),
    [graph, focusId]
  );

  useEffect(() => {
    let cancelled = false;
    setEdges(graph.edges);
    setFocusId(null);
    graph.relayout(graph.nodes).then((next) => {
      if (!cancelled) setNodes(next);
    });
    return () => {
      cancelled = true;
    };
  }, [graph, setNodes, setEdges]);

  // Second pass with measured heights; a capability switch re-toggles nodesInitialized.
  useEffect(() => {
    if (!nodesInitialized) return;
    let cancelled = false;
    graph.relayout(getNodes() as TrajectoryNode[]).then((next) => {
      if (cancelled) return;
      setNodes(next);
      onGraphHeight(getNodesBounds(next).height);
      requestAnimationFrame(() => fitView(FIT_VIEW_OPTIONS));
    });
    return () => {
      cancelled = true;
    };
  }, [nodesInitialized, graph, fitView, getNodes, setNodes, onGraphHeight]);

  // Re-fit when the container resizes from hidden/zero-size to visible.
  useEffect(() => {
    const el = wrapperRef.current;
    if (!el) return;
    const observer = new ResizeObserver(() => {
      if (el.clientWidth > 0 && el.clientHeight > 0) fitView(FIT_VIEW_OPTIONS);
    });
    observer.observe(el);
    return () => observer.disconnect();
  }, [fitView]);

  // Reframe after the expand/collapse transition changes the canvas size.
  // biome-ignore lint/correctness/useExhaustiveDependencies: `expanded` is the intended re-fit trigger.
  useEffect(() => {
    const id = requestAnimationFrame(() => fitView(FIT_VIEW_OPTIONS));
    return () => cancelAnimationFrame(id);
  }, [expanded, fitView]);

  const displayNodes = useMemo(
    () =>
      nodes.map((node) => ({
        ...node,
        className: cn(
          "transition-opacity duration-200",
          ancestors && !ancestors.nodeIds.has(node.id) && "opacity-25"
        ),
      })),
    [nodes, ancestors]
  );

  const displayEdges = useMemo(
    () =>
      edges.map((edge: Edge) => {
        if (!ancestors) return edge;
        if (ancestors.edgeIds.has(edge.id)) {
          return {
            ...edge,
            style: { ...edge.style, strokeOpacity: 0.9, strokeWidth: 2 },
          };
        }
        return {
          ...edge,
          labelStyle: { ...(edge.labelStyle as object), opacity: 0.2 },
          style: { ...edge.style, strokeOpacity: 0.08 },
        };
      }),
    [edges, ancestors]
  );

  return (
    <ReactFlow
      aria-label="Capability trajectory map"
      colorMode={resolvedTheme}
      defaultEdgeOptions={DEFAULT_EDGE_OPTIONS}
      edges={displayEdges}
      elementsSelectable
      fitView
      fitViewOptions={FIT_VIEW_OPTIONS}
      maxZoom={1.4}
      minZoom={0.25}
      nodes={displayNodes}
      nodesConnectable={false}
      nodeTypes={trajectoryNodeTypes}
      onEdgesChange={onEdgesChange}
      onInit={(instance) => instance.fitView(FIT_VIEW_OPTIONS)}
      onNodeClick={(_, node) => setFocusId((prev) => (prev === node.id ? null : node.id))}
      onNodesChange={onNodesChange}
      onPaneClick={() => setFocusId(null)}
      // Locked lets the wheel scroll the page straight through the canvas.
      panOnScroll={scrollCaptured}
      preventScrolling={scrollCaptured}
      proOptions={{ hideAttribution: true }}
      ref={wrapperRef}
      selectionOnDrag
      zoomOnDoubleClick={false}
      zoomOnScroll={scrollCaptured}
    >
      <Background
        bgColor="var(--background)"
        color="var(--border)"
        gap={20}
        size={1.5}
        variant={BackgroundVariant.Dots}
      />
      <Controls
        className="!rounded-sm !border !border-border !bg-card !shadow-none [&>button]:!border-border/60 [&>button]:!bg-card [&>button:hover]:!bg-accent [&_svg]:!fill-foreground"
        showInteractive={false}
      />
      {showMinimap && (
        <MiniMap
          ariaLabel="Trajectory minimap"
          className="!rounded-sm !border !border-border !bg-card"
          maskColor="color-mix(in oklab, var(--muted) 70%, transparent)"
          nodeColor="var(--muted-foreground)"
          nodeStrokeWidth={0}
          pannable
          zoomable
        />
      )}
    </ReactFlow>
  );
}

type TrajectoryFlowTabProps = {
  capability: Capability;
  expanded: boolean;
  onExpandedChange: (next: boolean) => void;
};

export function TrajectoryFlowTab({
  capability,
  expanded,
  onExpandedChange,
}: TrajectoryFlowTabProps) {
  // Null until the nodes report their bounds; the CSS clamp covers that first paint.
  const [graphHeight, setGraphHeight] = useState<number | null>(null);
  // Inflated by the fit padding so the graph renders at natural zoom instead of floating.
  const canvasHeight = graphHeight
    ? Math.round(Math.min(760, Math.max(320, graphHeight / (1 - 2 * FIT_VIEW_OPTIONS.padding))))
    : null;
  const [scrollLocked, setScrollLocked] = usePersistedState(FLOW_SCROLL_LOCK_KEY, true);
  const [showMinimap, setShowMinimap] = usePersistedState(FLOW_MINIMAP_KEY, false);

  // Full screen leaves no page to scroll, so the canvas always takes the wheel.
  const scrollCaptured = expanded || !scrollLocked;

  useEffect(() => {
    if (!expanded) return;
    const handleKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onExpandedChange(false);
    };
    window.addEventListener("keydown", handleKey);
    return () => window.removeEventListener("keydown", handleKey);
  }, [expanded, onExpandedChange]);

  if ((capability.flow?.trajectoryMap ?? []).length === 0) {
    return (
      <section aria-label="Trajectory map">
        <div className="flex h-32 items-center justify-center rounded-md border border-border bg-card px-4">
          <p className="text-sm text-muted-foreground">
            No trajectory map — rescan the connected repository.
          </p>
        </div>
      </section>
    );
  }

  return (
    <section aria-label="Trajectory map">
      <div
        className={cn(
          "overflow-hidden rounded-md border border-border bg-background",
          // `inset-2` fills the app card bezel.
          expanded
            ? "fixed inset-2 z-30"
            : cn(
                "relative w-full",
                canvasHeight === null && "h-[clamp(360px,calc(100dvh-31.5rem),760px)]"
              )
        )}
        style={!expanded && canvasHeight !== null ? { height: canvasHeight } : undefined}
      >
        <div className="absolute right-3 top-3 z-10 flex items-center gap-1.5">
          {!expanded && (
            <Button
              aria-label={scrollLocked ? "Unlock canvas scroll" : "Lock canvas scroll"}
              aria-pressed={!scrollLocked}
              className="bg-card/85"
              onClick={() => setScrollLocked(!scrollLocked)}
              size="icon"
              title={
                scrollLocked
                  ? "Scroll locked — the wheel scrolls the page. Click to pan/zoom the graph."
                  : "Scroll unlocked — the wheel pans and zooms the graph. Click to scroll the page."
              }
              variant="secondary"
            >
              {scrollLocked ? <Icon.lock /> : <Icon.unlock />}
            </Button>
          )}
          <Button
            aria-label={showMinimap ? "Hide minimap" : "Show minimap"}
            aria-pressed={showMinimap}
            className={cn(" bg-card/85", showMinimap && "bg-accent")}
            onClick={() => setShowMinimap(!showMinimap)}
            size="icon"
            title={showMinimap ? "Hide minimap" : "Show minimap"}
            variant="secondary"
          >
            <Icon.minimap />
          </Button>
          <Button
            aria-label={expanded ? "Exit full screen" : "Expand to full screen"}
            aria-pressed={expanded}
            className="bg-card/85"
            onClick={() => onExpandedChange(!expanded)}
            size="icon"
            title={expanded ? "Exit full screen (Esc)" : "Expand to full screen"}
            variant="secondary"
          >
            <Icon.aspectRatio />
          </Button>
        </div>

        <ReactFlowProvider>
          <FlowCanvas
            capability={capability}
            expanded={expanded}
            onGraphHeight={setGraphHeight}
            scrollCaptured={scrollCaptured}
            showMinimap={showMinimap}
          />
        </ReactFlowProvider>
      </div>
    </section>
  );
}
