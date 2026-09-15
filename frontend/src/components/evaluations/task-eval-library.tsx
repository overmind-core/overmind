import { useMemo, useState } from "react";

import { CapabilityCombobox } from "@/components/capability-combobox";
import { CreateEvaluatorDialog } from "@/components/evaluations/create-evaluator-dialog";
import { EvaluatorDetailDialog } from "@/components/evaluations/evaluator-detail-dialog";
import { EVALUATOR_KIND_LABEL } from "@/components/evaluations/evaluator-kind";
import { Alert } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent } from "@/components/ui/card";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
import { CountChip } from "@/components/ui/count-chip";
import { Icon } from "@/components/ui/icons";
import { ListToolbar } from "@/components/ui/list-toolbar";
import { SearchInput } from "@/components/ui/search-input";
import { Skeleton } from "@/components/ui/skeleton";
import {
  type BehaviourCoverageEntry,
  useBehaviourCoverageQueries,
  useBehaviourEvaluatorsQuery,
  useBehavioursQuery,
} from "@/hooks/use-behaviours";
import { useEvaluatorCatalogQuery, useProjectCapabilitiesQuery } from "@/hooks/use-evaluations";
import { errorMessage } from "@/lib/notify";
import { PROSE, TITLE } from "@/lib/typography";
import { cn } from "@/lib/utils";
import type { Behaviour, BehaviourEvaluator, EvaluatorCatalog } from "@/openapi";
import {
  type BehaviourBinding,
  segmentLabel,
  splitSuite,
  suiteSize,
  unassignedCatalog,
  unscoredStepLabels,
} from "./task-eval-groups";

