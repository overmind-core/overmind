import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import {
  Background,
  BackgroundVariant,
  type Edge,
  getViewportForBounds,
  MarkerType,
  MiniMap,
  type NodeChange,
  Position,
  ReactFlow,
  ReactFlowProvider,
  useEdgesState,
  useNodesInitialized,
  useNodesState,
  useReactFlow,
  type Viewport,
} from "@xyflow/react";

import { Button } from "@/components/ui/button";
import { DismissibleAlert } from "@/components/ui/dismissible-alert";
import { Icon } from "@/components/ui/icons";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { SearchInput } from "@/components/ui/search-input";
import { LoadingState } from "@/components/ui/spinner";
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip";
import { useEvalMatrixQuery } from "@/hooks/use-evaluations";
import { usePersistedState } from "@/hooks/use-persisted-state";
import { errorMessage } from "@/lib/notify";
import { cn } from "@/lib/utils";
import type { Capability, EvalMatrix } from "@/openapi";
import {
  buildTrajectoryGraph,
  collectAncestors,
  type TrajectoryNode,
} from "./buildTrajectoryGraph";
import { TrajectoryConnector } from "./TrajectoryConnector";
import { TrajectoryEdgeBridges } from "./TrajectoryEdgeBridges";
import { trajectoryNodeTypes } from "./TrajectoryNodes";
import { TrajectoryScoresProvider, useTrajectoryRun } from "./TrajectoryRunScores";
import { trajectoryBounds, trajectoryOverviewBounds } from "./trajectoryBounds";
import { trajectoryRunPath } from "./trajectoryRunPath";
import { projectTrajectory, searchTrajectory } from "./trajectoryVisibility";

import "@xyflow/react/dist/style.css";

const FIT_VIEW_OPTIONS = { maxZoom: 1, minZoom: 0.01, padding: 0.07 };
const EDGE_TYPES = { condition: TrajectoryConnector, continuation: TrajectoryConnector };
const DEFAULT_EDGE_OPTIONS = {
  markerEnd: {
    color: "var(--muted-foreground)",
    height: 14,
    type: MarkerType.ArrowClosed,
    width: 14,
  },
  type: "condition",
};

const FLOW_SCROLL_LOCK_KEY = "capabilityFlow:scrollLocked";
const FLOW_MINIMAP_KEY = "capabilityFlow:showMinimap";

function viewportMotion() {
  return {
    duration: window.matchMedia?.("(prefers-reduced-motion: reduce)").matches ? 0 : 420,
    ease: (t: number) => 1 - (1 - t) ** 3,
    interpolate: "linear" as const,
  };
}

