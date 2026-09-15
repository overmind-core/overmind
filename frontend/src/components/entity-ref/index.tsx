import type { ComponentType, MouseEvent, ReactNode } from "react";

import { Link } from "@tanstack/react-router";

import {
  CapabilityCard,
  DatasetCard,
  EvalRunCard,
  ExperimentCard,
  JobCard,
  ModelCard,
  ProjectCard,
  TraceCard,
} from "@/components/entity-ref/cards";
import { ENTITY_META, type EntityKind } from "@/components/entity-ref/shell";
import { HoverCard, HoverCardContent, HoverCardTrigger } from "@/components/ui/hover-card";
import { cn } from "@/lib/utils";

export type { EntityKind } from "@/components/entity-ref/shell";

const CHIP =
  "inline-flex max-w-full min-w-0 items-center gap-1.5 rounded-sm bg-control px-1.5 py-0.5 text-sm text-foreground align-middle transition-colors duration-150 hover:bg-control-hover outline-none focus-visible:ring-[2px] focus-visible:ring-ring/60";

/** TanStack types `to` against `params`, so each kind needs its own literal
 * branch — a generic route table would need a cast. */
export function EntityLink({
  children,
  className,
  id,
  kind,
  onClick,
  projectId,
  ...rest
}: {
  children: ReactNode;
  className: string;
  id: string;
  kind: EntityKind;
  onClick: (e: MouseEvent) => void;
  projectId?: string;
} & Record<string, unknown>) {
  const search = projectId ? { projectId } : undefined;
  switch (kind) {
    case "capability":
      return (
        <Link
          className={className}
          onClick={onClick}
          params={{ capabilityId: id }}
          to="/capabilities/$capabilityId"
          {...rest}
        >
          {children}
        </Link>
      );
    case "dataset":
      return (
        <Link
          className={className}
          onClick={onClick}
          params={{ datasetId: id }}
          search={search}
          to="/datasets/$datasetId"
          {...rest}
        >
          {children}
        </Link>
      );
    case "evalRun":
      return (
        <Link
          className={className}
          onClick={onClick}
          params={{ runId: id }}
          to="/evaluations/runs/$runId"
          {...rest}
        >
          {children}
        </Link>
      );
    case "experiment":
      return (
        <Link
          className={className}
          onClick={onClick}
          params={{ experimentId: id }}
          to="/optimiser/$experimentId"
          {...rest}
        >
          {children}
        </Link>
      );
    case "model":
      return (
        <Link
          className={className}
          onClick={onClick}
          params={{ modelId: id }}
          to="/inference/$modelId"
          {...rest}
        >
          {children}
        </Link>
      );
    case "project":
      return (
        <Link
          className={className}
          onClick={onClick}
          params={{ projectId: id }}
          to="/projects/$projectId"
          {...rest}
        >
          {children}
        </Link>
      );
    case "trace":
      return (
        <Link
          className={className}
          onClick={onClick}
          params={{ traceId: id }}
          to="/observability/$traceId"
          {...rest}
        >
          {children}
        </Link>
      );
    // A fine-tuning job's detail is a drawer over /training, not a route of its
    // own, so `job` opens it from the URL.
    case "job":
      return (
        <Link
          className={className}
          onClick={onClick}
          search={(prev: Record<string, unknown>) => ({
            ...prev,
            job: id,
            ...(projectId ? { projectId } : {}),
          })}
          to="/training"
          {...rest}
        >
          {children}
        </Link>
      );
  }
}

const CARDS: Record<EntityKind, ComponentType<{ id: string; name: string }>> = {
  capability: CapabilityCard,
  dataset: DatasetCard,
  evalRun: EvalRunCard,
  experiment: ExperimentCard,
  job: JobCard,
  model: ModelCard,
  project: ProjectCard,
  trace: TraceCard,
};

export function EntityRef({
  className,
  id,
  kind,
  name,
  projectId,
  showIcon = true,
}: {
  className?: string;
  id: string;
  kind: EntityKind;
  name?: string | null;
  projectId?: string;
  showIcon?: boolean;
}) {
  const meta = ENTITY_META[kind];
  const Card = CARDS[kind];
  const label = name?.trim() || `${id.slice(0, 8)}…`;

  return (
    <HoverCard>
      <HoverCardTrigger asChild>
        <EntityLink
          className={cn(CHIP, className)}
          id={id}
          kind={kind}
          onClick={(e) => e.stopPropagation()}
          projectId={projectId}
        >
          {showIcon ? (
            <meta.icon aria-hidden className="size-3.5 shrink-0 text-foreground" />
          ) : null}
          <span className="truncate">{label}</span>
        </EntityLink>
      </HoverCardTrigger>
      {/* Mounted only while open, so each card's query and its polling live exactly
          as long as the card. No Suspense boundary — plain `useQuery` never suspends,
          so one would silently never fire. */}
      <HoverCardContent className="p-0">
        <Card id={id} name={label} />
      </HoverCardContent>
    </HoverCard>
  );
}