export function TaskEvalLibrary({
  projectId,
  capabilityFilter,
  onCapabilityFilterChange,
}: {
  projectId: string;
  capabilityFilter: string | undefined;
  onCapabilityFilterChange: (next: string | undefined) => void;
}) {
  const catalogQuery = useEvaluatorCatalogQuery(projectId);
  const capabilitiesQuery = useProjectCapabilitiesQuery(projectId);
  const behavioursQuery = useBehavioursQuery(!!projectId);
  const [search, setSearch] = useState("");
  const [detailId, setDetailId] = useState<string | null>(null);

  const capabilityNameById = useMemo(() => {
    const map = new Map<string, string>();
    for (const a of capabilitiesQuery.data?.results ?? []) map.set(a.id, a.name || a.slug);
    return map;
  }, [capabilitiesQuery.data]);

  // The behaviours list spans every project the user can see.
  const capabilitiesLoaded = !!capabilitiesQuery.data;
  const behaviours = useMemo(
    () =>
      capabilitiesLoaded
        ? (behavioursQuery.data?.results ?? []).filter((b) => capabilityNameById.has(b.capability))
        : [],
    [behavioursQuery.data, capabilityNameById, capabilitiesLoaded]
  );

  const behaviourCapabilityIds = useMemo(
    () => [...new Set(behaviours.map((b) => b.capability))].sort(),
    [behaviours]
  );
  const coverageQueries = useBehaviourCoverageQueries(behaviourCapabilityIds);
  const coverageSettled = coverageQueries.every((q) => !q.isLoading);
  const entriesByCapability = useMemo(() => {
    const map = new Map<string, BehaviourCoverageEntry[]>();
    behaviourCapabilityIds.forEach((capabilityId, i) => {
      map.set(capabilityId, coverageQueries[i]?.data?.behaviours ?? []);
    });
    return map;
  }, [behaviourCapabilityIds, coverageQueries]);
  const coverageByBehaviourId = useMemo(() => {
    const map = new Map<string, BehaviourCoverageEntry>();
    for (const entries of entriesByCapability.values()) {
      for (const entry of entries) map.set(entry.behaviourId, entry);
    }
    return map;
  }, [entriesByCapability]);

  const catalog = useMemo(() => catalogQuery.data ?? [], [catalogQuery.data]);

  const capabilityOptions = useMemo(() => {
    const byId = new Map<string, string>();
    for (const ev of catalog) {
      if (ev.capability && ev.capabilityName) byId.set(ev.capability, ev.capabilityName);
    }
    for (const b of behaviours) {
      const name = capabilityNameById.get(b.capability);
      if (name) byId.set(b.capability, name);
    }
    return [...byId.entries()].sort((a, b) => a[1].localeCompare(b[1]));
  }, [catalog, behaviours, capabilityNameById]);

  const q = search.trim().toLowerCase();

  const visibleBehaviours = useMemo(
    () =>
      behaviours.filter((b) => {
        if (capabilityFilter === "generic") return false;
        if (capabilityFilter && capabilityFilter !== "all" && b.capability !== capabilityFilter)
          return false;
        if (!q) return true;
        return `${b.displayName ?? ""} ${b.key} ${b.entryAnchor ?? ""}`.toLowerCase().includes(q);
      }),
    [behaviours, capabilityFilter, q]
  );

  const unassigned = useMemo(
    () => (coverageSettled ? unassignedCatalog(catalog, entriesByCapability) : []),
    [catalog, entriesByCapability, coverageSettled]
  );
  const visibleUnassigned = useMemo(
    () =>
      unassigned.filter((ev) => {
        if (capabilityFilter === "generic" && ev.capability) return false;
        if (
          capabilityFilter &&
          capabilityFilter !== "all" &&
          capabilityFilter !== "generic" &&
          ev.capability !== capabilityFilter
        )
          return false;
        if (
          q &&
          !`${ev.displayName ?? ""} ${ev.name} ${ev.description ?? ""}`.toLowerCase().includes(q)
        )
          return false;
        return true;
      }),
    [unassigned, capabilityFilter, q]
  );

  const selectValue = capabilityFilter ?? "all";
  const hasActiveFilters = q !== "" || (!!capabilityFilter && capabilityFilter !== "all");
  const isLoading =
    catalogQuery.isLoading || behavioursQuery.isLoading || !capabilitiesLoaded || !coverageSettled;
  const loadError = behavioursQuery.error ?? catalogQuery.error;
  const showOwner = !capabilityFilter || capabilityFilter === "all";

  const handleClearFilters = () => {
    setSearch("");
    onCapabilityFilterChange(undefined);
  };

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-3">
      <ListToolbar
        actions={<CountChip count={visibleBehaviours.length + visibleUnassigned.length} />}
        filters={
          <CapabilityCombobox
            ariaLabel="Filter by capability"
            className="w-56"
            onChange={(v) => onCapabilityFilterChange(v === "all" ? undefined : v)}
            options={[
              { label: "All evaluations", value: "all" },
              { label: "Generic only", value: "generic" },
              ...capabilityOptions.map(([id, name]) => ({ label: name, value: id })),
            ]}
            placeholder="All capabilities"
            value={selectValue}
          />
        }
        hasActiveFilters={hasActiveFilters}
        onClearFilters={handleClearFilters}
        primary={<CreateEvaluatorDialog projectId={projectId} />}
        search={
          <SearchInput
            className="min-w-48 flex-1"
            label="Search tasks and evaluations"
            onChange={(e) => setSearch(e.target.value)}
            onClear={() => setSearch("")}
            placeholder="Search tasks and evaluations…"
            value={search}
          />
        }
      />

      {isLoading ? (
        <div className="space-y-2">
          {Array.from({ length: 5 }).map((_, i) => (
            <Skeleton className="h-14 w-full" key={i} />
          ))}
        </div>
      ) : loadError && behaviours.length === 0 && catalog.length === 0 ? (
        <Alert variant="destructive">
          {errorMessage(loadError, "Couldn't load the evaluation library.")}
        </Alert>
      ) : visibleBehaviours.length === 0 && visibleUnassigned.length === 0 ? (
        <Card>
          <CardContent className="flex flex-col items-center justify-center gap-2 py-16 text-center">
            <Icon.checkbox className="size-7 text-muted-foreground" />
            <p className="text-sm font-medium">
              {hasActiveFilters ? "No tasks or evaluations match your filters" : "No tasks yet"}
            </p>
            <p className={cn(PROSE, "max-w-sm text-sm text-muted-foreground")}>
              Tasks mint when an agent's repository is scanned; each carries the evaluators that
              score it. Evaluators without a task appear under Unassigned.
            </p>
          </CardContent>
        </Card>
      ) : (
        <div className="flex flex-col gap-3">
          {visibleBehaviours.map((behaviour) => (
            <BehaviourTaskCard
              behaviour={behaviour}
              capabilityName={capabilityNameById.get(behaviour.capability)}
              coverage={coverageByBehaviourId.get(behaviour.id)}
              key={behaviour.id}
              onOpenEvaluator={setDetailId}
              showOwner={showOwner}
            />
          ))}

          {visibleUnassigned.length > 0 ? (
            <UnassignedGroup
              evaluators={visibleUnassigned}
              onOpenEvaluator={setDetailId}
              showOwner={showOwner}
            />
          ) : null}
        </div>
      )}

      <EvaluatorDetailDialog
        evaluatorId={detailId ?? undefined}
        onOpenChange={(open) => !open && setDetailId(null)}
        open={!!detailId}
      />
    </div>
  );
}

