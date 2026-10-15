import { BaseEdge, EdgeLabelRenderer, type EdgeProps, useInternalNode } from "@xyflow/react";

import { Button } from "@/components/ui/button";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
import { Icon } from "@/components/ui/icons";
import { cn } from "@/lib/utils";
import type { TrajectoryRoute } from "./buildTrajectoryGraph";
import { useTrajectoryBridges } from "./TrajectoryEdgeBridges";
import { trajectoryEdgeGeometry } from "./trajectoryEdgeGeometry";

export function TrajectoryCondition({
  label,
  x,
  y,
  basis,
  dimmed,
  continuation = false,
}: {
  label: string;
  x: number;
  y: number;
  basis?: unknown;
  dimmed?: unknown;
  continuation?: boolean;
}) {
  const title = continuation ? "Continue" : "When";
  const Glyph = continuation ? Icon.refresh : basis === "prompt" ? Icon.docs : Icon.gitBranch;
  return (
    <div
      className="pointer-events-auto absolute z-10 nodrag nopan"
      style={{
        opacity: dimmed ? 0.45 : 1,
        transform: `translate(-50%, -50%) translate(${x}px, ${y}px)`,
      }}
    >
      <Collapsible className="group/condition min-w-24 rounded-sm border border-border bg-card">
        <CollapsibleTrigger asChild>
          <Button
            aria-label={`Show condition: ${label}`}
            className="w-full justify-between"
            onClick={(event) => event.stopPropagation()}
            size="xs"
            variant="secondary"
          >
            <Glyph className={cn("size-3", basis === "prompt" ? "text-cat-6" : "text-cat-1")} />
            {title}
            <Icon.chevronDown className="size-3 text-muted-foreground group-data-[state=open]/condition:rotate-180" />
          </Button>
        </CollapsibleTrigger>
        <CollapsibleContent className="w-44 px-3 py-2">
          <p className="max-h-64 overflow-y-auto whitespace-pre-wrap break-words text-xs leading-relaxed">
            {label}
          </p>
        </CollapsibleContent>
      </Collapsible>
    </div>
  );
}

export function TrajectoryConnector(props: EdgeProps) {
  const continuation = props.type === "continuation";
  const showCondition = continuation ? !props.data?.groupId && !props.data?.summary : !!props.label;
  const source = useInternalNode(props.source);
  const route = (source?.data.routes as Record<string, TrajectoryRoute> | undefined)?.[props.id];
  const geometry = trajectoryEdgeGeometry(props, route);
  const { path, bridges } = useTrajectoryBridges({
    id: props.id,
    path: geometry.path,
    source: props.source,
    target: props.target,
  });
  return (
    <>
      <g>
        {continuation && <title>{String(props.label || "Tool results return")}</title>}
        {bridges.map((bridge) => (
          <path
            className="pointer-events-none"
            d={bridge}
            fill="none"
            key={bridge}
            stroke="var(--background)"
            strokeWidth={5}
          />
        ))}
        <BaseEdge id={props.id} markerEnd={props.markerEnd} path={path} style={props.style} />
      </g>
      {showCondition && (
        <EdgeLabelRenderer>
          <TrajectoryCondition
            basis={props.data?.basis}
            continuation={continuation}
            dimmed={props.data?.dimmed}
            label={String(props.label || "Results")}
            x={geometry.label.x}
            y={geometry.label.y}
          />
        </EdgeLabelRenderer>
      )}
    </>
  );
}
