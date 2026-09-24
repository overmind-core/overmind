import { useMemo, useState } from "react";

import { createFileRoute, redirect } from "@tanstack/react-router";
import { toast } from "sonner";

import { CreateEvalSetDialog } from "@/components/evaluations/create-eval-set-dialog";
import { CreateRunDialog } from "@/components/evaluations/create-run-dialog";
import { EvalSetDetailDialog } from "@/components/evaluations/eval-set-detail-dialog";
import { RunsTable } from "@/components/evaluations/runs-table";
import { TaskEvalLibrary } from "@/components/evaluations/task-eval-library";
import { ProjectRequiredEmptyState } from "@/components/project-required-empty-state";
import { Alert } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { CountChip } from "@/components/ui/count-chip";
import { EmptyState } from "@/components/ui/empty-state";
import { Icon } from "@/components/ui/icons";
import { PageHeader } from "@/components/ui/page-header";
import { PageShell } from "@/components/ui/page-shell";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import {
  useDeleteEvalSetMutation,
  useEvalSetsQuery,
  useEvaluatorScoreHistoryQuery,
  useProjectCapabilitiesQuery,
} from "@/hooks/use-evaluations";
import { featureFlags } from "@/lib/feature-flags";
import { errorMessage, notify } from "@/lib/notify";
import { type EvaluationsSearch, evaluationsSearchSchema } from "@/lib/schemas";
import type { EvalSet } from "@/openapi";

export const Route = createFileRoute("/_auth/evaluations/")({
  beforeLoad: () => {
    if (!featureFlags.evaluations) throw redirect({ replace: true, to: "/" });
  },
  component: EvaluationsPage,
  validateSearch: evaluationsSearchSchema,
});

function EvaluationsPage() {
  const search = Route.useSearch();
  const { projectId, view, capability, page, page_size: pageSize } = search;
  const navigate = Route.useNavigate();

  const patchSearch = (updates: Partial<EvaluationsSearch>) =>
    navigate({
      replace: true,
      resetScroll: false,
      search: (prev) => ({ ...prev, ...updates }),
    });

  if (!projectId) {
    return (
      <PageShell
        header={
          <PageHeader
            description="Score capabilities against datasets and graders."
            icon={
              <Icon.evaluations
                aria-hidden
                className="size-6 shrink-0 [image-rendering:pixelated] dark:invert"
              />
            }
            title="Evaluations"
          />
        }
        variant="full"
      >
        <ProjectRequiredEmptyState />
      </PageShell>
    );
  }

  const activeView = view ?? "runs";
  const setView = (next: string) =>
    navigate({
      replace: true,
      resetScroll: false,
      search: (prev) => ({
        ...prev,
        capability: next === "library" ? prev.capability : undefined,
        view: next as "sets" | "runs" | "library",
      }),
    });
  const setCapabilityFilter = (next: string | undefined) =>
    navigate({
      replace: true,
      resetScroll: false,
      search: (prev) => ({ ...prev, capability: next }),
    });

  return (
    <PageShell
      header={
        <PageHeader
          actions={
            <div className="flex flex-wrap items-center gap-3">
              {activeView === "runs" && <CreateRunDialog key={projectId} projectId={projectId} />}
              <Tabs className="w-auto shrink-0" onValueChange={setView} value={activeView}>
                <TabsList>
                  <TabsTrigger value="runs">Runs</TabsTrigger>
                  <TabsTrigger value="sets">Eval sets</TabsTrigger>
                  <TabsTrigger value="library">Eval library</TabsTrigger>
                </TabsList>
              </Tabs>
            </div>
          }
          description="Score capabilities against datasets and graders."
          icon={
            <Icon.evaluations
              aria-hidden
              className="size-6 shrink-0 [image-rendering:pixelated] dark:invert"
            />
          }
          title="Evaluations"
        />
      }
      variant="full"
    >
      <div className="flex min-h-0 flex-1 flex-col">
        {activeView === "sets" && (
          <div className="flex flex-col gap-3">
            <div>
              <CreateEvalSetDialog key={projectId} projectId={projectId} />
            </div>
            <EvalSetsTab projectId={projectId} />
          </div>
        )}
        {activeView === "runs" && (
          <RunsTable
            filters={search}
            onSearchChange={patchSearch}
            page={page}
            pageSize={pageSize}
            projectId={projectId}
          />
        )}
        {activeView === "library" && (
          <TaskEvalLibrary
            capabilityFilter={capability}
            onCapabilityFilterChange={setCapabilityFilter}
            projectId={projectId}
          />
        )}
      </div>
    </PageShell>
  );
}