function coverageLine(coverage: BehaviourCoverageEntry): string {
  const total = coverage.steps.length;
  const unscored = unscoredStepLabels(coverage);
  const parts: string[] = [];
  if (total > 0) {
    parts.push(`${total - unscored.length}/${total} steps scored`);
  }
  parts.push(coverage.outcomeCovered ? "outcome scored" : "outcome unscored");
  if (unscored.length > 0) {
    parts.push(`unscored: ${unscored.join(", ")}`);
  }
  return parts.join(" · ");
}

function BehaviourTaskCard({
  behaviour,
  coverage,
  capabilityName,
  showOwner,
  onOpenEvaluator,
}: {
  behaviour: Behaviour;
  coverage: BehaviourCoverageEntry | undefined;
  capabilityName: string | undefined;
  showOwner: boolean;
  onOpenEvaluator: (id: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const evaluatorsQuery = useBehaviourEvaluatorsQuery(behaviour.id, open);

  const name = behaviour.displayName || behaviour.key;
  const evalCount = coverage ? suiteSize(coverage) : null;
  const passRate =
    behaviour.executionCount > 0 && behaviour.avgSuccessScore != null
      ? Math.round(behaviour.avgSuccessScore * 100)
      : null;

  return (
    <Card className="overflow-hidden p-0">
      <Collapsible onOpenChange={setOpen} open={open}>
        <CollapsibleTrigger asChild>
          <button
            className="flex w-full items-center gap-3 px-4 py-3 text-left transition-colors hover:bg-wash-subtle focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring"
            type="button"
          >
            <Icon.chevronRight
              aria-hidden
              className={cn(
                "size-4 shrink-0 text-muted-foreground transition-transform",
                open && "rotate-90"
              )}
            />
            <div className="min-w-0 flex-1">
              <div className="flex min-w-0 flex-wrap items-center gap-2">
                <span className="truncate text-sm font-medium">{name}</span>
                {evalCount != null ? <CountChip count={evalCount} /> : null}
                {showOwner && capabilityName ? (
                  <Badge size="chip" variant="secondary">
                    {capabilityName}
                  </Badge>
                ) : null}
                {behaviour.status === "retired" ? (
                  <Badge size="chip" variant="outline">
                    Retired
                  </Badge>
                ) : null}
              </div>
              <p className="truncate text-xs text-muted-foreground">
                {coverage ? coverageLine(coverage) : "No coverage data"}
              </p>
            </div>
            <div className="shrink-0 text-right">
              {passRate != null ? (
                <>
                  <span className="font-mono text-sm tabular-nums">{passRate}%</span>
                  <p className="text-xs text-muted-foreground">
                    {behaviour.executionCount} executions
                  </p>
                </>
              ) : (
                <span className="text-xs text-muted-foreground">
                  {behaviour.executionCount > 0
                    ? `${behaviour.executionCount} executions`
                    : "No executions"}
                </span>
              )}
            </div>
          </button>
        </CollapsibleTrigger>
        <CollapsibleContent>
          <div className="space-y-4 border-t border-border/70 px-4 py-4">
            {evaluatorsQuery.isLoading ? (
              <div className="space-y-2">
                <Skeleton className="h-20 w-full" />
                <Skeleton className="h-20 w-full" />
              </div>
            ) : evaluatorsQuery.error ? (
              <Alert variant="destructive">
                {errorMessage(evaluatorsQuery.error, "Couldn't load this task's evaluators.")}
              </Alert>
            ) : (
              <TaskSuite
                coverage={coverage}
                evaluators={evaluatorsQuery.data ?? []}
                onOpenEvaluator={onOpenEvaluator}
              />
            )}
          </div>
        </CollapsibleContent>
      </Collapsible>
    </Card>
  );
}

function TaskSuite({
  evaluators,
  coverage,
  onOpenEvaluator,
}: {
  evaluators: BehaviourEvaluator[];
  coverage: BehaviourCoverageEntry | undefined;
  onOpenEvaluator: (id: string) => void;
}) {
  const { step, outcome } = splitSuite(evaluators);
  const unscored = coverage ? unscoredStepLabels(coverage) : [];

  if (evaluators.length === 0) {
    return (
      <p className={cn(PROSE, "py-2 text-center text-xs text-muted-foreground")}>
        No evaluators bound to this task.
      </p>
    );
  }

  return (
    <>
      <section aria-label="Step evals">
        <div className="flex items-center gap-2">
          <p className="text-sm font-medium">Step evals</p>
          <CountChip count={step.length} />
        </div>
        {step.length === 0 ? (
          <p className="mt-2 text-xs text-muted-foreground">No step evals.</p>
        ) : (
          <div className="mt-2 grid grid-cols-1 gap-3 md:grid-cols-2 lg:grid-cols-3">
            {step.map((ev) => (
              <SuiteEvaluatorCard
                evaluator={ev}
                key={ev.id}
                onClick={() => onOpenEvaluator(ev.id)}
                tag={segmentLabel((ev.behaviourBinding as BehaviourBinding).anchorSegment ?? [])}
              />
            ))}
          </div>
        )}
        {unscored.length > 0 ? (
          <p className="mt-2 text-xs text-muted-foreground">
            Unscored steps: {unscored.join(", ")}
          </p>
        ) : null}
      </section>
      <section aria-label="Outcome evals">
        <div className="flex items-center gap-2">
          <p className="text-sm font-medium">Outcome evals</p>
          <CountChip count={outcome.length} />
        </div>
        {outcome.length === 0 ? (
          <p className="mt-2 text-xs text-muted-foreground">No outcome evals.</p>
        ) : (
          <div className="mt-2 grid grid-cols-1 gap-3 md:grid-cols-2 lg:grid-cols-3">
            {outcome.map((ev) => (
              <SuiteEvaluatorCard
                evaluator={ev}
                key={ev.id}
                onClick={() => onOpenEvaluator(ev.id)}
                tag="Outcome"
              />
            ))}
          </div>
        )}
      </section>
    </>
  );
}

function SuiteEvaluatorCard({
  evaluator,
  tag,
  onClick,
}: {
  evaluator: BehaviourEvaluator;
  tag: string;
  onClick: () => void;
}) {
  const kind = EVALUATOR_KIND_LABEL[evaluator.kind] ?? evaluator.kind;
  return (
    <button className="min-w-0 text-left" onClick={onClick} type="button">
      <Card className="h-full min-w-0 cursor-pointer p-3 transition-colors hover:border-primary/40 hover:bg-wash-subtle">
        <div className="flex min-w-0 flex-col gap-1.5">
          <p className={cn(TITLE.card, "min-w-0 break-words")} title={evaluator.name}>
            {evaluator.displayName?.trim() || evaluator.name}
          </p>
          {evaluator.description ? (
            <p className={cn(PROSE, "line-clamp-2 text-xs text-muted-foreground")}>
              {evaluator.description}
            </p>
          ) : null}
          <div className="mt-1 border-t border-border/70 pt-2">
            <p className="truncate text-xs font-semibold text-muted-foreground">
              {kind} · {tag}
            </p>
          </div>
        </div>
      </Card>
    </button>
  );
}

function UnassignedGroup({
  evaluators,
  showOwner,
  onOpenEvaluator,
}: {
  evaluators: EvaluatorCatalog[];
  showOwner: boolean;
  onOpenEvaluator: (id: string) => void;
}) {
  return (
    <Card className="overflow-hidden p-0">
      <div className="flex flex-wrap items-center gap-2 border-b border-border/70 bg-wash-subtle px-4 py-2.5">
        <span className="text-sm font-medium">Unassigned</span>
        <CountChip count={evaluators.length} />
        <span className="text-xs text-muted-foreground">Not bound to any task.</span>
      </div>
      <div className="grid grid-cols-1 gap-4 p-4 md:grid-cols-2 lg:grid-cols-3">
        {evaluators.map((ev) => (
          <EvaluatorCatalogCard
            evaluator={ev}
            key={ev.id}
            onClick={() => onOpenEvaluator(ev.id)}
            showOwner={showOwner}
          />
        ))}
      </div>
    </Card>
  );
}

function EvaluatorCatalogCard({
  evaluator,
  onClick,
  showOwner,
}: {
  evaluator: EvaluatorCatalog;
  onClick: () => void;
  showOwner: boolean;
}) {
  const kind = EVALUATOR_KIND_LABEL[evaluator.kind] ?? evaluator.kind;
  const scope = evaluator.scope.replace(/_/g, " ");
  const owner = evaluator.isGeneric ? "Generic" : evaluator.capabilityName || "Capability";
  const meta = showOwner ? `${kind} · ${scope} · ${owner}` : `${kind} · ${scope}`;

  return (
    <button className="min-w-0 text-left" onClick={onClick} type="button">
      <Card className="h-full min-w-0 cursor-pointer p-4 transition-colors hover:border-primary/40 hover:bg-wash-subtle">
        <div className="flex min-w-0 flex-col gap-2">
          <p className={cn(TITLE.card, "min-w-0 break-words")} title={evaluator.name}>
            {evaluator.displayName?.trim() || evaluator.name}
          </p>
          {evaluator.description ? (
            <p className={cn(PROSE, "line-clamp-2 text-xs text-muted-foreground")}>
              {evaluator.description}
            </p>
          ) : (
            <p className="text-xs italic text-muted-foreground/60">No description</p>
          )}
          <div className="mt-1 border-t border-border/70 pt-2">
            <p className="text-xs font-semibold text-muted-foreground">{meta}</p>
          </div>
        </div>
      </Card>
    </button>
  );
}