function FlowCanvas({
  canvasActions,
  capability,
  evaluations,
  expanded,
  onGraphHeight,
  scrollCaptured,
  showMinimap,
}: {
  canvasActions: React.ReactNode;
  capability: Capability;
  evaluations: EvalMatrix[];
  expanded: boolean;
  onGraphHeight: (height: number) => void;
  scrollCaptured: boolean;
  showMinimap: boolean;
}) {
  const [query, setQuery] = useState("");
  const [searchOpen, setSearchOpen] = useState(false);
  const [matchIndex, setMatchIndex] = useState(0);
  const run = useTrajectoryRun();
  // Keyed on content: the detail query refetches every 15s, and an identity-keyed
  // rebuild would reset dragged node positions and re-fit under the user.
  const flowKey = JSON.stringify([
    capability.flow,
    capability.name,
    capability.entrypointFn,
    evaluations,
  ]);
  // biome-ignore lint/correctness/useExhaustiveDependencies: flowKey includes every graph input without identity-only refetch changes.
  const fullGraph = useMemo(
    () =>
      buildTrajectoryGraph(capability.flow, capability.name, evaluations, capability.entrypointFn),
    [flowKey]
  );
  const runPath = useMemo(
    () =>
      run.highlighted && run.trace
        ? trajectoryRunPath(
            fullGraph,
            run.trace.spans,
            capability.id,
            run.trace.traceStatus === "completed"
          )
        : null,
    [fullGraph, run.highlighted, run.trace, capability.id]
  );
  const hasRunPath = !!runPath?.mappedSpans;
  const displayedRunId = run.trace?.traceId;
  const [collapsed, setCollapsed] = useState<Set<string>>(
    () =>
      new Set(
        fullGraph.nodes
          .filter((node) => ["actionGroup", "action"].includes(node.data.kind))
          .map((node) => node.id)
      )
  );
  const normalDisclosure = useRef<Set<string> | null>(null);
  const revealedRun = useRef("");
  useEffect(() => {
    if (!runPath || !displayedRunId) {
      if (normalDisclosure.current) setCollapsed(normalDisclosure.current);
      normalDisclosure.current = null;
      revealedRun.current = "";
      return;
    }
    const key = `${displayedRunId}:${[...runPath.expandedIds].sort().join("|")}`;
    if (revealedRun.current === key) return;
    normalDisclosure.current ??= new Set(collapsed);
    revealedRun.current = key;
    setCollapsed(
      new Set([...normalDisclosure.current].filter((id) => !runPath.expandedIds.has(id)))
    );
  }, [runPath, displayedRunId, collapsed]);
  const matches = useMemo(
    () => searchTrajectory(runPath?.graph ?? fullGraph, query),
    [fullGraph, runPath, query]
  );
  const match = matches[matchIndex % Math.max(1, matches.length)];
  const searchTarget = match?.id;
  const graph = useMemo(
    () => projectTrajectory(runPath?.graph ?? fullGraph, collapsed, searchTarget),
    [fullGraph, runPath, collapsed, searchTarget]
  );
  const [nodes, setNodes, onNodesChange] = useNodesState<TrajectoryNode>([]);
  const manualPositions = useRef(new Map<string, { x: number; y: number }>());
  const changeNodes = useCallback(
    (changes: NodeChange<TrajectoryNode>[]) => {
      for (const change of changes) {
        if (change.type === "position" && change.position)
          manualPositions.current.set(change.id, change.position);
      }
      onNodesChange(changes);
    },
    [onNodesChange]
  );
  const preservePositions = useCallback(
    (next: TrajectoryNode[]) =>
      next.map((node) => {
        const position = manualPositions.current.get(node.id);
        return position ? { ...node, position } : node;
      }),
    []
  );
  const [edges, setEdges, onEdgesChange] = useEdgesState(graph.edges);
  const { fitView, getNodes, getInternalNode, getViewport, setViewport, zoomIn, zoomOut } =
    useReactFlow<TrajectoryNode>();
  const beforeSearch = useRef<Viewport | null>(null);
  const nodesInitialized = useNodesInitialized();
  const measurementKey = nodes.map((node) => `${node.id}:${node.measured?.height}`).join("|");
  const hasInteracted = useRef(false);
  const framedSearch = useRef<string | undefined>(undefined);
  const framedRun = useRef<string | undefined>(undefined);
  const wrapperRef = useRef<HTMLDivElement>(null);
  const searchTargetRef = useRef(searchTarget);
  searchTargetRef.current = searchTarget;
  const fitOverview = useCallback(
    (visible: TrajectoryNode[] = getNodes(), animate = false) => {
      const wrapper = wrapperRef.current;
      if (!wrapper?.clientWidth || !wrapper.clientHeight || !visible.length) return;
      const { minZoom, maxZoom, padding } = FIT_VIEW_OPTIONS;
      return setViewport(
        getViewportForBounds(
          trajectoryOverviewBounds(visible),
          wrapper.clientWidth,
          wrapper.clientHeight,
          minZoom,
          maxZoom,
          padding
        ),
        animate ? viewportMotion() : undefined
      );
    },
    [getNodes, setViewport]
  );
  const fitGraph = useCallback(() => {
    if (searchTargetRef.current)
      return fitView({
        maxZoom: 1,
        minZoom: 0.7,
        nodes: [{ id: searchTargetRef.current }],
        padding: 0.25,
      });
    return fitOverview();
  }, [fitView, fitOverview]);

  const changeQuery = useCallback(
    (value: string) => {
      if (value.trim() && !query.trim()) beforeSearch.current = getViewport();
      if (!value.trim() && beforeSearch.current) {
        setViewport(beforeSearch.current);
        beforeSearch.current = null;
      }
      setQuery(value);
      setMatchIndex(0);
    },
    [query, getViewport, setViewport]
  );
  const toggleBranch = useCallback(
    (id: string) => {
      const isOpen = graph.edges.some(
        (edge) => edge.source === id && edge.data?.relation !== "continuation"
      );
      // Opening a search result commits its path without restoring the pre-search viewport.
      beforeSearch.current = null;
      changeQuery("");
      setCollapsed((current) => {
        const next = new Set(current);
        if (searchTarget)
          for (const ancestor of collectAncestors(fullGraph, id).nodeIds)
            if (ancestor !== id) next.delete(ancestor);
        if (isOpen) next.add(id);
        else next.delete(id);
        return next;
      });
    },
    [graph, fullGraph, searchTarget, changeQuery]
  );
  const [focusId, setFocusId] = useState<string | null>(null);
  const ancestors = useMemo(
    () =>
      searchTarget || focusId ? collectAncestors(fullGraph, (searchTarget || focusId)!) : null,
    [fullGraph, focusId, searchTarget]
  );

  useEffect(() => {
    let cancelled = false;
    setFocusId(null);
    graph.relayout(graph.nodes).then((next) => {
      if (!cancelled) {
        setEdges(graph.edges);
        setNodes(preservePositions(next));
      }
    });
    return () => {
      cancelled = true;
    };
  }, [graph, setNodes, setEdges, preservePositions]);

  // Disclosures change card height without changing graph identity.
  // biome-ignore lint/correctness/useExhaustiveDependencies: measurementKey triggers layout after React Flow measures expanded content.
  useEffect(() => {
    if (!nodesInitialized) return;
    let cancelled = false;
    const measured = new Map(getNodes().map((node) => [node.id, node.measured]));
    if (graph.nodes.some((node) => !measured.get(node.id)?.height)) return;
    graph
      .relayout(
        graph.nodes.map((node) => {
          const handles = getInternalNode(node.id)?.internals.handleBounds;
          const center = (
            handle:
              | { x: number; y: number; width: number; height: number; position: Position }
              | undefined
          ) =>
            handle
              ? {
                  x:
                    handle.x +
                    (handle.position === Position.Left
                      ? 0
                      : handle.position === Position.Right
                        ? handle.width
                        : handle.width / 2),
                  y:
                    handle.y +
                    (handle.position === Position.Top
                      ? 0
                      : handle.position === Position.Bottom
                        ? handle.height
                        : handle.height / 2),
                }
              : undefined;
          return {
            ...node,
            data: {
              ...node.data,
              layoutPorts: {
                actions: center(handles?.source?.find((h) => h.id === "actions")),
                in: center(handles?.target?.find((h) => !h.id)),
                out: center(handles?.source?.find((h) => !h.id)),
                outcomes: center(handles?.source?.find((h) => h.id === "outcomes")),
                retry: center(handles?.target?.find((h) => h.id === "retry")),
                return: center(handles?.target?.find((h) => h.id === "return")),
                returnOut: center(handles?.source?.find((h) => h.id === "return")),
              },
            },
            measured: measured.get(node.id),
          };
        })
      )
      .then((next) => {
        if (cancelled) return;
        next = preservePositions(next);
        setNodes(next);
        onGraphHeight(trajectoryBounds(next).height);
        if (searchTarget && framedSearch.current !== searchTarget) {
          const target = next.find((node) => node.id === searchTarget);
          const wrapper = wrapperRef.current;
          if (target && wrapper) {
            const parents = graph.edges
              .filter((edge) => edge.target === searchTarget)
              .map((edge) => edge.source);
            const contextBounds = trajectoryBounds(
              next
                .filter((node) => node.id === searchTarget || parents.includes(node.id))
                .map((node) =>
                  node.data.kind === "actionGroup"
                    ? { ...node, measured: { ...node.measured, height: 112 } }
                    : node
                )
            );
            // A distant category must not push the searched tool off screen.
            const bounds =
              wrapper.clientWidth < 460 || contextBounds.height * 0.65 > wrapper.clientHeight * 0.7
                ? trajectoryBounds([target])
                : contextBounds;
            setViewport(
              getViewportForBounds(
                { ...bounds, height: bounds.height + 84, y: bounds.y - 84 },
                wrapper.clientWidth,
                wrapper.clientHeight,
                0.65,
                1,
                0.16
              )
            );
            framedSearch.current = searchTarget;
          }
        } else if (
          run.highlighted &&
          displayedRunId &&
          runPath?.mappedSpans &&
          framedRun.current !== displayedRunId &&
          [...runPath.nodeIds].every((id) => next.some((node) => node.id === id))
        ) {
          const wrapper = wrapperRef.current;
          if (wrapper?.clientWidth && wrapper.clientHeight) {
            setViewport(
              getViewportForBounds(
                trajectoryBounds(next.filter((node) => runPath.nodeIds.has(node.id))),
                wrapper.clientWidth,
                wrapper.clientHeight,
                FIT_VIEW_OPTIONS.minZoom,
                1,
                0.15
              ),
              viewportMotion()
            );
            framedRun.current = displayedRunId;
          }
        } else if (!run.highlighted && framedRun.current) {
          fitOverview(next, true);
          framedRun.current = undefined;
        } else if (!hasInteracted.current) {
          requestAnimationFrame(() => fitOverview(next));
        }
      });
    return () => {
      cancelled = true;
    };
  }, [
    nodesInitialized,
    measurementKey,
    graph,
    fitOverview,
    setViewport,
    getNodes,
    getInternalNode,
    setNodes,
    onGraphHeight,
    searchTarget,
    preservePositions,
    run.highlighted,
    displayedRunId,
    runPath,
  ]);

  useEffect(() => {
    if (!searchTarget) framedSearch.current = undefined;
  }, [searchTarget]);

  // Re-fit when the container resizes from hidden/zero-size to visible.
  useEffect(() => {
    const el = wrapperRef.current;
    if (!el) return;
    const observer = new ResizeObserver(() => {
      if (el.clientWidth > 0 && el.clientHeight > 0) fitGraph();
    });
    observer.observe(el);
    return () => observer.disconnect();
  }, [fitGraph]);

  // Reframe after the expand/collapse transition changes the canvas size.
  // biome-ignore lint/correctness/useExhaustiveDependencies: `expanded` is the intended re-fit trigger.
  useEffect(() => {
    const id = requestAnimationFrame(() => fitGraph());
    return () => cancelAnimationFrame(id);
  }, [expanded, fitGraph]);

  const displayNodes = useMemo(
    () =>
      nodes.map((node) => ({
        ...node,
        className: cn(
          "transition-opacity duration-300 motion-reduce:duration-0",
          ancestors && !ancestors.nodeIds.has(node.id) && "opacity-50",
          !ancestors && hasRunPath && !runPath?.nodeIds.has(node.id) && "opacity-40",
          hasRunPath && runPath?.nodeIds.has(node.id) && "rounded-md outline outline-1 outline-info"
        ),
        data: { ...node.data, onToggle: toggleBranch },
        selected: node.id === searchTarget || node.id === focusId,
      })),
    [nodes, ancestors, toggleBranch, searchTarget, focusId, runPath, hasRunPath]
  );

  const displayEdges = useMemo(
    () =>
      edges.map((edge: Edge) => {
        if (hasRunPath && runPath) {
          const recorded =
            runPath.edgeIds.has(edge.id) ||
            (edge.data?.summary && runPath.transitions.has(`${edge.source}->${edge.target}`));
          return {
            ...edge,
            data: { ...edge.data, dimmed: !recorded },
            markerEnd: {
              color: recorded ? "var(--info)" : "var(--muted-foreground)",
              type: MarkerType.ArrowClosed,
            },
            style: {
              ...edge.style,
              stroke: recorded ? "var(--info)" : edge.style?.stroke,
              strokeDasharray: recorded ? undefined : edge.style?.strokeDasharray,
              strokeOpacity: recorded ? 1 : 0.2,
              strokeWidth: recorded ? 2.5 : 1,
            },
          };
        }
        if (!ancestors) return edge;
        if (
          ancestors.edgeIds.has(edge.id) ||
          (edge.data?.relation === "continuation" && ancestors.nodeIds.has(edge.source))
        ) {
          return {
            ...edge,
            style: { ...edge.style, strokeOpacity: 0.9, strokeWidth: 2 },
          };
        }
        return {
          ...edge,
          data: { ...edge.data, dimmed: true },
          style: { ...edge.style, strokeOpacity: 0.25 },
        };
      }),
    [edges, ancestors, runPath, hasRunPath]
  );

  return (
    <div
      className="relative h-full min-h-0"
      onPointerDownCapture={() => {
        hasInteracted.current = true;
      }}
      onWheelCapture={() => {
        hasInteracted.current = true;
      }}
    >
      <TooltipProvider>
        <div
          aria-label="Canvas actions"
          className="absolute right-3 top-3 z-10 flex max-w-[calc(100%-1.5rem)] flex-wrap items-center justify-end gap-0.5 rounded-md border border-border bg-card p-1"
          role="group"
        >
          <Popover onOpenChange={setSearchOpen} open={searchOpen}>
            <Tooltip>
              <TooltipTrigger asChild>
                <PopoverTrigger asChild>
                  <Button
                    aria-label="Search trajectory"
                    aria-pressed={!!query.trim()}
                    className={cn(query.trim() && "bg-accent")}
                    size="icon"
                    variant="ghost"
                  >
                    <Icon.search />
                  </Button>
                </PopoverTrigger>
              </TooltipTrigger>
              <TooltipContent side="bottom" sideOffset={8}>
                Search trajectory
              </TooltipContent>
            </Tooltip>
            <PopoverContent
              align="end"
              aria-label="Trajectory search"
              className="nodrag nopan nowheel w-80 max-w-[calc(100vw-2rem)] space-y-3 p-3"
              onEscapeKeyDown={(event) => event.stopPropagation()}
            >
              <div className="flex items-center gap-2">
                <SearchInput
                  className="min-w-0 flex-1"
                  label="Search trajectory"
                  onChange={(event) => changeQuery(event.target.value)}
                  onClear={() => changeQuery("")}
                  onKeyDown={(event) => {
                    if (event.key === "Enter" && matches.length)
                      setMatchIndex((index) => (index + 1) % matches.length);
                  }}
                  placeholder="Search nodes or conditions…"
                  value={query}
                />
                {matches.length > 1 && (
                  <Button
                    aria-label="Next matching node"
                    onClick={() => setMatchIndex((index) => (index + 1) % matches.length)}
                    size="sm"
                    variant="secondary"
                  >
                    {(matchIndex % matches.length) + 1} / {matches.length}
                    <Icon.chevronRight />
                  </Button>
                )}
              </div>
              {query.trim() && (
                <p className="break-words text-xs text-muted-foreground" role="status">
                  {match ? `Path to ${match.label}` : "No matching nodes"}
                </p>
              )}
            </PopoverContent>
          </Popover>
          {!run.highlighted && (
            <>
              <Tooltip>
                <TooltipTrigger asChild>
                  <Button
                    aria-label="Expand all"
                    onClick={() => {
                      changeQuery("");
                      setSearchOpen(false);
                      setCollapsed(new Set());
                    }}
                    size="icon"
                    variant="ghost"
                  >
                    <Icon.expand />
                  </Button>
                </TooltipTrigger>
                <TooltipContent side="bottom" sideOffset={8}>
                  Expand all paths
                </TooltipContent>
              </Tooltip>
              <Tooltip>
                <TooltipTrigger asChild>
                  <Button
                    aria-label="Collapse all"
                    onClick={() => {
                      changeQuery("");
                      setSearchOpen(false);
                      setCollapsed(
                        new Set(
                          fullGraph.nodes
                            .filter((node) => ["actionGroup", "action"].includes(node.data.kind))
                            .map((node) => node.id)
                        )
                      );
                    }}
                    size="icon"
                    variant="ghost"
                  >
                    <Icon.collapse />
                  </Button>
                </TooltipTrigger>
                <TooltipContent side="bottom" sideOffset={8}>
                  Collapse all paths
                </TooltipContent>
              </Tooltip>
            </>
          )}
          {canvasActions}
        </div>
        {run.traceId && run.highlighted && (
          <div
            aria-live="polite"
            className="pointer-events-none absolute bottom-14 left-3 right-3 z-10 rounded-sm bg-card px-3 py-1.5 text-xs text-muted-foreground sm:bottom-3 sm:left-1/2 sm:right-auto sm:max-w-[calc(100%-15rem)] sm:-translate-x-1/2"
            role="status"
          >
            {run.loading
              ? "Loading recorded route…"
              : run.error
                ? "Route unavailable · open the trace for details"
                : runPath
                  ? hasRunPath
                    ? `${runPath.mappedSpans}/${runPath.totalSpans} spans mapped · recorded steps highlighted`
                    : "No spans match this map · route not captured"
                  : "No captured route"}
          </div>
        )}
        <div
          aria-label="Canvas zoom"
          className="absolute bottom-3 left-3 z-10 flex items-center gap-0.5 rounded-md border border-border bg-card p-1"
          role="group"
        >
          {[
            { action: () => zoomIn(), icon: Icon.add, label: "Zoom in" },
            { action: () => zoomOut(), icon: Icon.subtract, label: "Zoom out" },
            {
              action: () => fitOverview(),
              icon: Icon.aspectRatio,
              label: "Fit view",
            },
          ].map(({ label, icon: Glyph, action }) => (
            <Tooltip key={label}>
              <TooltipTrigger asChild>
                <Button aria-label={label} onClick={action} size="icon" variant="ghost">
                  <Glyph />
                </Button>
              </TooltipTrigger>
              <TooltipContent side="top" sideOffset={8}>
                {label}
              </TooltipContent>
            </Tooltip>
          ))}
        </div>
      </TooltipProvider>
      <div className="flex h-full min-h-0">
        <TrajectoryEdgeBridges>
          <ReactFlow
            aria-label="Capability trajectory map"
            colorMode="dark"
            defaultEdgeOptions={DEFAULT_EDGE_OPTIONS}
            edges={displayEdges}
            edgeTypes={EDGE_TYPES}
            elementsSelectable
            elevateEdgesOnSelect={false}
            elevateNodesOnSelect={false}
            maxZoom={1.4}
            minZoom={FIT_VIEW_OPTIONS.minZoom}
            nodes={displayNodes}
            nodesConnectable={false}
            nodeTypes={trajectoryNodeTypes}
            onEdgesChange={onEdgesChange}
            onInit={() => fitGraph()}
            onNodeClick={(_, node) => {
              changeQuery("");
              if (node.data.kind !== "evaluations")
                setFocusId((prev) => (prev === node.id ? null : node.id));
            }}
            onNodesChange={changeNodes}
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
              color="color-mix(in oklab, var(--border) 60%, transparent)"
              gap={24}
              size={1}
              variant={BackgroundVariant.Dots}
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
        </TrajectoryEdgeBridges>
      </div>
    </div>
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
  const matrix = useEvalMatrixQuery(capability.project, capability.id);
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
      if (
        event.key === "Escape" &&
        !event.defaultPrevented &&
        !document.querySelector('[role="dialog"]')
      )
        onExpandedChange(false);
    };
    window.addEventListener("keydown", handleKey);
    return () => window.removeEventListener("keydown", handleKey);
  }, [expanded, onExpandedChange]);

  if (matrix.isLoading) return <LoadingState label="Loading trajectory evaluations" />;
  if (matrix.error)
    return (
      <DismissibleAlert
        error={new Error(errorMessage(matrix.error, "Couldn't load trajectory evaluations."))}
        variant="destructive"
      />
    );

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
    <section aria-label="Trajectory map" className="space-y-2">
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
        <TrajectoryScoresProvider
          capability={capability.id}
          key={capability.id}
          project={capability.project}
        >
          {(scoreControl, highlighted) => (
            <ReactFlowProvider>
              <FlowCanvas
                canvasActions={
                  <>
                    {scoreControl}
                    {!expanded && (
                      <Tooltip>
                        <TooltipTrigger asChild>
                          <Button
                            aria-label={
                              scrollLocked ? "Unlock canvas scroll" : "Lock canvas scroll"
                            }
                            aria-pressed={!scrollLocked}
                            onClick={() => setScrollLocked(!scrollLocked)}
                            size="icon"
                            variant="ghost"
                          >
                            {scrollLocked ? <Icon.lock /> : <Icon.unlock />}
                          </Button>
                        </TooltipTrigger>
                        <TooltipContent side="bottom" sideOffset={8}>
                          {scrollLocked ? "Unlock canvas scroll" : "Lock canvas scroll"}
                        </TooltipContent>
                      </Tooltip>
                    )}
                    {!highlighted && (
                      <Tooltip>
                        <TooltipTrigger asChild>
                          <Button
                            aria-label={showMinimap ? "Hide minimap" : "Show minimap"}
                            aria-pressed={showMinimap}
                            className={cn(showMinimap && "bg-accent")}
                            onClick={() => setShowMinimap(!showMinimap)}
                            size="icon"
                            variant="ghost"
                          >
                            <Icon.minimap />
                          </Button>
                        </TooltipTrigger>
                        <TooltipContent side="bottom" sideOffset={8}>
                          {showMinimap ? "Hide minimap" : "Show minimap"}
                        </TooltipContent>
                      </Tooltip>
                    )}
                    <Tooltip>
                      <TooltipTrigger asChild>
                        <Button
                          aria-label={expanded ? "Exit full screen" : "Expand to full screen"}
                          aria-pressed={expanded}
                          onClick={() => onExpandedChange(!expanded)}
                          size="icon"
                          variant="ghost"
                        >
                          <Icon.fullscreen />
                        </Button>
                      </TooltipTrigger>
                      <TooltipContent side="bottom" sideOffset={8}>
                        {expanded ? "Exit full screen (Esc)" : "Expand to full screen"}
                      </TooltipContent>
                    </Tooltip>
                  </>
                }
                capability={capability}
                evaluations={matrix.data ?? []}
                expanded={expanded}
                key={capability.id}
                onGraphHeight={setGraphHeight}
                scrollCaptured={scrollCaptured}
                showMinimap={showMinimap && !highlighted}
              />
            </ReactFlowProvider>
          )}
        </TrajectoryScoresProvider>
      </div>
    </section>
  );
}