function setLatestScore(set: EvalSet, latestByName: Map<string, number>): number | null {
  const scores = set.members
    .map((m) => latestByName.get(m.evaluatorName))
    .filter((s): s is number => s != null);
  if (scores.length === 0) return null;
  return scores.reduce((a, b) => a + b, 0) / scores.length;
}

function EvalSetsTab({ projectId }: { projectId: string }) {
  const setsQuery = useEvalSetsQuery(projectId);
  const capabilitiesQuery = useProjectCapabilitiesQuery(projectId);

  const capabilityNameById = useMemo(() => {
    const map = new Map<string, string>();
    for (const a of capabilitiesQuery.data?.results ?? []) map.set(a.id, a.name || a.slug);
    return map;
  }, [capabilitiesQuery.data]);

  // Sets whose capability is gone (archived/soft-deleted) would 404 on "Manage sets";
  // gate on the capabilities query so a slow load doesn't hide everything meanwhile.
  const capabilitiesLoaded = !!capabilitiesQuery.data;
  const groups = useMemo(() => {
    const sets = (setsQuery.data?.results ?? []).filter(
      (s) =>
        s.project === projectId &&
        (!s.capability || !capabilitiesLoaded || capabilityNameById.has(s.capability))
    );
    const byCapability = new Map<string, EvalSet[]>();
    for (const set of sets) {
      const list = byCapability.get(set.capability ?? "") ?? [];
      list.push(set);
      byCapability.set(set.capability ?? "", list);
    }
    return [...byCapability.entries()].sort((a, b) =>
      (capabilityNameById.get(a[0]) ?? a[0]).localeCompare(capabilityNameById.get(b[0]) ?? b[0])
    );
  }, [setsQuery.data, projectId, capabilityNameById, capabilitiesLoaded]);

  if (setsQuery.isLoading) {
    return (
      <div className="space-y-2">
        {Array.from({ length: 5 }).map((_, i) => (
          <Skeleton className="h-12 w-full" key={i} />
        ))}
      </div>
    );
  }

  if (setsQuery.error && groups.length === 0) {
    return (
      <Alert variant="destructive">
        {errorMessage(setsQuery.error, "Couldn't load eval sets.")}
      </Alert>
    );
  }

  if (groups.length === 0) {
    return (
      <Card>
        <CardContent className="py-4">
          <EmptyState
            description="Create an eval set from the evaluator library."
            icon={Icon.evaluations}
            iconClassName="dark:invert [image-rendering:pixelated]"
            size="section"
            title="No eval sets yet"
          />
        </CardContent>
      </Card>
    );
  }

  return (
    <div className="flex flex-col gap-4">
      {groups.map(([capabilityId, sets]) => (
        <CapabilitySetsGroup
          capabilityId={capabilityId}
          capabilityName={
            capabilityId
              ? (capabilityNameById.get(capabilityId) ?? "Unknown capability")
              : "No capability"
          }
          key={capabilityId}
          projectId={projectId}
          sets={sets}
        />
      ))}
    </div>
  );
}

function CapabilitySetsGroup({
  capabilityId,
  capabilityName,
  sets,
  projectId,
}: {
  capabilityId: string;
  capabilityName: string;
  sets: EvalSet[];
  projectId: string;
}) {
  const navigate = Route.useNavigate();
  const [selectedSet, setSelectedSet] = useState<EvalSet | null>(null);
  const { data: scoreHistory } = useEvaluatorScoreHistoryQuery(capabilityId);
  const deleteSet = useDeleteEvalSetMutation(projectId, capabilityId);

  const handleDeleteSet = async (set: EvalSet) => {
    try {
      await deleteSet.mutateAsync(set.id);
      toast.success(`Deleted "${set.name}"`);
    } catch (e) {
      notify.error(e, "Failed to delete eval set");
    }
  };

  const latestByName = useMemo(() => {
    const map = new Map<string, number>();
    for (const series of scoreHistory ?? []) {
      const last = series.points.at(-1);
      if (last) map.set(series.name, last.score);
    }
    return map;
  }, [scoreHistory]);

  const openCapabilityEvaluations = () =>
    navigate({
      params: { capabilityId },
      search: { projectId, tab: "evaluators" },
      to: "/capabilities/$capabilityId",
    });

  return (
    <Card className="overflow-hidden p-0">
      <div className="flex items-center justify-between gap-2 border-b bg-wash-subtle px-4 py-2.5">
        <div className="flex items-center gap-2">
          <Icon.capability className="size-4 text-muted-foreground" />
          <span className="text-sm font-medium">{capabilityName}</span>
          <CountChip count={sets.length} />
        </div>
        {capabilityId && (
          <Button onClick={openCapabilityEvaluations} size="sm" variant="secondary">
            <Icon.externalLink />
            Manage sets
          </Button>
        )}
      </div>
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Set</TableHead>
            <TableHead className="text-center">Generative</TableHead>
            <TableHead className="text-center">Trace scoring</TableHead>
            <TableHead className="text-center">Status</TableHead>
            <TableHead className="text-center">Latest score</TableHead>
            <TableHead className="w-16 text-center">Actions</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {sets.map((set) => {
            const score = setLatestScore(set, latestByName);
            return (
              <TableRow
                aria-label={`Open ${set.name} for ${capabilityName}`}
                className="cursor-pointer"
                key={set.id}
                onClick={() => setSelectedSet(set)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" || e.key === " ") {
                    e.preventDefault();
                    setSelectedSet(set);
                  }
                }}
                role="button"
                tabIndex={0}
              >
                <TableCell className="font-mono text-sm">{set.name}</TableCell>
                <TableCell className="text-center font-mono text-xs tabular-nums">
                  {set.generativeCount}
                </TableCell>
                <TableCell className="text-center font-mono text-xs tabular-nums">
                  {set.traceScoringCount}
                </TableCell>
                <TableCell className="text-center">
                  <div className="flex flex-wrap justify-center gap-1.5">
                    {set.isActive ? (
                      <Badge size="chip" variant="default">
                        Active
                      </Badge>
                    ) : null}
                  </div>
                </TableCell>
                <TableCell className="text-center">
                  {score === null ? (
                    <span className="text-xs text-muted-foreground">No runs yet</span>
                  ) : (
                    <span className="font-mono text-sm tabular-nums">{Math.round(score)}%</span>
                  )}
                </TableCell>
                <TableCell className="text-center">
                  {/* Stops dialog clicks bubbling to the row's navigate handler. */}
                  {/* biome-ignore lint/a11y/useKeyWithClickEvents: guard only, not interactive */}
                  <span className="inline-flex" onClick={(e) => e.stopPropagation()}>
                    <ConfirmDialog
                      confirmLabel="Delete set"
                      description={
                        <>
                          This permanently removes the set and its members.
                          {set.isActive
                            ? " This is the capability's active set. Another set becomes active in its place, or none if this is the last one."
                            : ""}
                        </>
                      }
                      destructive
                      isPending={deleteSet.isPending}
                      onConfirm={() => handleDeleteSet(set)}
                      title={`Delete "${set.name}"?`}
                      trigger={
                        <Button
                          aria-label={`Delete ${set.name}`}
                          className="text-muted-foreground hover:text-destructive"
                          onClick={(e) => e.stopPropagation()}
                          size="icon-sm"
                          variant="ghost"
                        >
                          <Icon.delete />
                        </Button>
                      }
                    />
                  </span>
                </TableCell>
              </TableRow>
            );
          })}
        </TableBody>
      </Table>
      <EvalSetDetailDialog onClose={() => setSelectedSet(null)} set={selectedSet} />
    </Card>
  );
}
