import { useEffect, useMemo, useState } from "react";

import { Link } from "@tanstack/react-router";
import { toast } from "sonner";

import { EvaluatorDetailDialog } from "@/components/evaluations/evaluator-detail-dialog";
import { EVALUATOR_KIND_LABEL } from "@/components/evaluations/evaluator-kind";
import { RunLocallyDialog } from "@/components/optimiser/run-locally-dialog";
import { Alert } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { DeltaChip } from "@/components/ui/delta-chip";
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { EmptyState } from "@/components/ui/empty-state";
import { Icon } from "@/components/ui/icons";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { lazyChart } from "@/components/ui/lazy-chart";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { SearchInput } from "@/components/ui/search-input";
import { SelectableCard, SelectableCardGroup } from "@/components/ui/selectable-card";
import { LoadingState, Spinner } from "@/components/ui/spinner";
import { Switch } from "@/components/ui/switch";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { useCapabilityEvalPreload } from "@/hooks/use-capability-eval-preload";
import {
  useActivateEvalSetMutation,
  useAddEvalSetMembersMutation,
  useCreateEvalSetMutation,
  useEvalSetsQuery,
  useEvaluatorCatalogQuery,
  useEvaluatorScoreHistoryQuery,
  useRemoveEvalSetMemberMutation,
  useUpdateEvalSetMemberMutation,
} from "@/hooks/use-evaluations";
import { useGuestGate } from "@/hooks/use-guest-gate";
import { isActiveEvalPreloadStatus, resolveEvalPreloadStatus } from "@/lib/eval-preload";
import { notify } from "@/lib/notify";
import { PROSE } from "@/lib/typography";
import { cn } from "@/lib/utils";
import type {
  Capability,
  EvalSet,
  EvalSetMember,
  EvaluatorCatalog,
  EvaluatorScoreHistory,
} from "@/openapi";

const EvalScoreHistoryChart = lazyChart(
  () =>
    import("@/components/capability-detail/EvalScoreHistoryChart").then((m) => ({
      default: m.EvalScoreHistoryChart,
    })),
  { minHeight: 180 }
);

interface EvaluatorsTabProps {
  capabilityId: string;
  capability: Capability;
  projectId: string | undefined;
}

type Role = "generative" | "trace_scoring";

const ROLES: { role: Role; label: string; blurb: string; preloadAuthoringCopy: string }[] = [
  {
    blurb:
      "Graders that run when candidate models generate over the dataset (optimiser, backtest, eval runs). Scored against the model's output, with the dataset's golden reference.",
    label: "Generative testing",
    preloadAuthoringCopy: "Authoring generative testing evaluators from the capability card.",
    role: "generative",
  },
  {
    blurb:
      "Graders that score live production traces on ingest: the entry-point output, with no golden reference. The score below is from offline runs. Live per-trace scores are on the Traces page.",
    label: "Trace scoring",
    preloadAuthoringCopy: "Authoring trace scoring evaluators from the capability card.",
    role: "trace_scoring",
  },
];

const memberRole = (member: EvalSetMember): Role =>
  (member.role as Role | undefined) ?? "generative";

const memberLabel = (member: EvalSetMember): string =>
  member.evaluatorDisplayName?.trim() || member.evaluatorName;

const catalogLabel = (ev: EvaluatorCatalog): string => ev.displayName?.trim() || ev.name;

// Below this (in 0–100 points) a delta rounds to 0% and reads as "no change".
const _DELTA_EPSILON = 0.5;

/** `latestScore` stays null until a completed run produces one — a dash, never a 0. */
function EvalScoreCell({ member }: { member: EvalSetMember }) {
  if (member.latestScore == null) {
    return (
      <span
        className="font-mono text-xs tabular-nums text-muted-foreground"
        title="No completed runs for this evaluator yet"
      >
        —
      </span>
    );
  }
  return (
    <div className="inline-flex items-center gap-2">
      <span className="font-mono text-xs font-medium tabular-nums">
        {Math.round(member.latestScore)}%
      </span>
      <ScoreDelta delta={member.delta} hasPrevious={member.previousScore != null} />
    </div>
  );
}

function ScoreDelta({ delta, hasPrevious }: { delta: number | null; hasPrevious: boolean }) {
  if (delta == null || !hasPrevious) {
    return (
      <span
        className="font-mono text-xs tabular-nums text-muted-foreground"
        title="No previous run to compare"
      >
        —
      </span>
    );
  }
  return <DeltaChip label={`${delta > 0 ? "Up" : "Down"} vs previous run`} value={delta} />;
}

export function EvaluatorsTab({ capabilityId, capability, projectId }: EvaluatorsTabProps) {
  const setsQuery = useEvalSetsQuery(projectId);
  const preloadQuery = useCapabilityEvalPreload(capabilityId, { projectId });
  const { data: scoreHistory } = useEvaluatorScoreHistoryQuery(capabilityId);
  const catalogQuery = useEvaluatorCatalogQuery(projectId, capabilityId);

  const createSet = useCreateEvalSetMutation(projectId);
  const activateSet = useActivateEvalSetMutation(projectId, capabilityId);
  const addMembers = useAddEvalSetMembersMutation(projectId);
  const updateMember = useUpdateEvalSetMemberMutation(projectId);
  const removeMember = useRemoveEvalSetMemberMutation(projectId);

  const capabilitySets = useMemo<EvalSet[]>(
    () => (setsQuery.data?.results ?? []).filter((s) => s.capability === capabilityId),
    [setsQuery.data, capabilityId]
  );

  const defaultSetMemberCount = useMemo(
    () => capabilitySets.reduce((count, set) => count + (set.members?.length ?? 0), 0),
    [capabilitySets]
  );

  const preloadStatus =
    preloadQuery.data?.status ?? resolveEvalPreloadStatus(capability, { defaultSetMemberCount });
  const preloadActive = isActiveEvalPreloadStatus(preloadStatus);
  const preloadError = preloadQuery.data?.error;

  const [selectedSetId, setSelectedSetId] = useState<string | null>(null);
  const [createOpen, setCreateOpen] = useState(false);
  const [newSetName, setNewSetName] = useState("");
  const [detailId, setDetailId] = useState<string | null>(null);
  const [optimiseOpen, setOptimiseOpen] = useState(false);
  const guard = useGuestGate();
  const openCreateSet = guard(() => setCreateOpen(true));

  useEffect(() => {
    if (selectedSetId && capabilitySets.some((s) => s.id === selectedSetId)) return;
    const active = capabilitySets.find((s) => s.isActive);
    setSelectedSetId(active?.id ?? capabilitySets[0]?.id ?? null);
  }, [capabilitySets, selectedSetId]);

  const selectedSet = capabilitySets.find((s) => s.id === selectedSetId) ?? null;
  const members = useMemo(() => selectedSet?.members ?? [], [selectedSet]);

  const setMemberNames = useMemo(() => new Set(members.map((m) => m.evaluatorName)), [members]);
  const scopedSeries = useMemo<EvaluatorScoreHistory[]>(
    () => (scoreHistory ?? []).filter((s) => setMemberNames.has(s.name)),
    [scoreHistory, setMemberNames]
  );

  const catalog: EvaluatorCatalog[] = catalogQuery.data ?? [];

  const handleSelectSet = (id: string) => {
    setSelectedSetId(id);
  };

  const handleCreateSet = async () => {
    const name = newSetName.trim();
    if (!name || !projectId) return;
    try {
      const created = await createSet.mutateAsync({
        capability: capabilityId,
        name,
        project: projectId,
      });
      setSelectedSetId(created.id);
      setCreateOpen(false);
      setNewSetName("");
      toast.success("Eval set created");
    } catch (e) {
      notify.error(e, "Failed to create eval set");
    }
  };

  const handleActivate = async () => {
    if (!selectedSet) return;
    try {
      await activateSet.mutateAsync(selectedSet.id);
      toast.success("Eval set is now active");
    } catch (e) {
      notify.error(e, "Failed to activate eval set");
    }
  };

  const handleAddFromLibrary = async (role: Role, evaluatorIds: string[]) => {
    if (!selectedSet || evaluatorIds.length === 0) return;
    try {
      await addMembers.mutateAsync({
        body: { evaluatorIds, role },
        setId: selectedSet.id,
      });
      toast.success(
        evaluatorIds.length === 1
          ? "Evaluator added to set"
          : `Added ${evaluatorIds.length} evaluators to set`
      );
    } catch (e) {
      notify.error(e, "Failed to add evaluators");
    }
  };

  const handleToggleMember = async (member: EvalSetMember, enabled: boolean) => {
    if (!selectedSet) return;
    try {
      await updateMember.mutateAsync({
        body: { enabled },
        memberId: member.id,
        setId: selectedSet.id,
      });
    } catch (e) {
      notify.error(e, "Failed to update evaluator");
    }
  };

  const handleRemoveMember = async (member: EvalSetMember) => {
    if (!selectedSet) return;
    try {
      await removeMember.mutateAsync({ memberId: member.id, setId: selectedSet.id });
      toast.success("Removed from set");
    } catch (e) {
      notify.error(e, "Failed to remove evaluator");
    }
  };

  if (setsQuery.isLoading) {
    return <LoadingState />;
  }

  if (setsQuery.error) {
    return (
      <Card>
        <CardContent className="py-10 text-center text-sm text-muted-foreground">
          Failed to load eval sets for this capability.
        </CardContent>
      </Card>
    );
  }

  if (preloadActive && capabilitySets.length === 0) {
    return <EvalPreloadInProgressPanel />;
  }

  if (preloadStatus === "failed" && capabilitySets.length === 0) {
    return (
      <>
        <EvalPreloadFailedPanel
          error={preloadError}
          onCreate={openCreateSet}
          projectId={projectId}
        />
        <CreateSetDialog
          name={newSetName}
          onNameChange={setNewSetName}
          onOpenChange={setCreateOpen}
          onSubmit={handleCreateSet}
          open={createOpen}
          pending={createSet.isPending}
        />
      </>
    );
  }

  if (preloadStatus === "empty" && capabilitySets.length === 0) {
    return (
      <>
        <EvalPreloadEmptyPanel onCreate={openCreateSet} />
        <CreateSetDialog
          name={newSetName}
          onNameChange={setNewSetName}
          onOpenChange={setCreateOpen}
          onSubmit={handleCreateSet}
          open={createOpen}
          pending={createSet.isPending}
        />
      </>
    );
  }

  if (capabilitySets.length === 0) {
    return (
      <>
        <Card>
          <CardContent className="flex flex-col items-center justify-center gap-3 py-16 text-center">
            <Icon.listBox className="size-8 text-muted-foreground" />
            <div className="space-y-1">
              <p className="text-sm font-medium">No eval sets for this capability yet</p>
              <p className={cn(PROSE, "max-w-sm text-sm text-muted-foreground")}>
                Create a set to group this capability&apos;s evaluators by role. The active set
                drives the optimiser, backtest, and the run wizard.
              </p>
            </div>
            <Button className="mt-1" onClick={openCreateSet} size="sm">
              <Icon.add />
              New eval set
            </Button>
          </CardContent>
        </Card>
        <CreateSetDialog
          name={newSetName}
          onNameChange={setNewSetName}
          onOpenChange={setCreateOpen}
          onSubmit={handleCreateSet}
          open={createOpen}
          pending={createSet.isPending}
        />
      </>
    );
  }

  return (
    <div className="space-y-4">
      {preloadStatus === "failed" ? (
        <EvalPreloadFailedBanner error={preloadError} projectId={projectId} />
      ) : null}

      <div className="flex flex-wrap items-center gap-2">
        <EvalSetSelect onChange={handleSelectSet} sets={capabilitySets} value={selectedSetId} />

        {selectedSet?.isActive ? (
          // h-7 matches the adjacent EvalSetSelect trigger (Button size="sm"),
          // which Badge's own sizes don't cover.
          <Badge className="h-7 gap-1" variant="default">
            <Icon.success className="size-3" /> Active
          </Badge>
        ) : (
          <Button
            disabled={activateSet.isPending}
            onClick={guard(handleActivate)}
            size="sm"
            variant="secondary"
          >
            {activateSet.isPending ? <Spinner className="" size="sm" /> : null}
            Set active
          </Button>
        )}

        <div className="ml-auto flex flex-wrap items-center gap-2">
          <Button onClick={guard(() => setOptimiseOpen(true))} size="sm" variant="secondary">
            <Icon.optimiser />
            Optimise
          </Button>
          <Button onClick={openCreateSet} size="sm" variant="secondary">
            <Icon.add />
            New eval set
          </Button>
          <Button asChild size="sm" variant="secondary">
            <Link
              onClick={guard()}
              search={{ capability: capabilityId, projectId, view: "library" }}
              to="/evaluations"
            >
              <Icon.externalLink />
              Eval library
            </Link>
          </Button>
        </div>
      </div>

      {scopedSeries.some((s) => s.points.length > 0) ? (
        <EvalScoreHistoryChart series={scopedSeries.filter((s) => s.points.length > 0)} />
      ) : null}

      {ROLES.map(({ role, label, blurb, preloadAuthoringCopy }) => {
        const roleMembers = members.filter((m) => memberRole(m) === role);
        // By name, not id: re-authoring mints a new evaluator id, so
        // `visible_catalog` (latest version per name) and a member pinned to an
        // earlier version never share one, and an id match would re-offer a
        // grader already in the set.
        const existingNames = new Set(roleMembers.map((m) => m.evaluatorName));
        // The backend rejects an add that isn't applicable to the role.
        const addableCatalog = catalog.filter(
          (ev) => !existingNames.has(ev.name) && (ev.applicableRoles ?? []).includes(role)
        );
        return (
          <Card className="overflow-hidden p-0" key={role}>
            <div className="flex items-start justify-between gap-2 border-b border-border/70 px-4 py-3">
              <div className="min-w-0">
                <div className="flex items-center gap-2 text-sm font-medium">
                  {label}
                  <Badge variant="secondary">{roleMembers.length}</Badge>
                </div>
                <p className={cn(PROSE, "text-xs text-muted-foreground")}>{blurb}</p>
              </div>
              <AddFromLibraryPopover
                disabled={addMembers.isPending}
                evaluators={addableCatalog}
                loading={catalogQuery.isLoading}
                onAdd={(ids) => handleAddFromLibrary(role, ids)}
                role={role}
              />
            </div>

            {roleMembers.length === 0 ? (
              preloadActive ? (
                <div className="flex items-center justify-center gap-2 px-4 py-6 text-xs text-muted-foreground">
                  <Spinner size="sm" />
                  <span>{preloadAuthoringCopy}</span>
                </div>
              ) : (
                <p className={cn(PROSE, "px-4 py-6 text-center text-xs text-muted-foreground")}>
                  {`No ${label.toLowerCase()} evaluators yet — they're authored automatically when the capability's code is scanned, or add from the library.`}
                </p>
              )
            ) : (
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Name</TableHead>
                    <TableHead className="text-center">Kind</TableHead>
                    <TableHead className="w-36 text-center">
                      {role === "trace_scoring" ? (
                        <span
                          className="inline-flex items-center gap-1"
                          title="Latest offline run score for this evaluator. Live per-trace scores are on the Traces page."
                        >
                          Eval score
                          <span className="text-xs font-normal text-muted-foreground">
                            (offline)
                          </span>
                        </span>
                      ) : (
                        "Eval score"
                      )}
                    </TableHead>
                    <TableHead className="w-20 text-center">Enabled</TableHead>
                    <TableHead className="w-16 text-center">Remove</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {roleMembers.map((member) => (
                    <TableRow key={member.id}>
                      <TableCell className="max-w-xs">
                        <button
                          className="text-left text-sm hover:underline focus-visible:underline focus-visible:outline-none"
                          onClick={() => setDetailId(member.evaluator)}
                          title={member.evaluatorName}
                          type="button"
                        >
                          {memberLabel(member)}
                        </button>
                      </TableCell>
                      <TableCell className="text-center">
                        <Badge size="chip" variant="secondary">
                          {EVALUATOR_KIND_LABEL[member.evaluatorKind] ?? member.evaluatorKind}
                        </Badge>
                      </TableCell>
                      <TableCell className="text-center">
                        <EvalScoreCell member={member} />
                      </TableCell>
                      <TableCell className="text-center">
                        <Switch
                          aria-label={`Toggle ${memberLabel(member)}`}
                          checked={member.enabled ?? true}
                          onCheckedChange={guard((checked: boolean) =>
                            handleToggleMember(member, checked)
                          )}
                          size="sm"
                        />
                      </TableCell>
                      <TableCell className="text-center">
                        <ConfirmDialog
                          confirmLabel="Remove evaluator"
                          description={`Remove ${memberLabel(member)} from this set? Past scores stay in history, but it won't run in future evaluations.`}
                          destructive
                          isPending={removeMember.isPending}
                          onConfirm={() => handleRemoveMember(member)}
                          title="Remove evaluator"
                          trigger={
                            <Button
                              aria-label={`Remove ${memberLabel(member)}`}
                              className="text-muted-foreground hover:text-destructive"
                              onClick={guard()}
                              size="icon-sm"
                              variant="ghost"
                            >
                              <Icon.delete />
                            </Button>
                          }
                        />
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            )}
          </Card>
        );
      })}

      <CreateSetDialog
        name={newSetName}
        onNameChange={setNewSetName}
        onOpenChange={setCreateOpen}
        onSubmit={handleCreateSet}
        open={createOpen}
        pending={createSet.isPending}
      />

      <EvaluatorDetailDialog
        evaluatorId={detailId ?? undefined}
        onOpenChange={(open) => !open && setDetailId(null)}
        open={!!detailId}
      />

      {projectId ? (
        <RunLocallyDialog
          capabilityId={capabilityId}
          onOpenChange={setOptimiseOpen}
          open={optimiseOpen}
          projectId={projectId}
        />
      ) : null}
    </div>
  );
}

function EvalPreloadInProgressPanel() {
  return (
    <Card>
      <CardContent className="flex flex-col items-center justify-center gap-4 py-16 text-center">
        <LoadingState className="py-0" label="Authoring Default eval set" />
        <p className={cn(PROSE, "max-w-md text-sm text-muted-foreground")}>
          We&apos;re reading your agent&apos;s capability card and authoring deterministic checks
          and LLM judges. This usually takes under a minute.
        </p>
      </CardContent>
    </Card>
  );
}

function EvalPreloadFailedPanel({
  error,
  onCreate,
  projectId,
}: {
  error?: string | null;
  onCreate: () => void;
  projectId?: string;
}) {
  const guard = useGuestGate();
  return (
    <Card>
      <CardContent className="space-y-4 py-10">
        <Alert variant="destructive">
          {error?.trim() ||
            "Default eval set authoring failed after retries. Add evaluators manually from the library."}
        </Alert>
        <div className="flex flex-wrap justify-center gap-2">
          <Button onClick={onCreate} size="sm">
            <Icon.add />
            New eval set
          </Button>
          {projectId ? (
            <Button asChild size="sm" variant="secondary">
              <Link onClick={guard()} search={{ projectId, view: "library" }} to="/evaluations">
                <Icon.externalLink />
                Eval library
              </Link>
            </Button>
          ) : null}
        </div>
      </CardContent>
    </Card>
  );
}

function EvalPreloadFailedBanner({
  error,
  projectId,
}: {
  error?: string | null;
  projectId?: string;
}) {
  const guard = useGuestGate();
  return (
    <div className="space-y-2">
      <Alert variant="destructive">
        {error?.trim() ||
          "Default eval set authoring failed. Add evaluators from the library or create a new set."}
      </Alert>
      {projectId ? (
        <Button asChild size="sm" variant="secondary">
          <Link onClick={guard()} search={{ projectId, view: "library" }} to="/evaluations">
            <Icon.externalLink />
            Open Eval library
          </Link>
        </Button>
      ) : null}
    </div>
  );
}

function EvalPreloadEmptyPanel({ onCreate }: { onCreate: () => void }) {
  return (
    <EmptyState
      action={
        <Button onClick={onCreate} size="sm">
          <Icon.add />
          New eval set
        </Button>
      }
      description="No evaluators derived from the capability card. Create a set and add graders from the library."
      icon={Icon.listBox}
      size="section"
      title="No evaluators could be authored"
    />
  );
}

function CreateSetDialog({
  open,
  onOpenChange,
  name,
  onNameChange,
  onSubmit,
  pending,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  name: string;
  onNameChange: (next: string) => void;
  onSubmit: () => void;
  pending: boolean;
}) {
  return (
    <Dialog onOpenChange={onOpenChange} open={open}>
      <DialogContent size="sm">
        <DialogHeader>
          <DialogTitle>Create eval set</DialogTitle>
          <DialogDescription>
            Name a new set of evaluators for this capability. Add graders from the Eval Library
            afterwards, then activate the set when you&apos;re ready.
          </DialogDescription>
        </DialogHeader>
        <DialogBody>
          <div className="flex flex-col gap-1.5">
            <Label className="text-xs" htmlFor="new-set-name">
              Set name
            </Label>
            <Input
              autoFocus
              id="new-set-name"
              onChange={(e) => onNameChange(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && name.trim()) onSubmit();
              }}
              placeholder="e.g. Default"
              value={name}
            />
          </div>
        </DialogBody>
        <DialogFooter>
          <Button onClick={() => onOpenChange(false)} variant="secondary">
            <Icon.close />
            Cancel
          </Button>
          <Button disabled={!name.trim() || pending} onClick={onSubmit}>
            {pending ? <Spinner className="" /> : null}
            {pending ? "Creating…" : "Create eval set"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function EvalSetSelect({
  sets,
  value,
  onChange,
}: {
  sets: EvalSet[];
  value: string | null;
  onChange: (id: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const [search, setSearch] = useState("");
  const selected = sets.find((s) => s.id === value) ?? null;

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    if (!q) return sets;
    return sets.filter((s) => s.name.toLowerCase().includes(q));
  }, [sets, search]);

  const handleOpenChange = (next: boolean) => {
    setOpen(next);
    if (!next) setSearch("");
  };

  const handleSelect = (id: string) => {
    onChange(id);
    setOpen(false);
    setSearch("");
  };

  return (
    <Popover onOpenChange={handleOpenChange} open={open}>
      <PopoverTrigger asChild>
        <Button
          aria-label="Select eval set"
          className="w-auto min-w-[8rem] max-w-[14rem] justify-between"
          size="sm"
          variant="secondary"
        >
          <span className="truncate">{selected?.name ?? "Select an eval set"}</span>
          <Icon.chevronDown className="shrink-0 text-muted-foreground" />
        </Button>
      </PopoverTrigger>
      <PopoverContent align="start" className="w-64 overflow-hidden p-0">
        <div className="border-b border-border/70 p-1.5">
          <SearchInput
            label="Search eval sets"
            onChange={(e) => setSearch(e.target.value)}
            onClear={() => setSearch("")}
            placeholder="Search sets…"
            size="sm"
            value={search}
          />
        </div>
        <SelectableCardGroup className="max-h-[280px] overflow-y-auto p-1">
          {filtered.length === 0 ? (
            <p className="px-2 py-3 text-xs text-muted-foreground">No sets match "{search}".</p>
          ) : (
            filtered.map((s) => {
              const isSelected = s.id === value;
              return (
                <SelectableCard
                  className={cn(
                    "flex w-full items-center gap-2 rounded-sm px-2 py-1.5 text-xs",
                    !isSelected && "border-transparent bg-transparent hover:bg-muted"
                  )}
                  key={s.id}
                  onSelect={() => handleSelect(s.id)}
                  role="radio"
                  selected={isSelected}
                >
                  <Icon.success
                    className={cn("size-3.5 shrink-0", isSelected ? "opacity-100" : "opacity-0")}
                  />
                  <span className="truncate font-medium">{s.name}</span>
                  {s.isActive ? (
                    <span className="ml-auto shrink-0 text-xs text-primary">(active)</span>
                  ) : null}
                </SelectableCard>
              );
            })
          )}
        </SelectableCardGroup>
      </PopoverContent>
    </Popover>
  );
}

/** `evaluators` arrives already excluding this section's members, so a duplicate
 * can't be offered. */
function AddFromLibraryPopover({
  evaluators,
  loading,
  disabled,
  role,
  onAdd,
}: {
  evaluators: EvaluatorCatalog[];
  loading: boolean;
  disabled: boolean;
  role: Role;
  onAdd: (ids: string[]) => void;
}) {
  const [open, setOpen] = useState(false);
  const [search, setSearch] = useState("");
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [detailId, setDetailId] = useState<string | null>(null);
  const guard = useGuestGate();

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    if (!q) return evaluators;
    return evaluators.filter((ev) =>
      `${catalogLabel(ev)} ${ev.name} ${ev.description ?? ""}`.toLowerCase().includes(q)
    );
  }, [evaluators, search]);

  // Drop any selected id that left the addable catalogue (e.g. just added).
  const availableIds = useMemo(() => new Set(evaluators.map((ev) => ev.id)), [evaluators]);
  useEffect(() => {
    setSelected((prev) => {
      const next = new Set([...prev].filter((id) => availableIds.has(id)));
      return next.size === prev.size ? prev : next;
    });
  }, [availableIds]);

  const handleToggle = (id: string) =>
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) {
        next.delete(id);
      } else {
        next.add(id);
      }
      return next;
    });

  const handleConfirm = () => {
    if (selected.size === 0) return;
    onAdd([...selected]);
    setSelected(new Set());
    setSearch("");
    setOpen(false);
  };

  // The detail dialog steals focus; ignore that close so the multi-selection survives.
  const handleOpenChange = (next: boolean) => {
    if (!next && detailId) return;
    setOpen(next);
    if (!next) {
      setSearch("");
      setSelected(new Set());
    }
  };

  return (
    <>
      <Popover onOpenChange={handleOpenChange} open={open}>
        <PopoverTrigger asChild>
          <Button
            aria-label={`Add ${role === "generative" ? "generative" : "trace scoring"} evaluators from library`}
            className="shrink-0 px-2 text-xs"
            disabled={disabled}
            onClick={guard()}
            size="sm"
            variant="secondary"
          >
            <Icon.add />
            Add from library
          </Button>
        </PopoverTrigger>
        <PopoverContent align="end" className="w-96 overflow-hidden p-0">
          <div className="border-b border-border/70 p-1.5">
            <SearchInput
              label="Search library evaluators"
              onChange={(e) => setSearch(e.target.value)}
              onClear={() => setSearch("")}
              placeholder="Search evaluators…"
              size="sm"
              value={search}
            />
          </div>
          <div className="max-h-[300px] overflow-y-auto p-1">
            {loading ? (
              <p className="flex items-center gap-1.5 px-2 py-3 text-xs text-muted-foreground">
                <Spinner size="sm" /> Loading library…
              </p>
            ) : filtered.length === 0 ? (
              <p className="px-2 py-3 text-xs text-muted-foreground">
                {evaluators.length === 0
                  ? "All library evaluators are already in this section."
                  : `No evaluators match "${search}".`}
              </p>
            ) : (
              filtered.map((ev) => {
                const isSelected = selected.has(ev.id);
                return (
                  <div
                    className={cn(
                      "flex items-start gap-1 rounded-sm",
                      isSelected ? "bg-primary/5" : "hover:bg-muted"
                    )}
                    key={ev.id}
                  >
                    <button
                      aria-pressed={isSelected}
                      className="flex min-w-0 flex-1 items-start gap-2 rounded-sm px-2 py-1.5 text-left text-xs focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                      onClick={() => handleToggle(ev.id)}
                      type="button"
                    >
                      {/* Checkbox centres on the name's `h-5` line box; the sibling
                          "View logic" button mirrors `py-1.5` with `my-1.5` so both
                          first lines land level. */}
                      <span className="flex h-5 shrink-0 items-center">
                        <span
                          className={cn(
                            "flex size-3.5 items-center justify-center rounded-md border",
                            isSelected
                              ? "border-primary bg-primary text-primary-foreground"
                              : "border-muted-foreground/40"
                          )}
                        >
                          {isSelected ? <Icon.success className="size-2.5" /> : null}
                        </span>
                      </span>
                      <span className="flex min-w-0 flex-1 flex-col">
                        <span className="flex h-5 items-center gap-1.5">
                          <span className="truncate font-medium" title={ev.name}>
                            {catalogLabel(ev)}
                          </span>
                          <Badge className="shrink-0 text-xs" variant="outline">
                            {EVALUATOR_KIND_LABEL[ev.kind] ?? ev.kind}
                          </Badge>
                        </span>
                        {ev.description ? (
                          <span className="line-clamp-1 text-xs text-muted-foreground">
                            {ev.description}
                          </span>
                        ) : null}
                      </span>
                    </button>
                    <button
                      aria-label={`View ${catalogLabel(ev)} logic`}
                      className="my-1.5 flex h-5 shrink-0 items-center rounded-sm px-1.5 text-xs font-medium text-muted-foreground hover:bg-muted hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                      onClick={() => setDetailId(ev.id)}
                      type="button"
                    >
                      View logic
                    </button>
                  </div>
                );
              })
            )}
          </div>
          <div className="flex items-center justify-between gap-2 border-t border-border/70 p-1.5">
            <span className="pl-1 text-xs text-muted-foreground">
              {selected.size > 0 ? `${selected.size} selected` : "Select evaluators to add"}
            </span>
            <Button
              className="px-2.5 text-xs"
              disabled={selected.size === 0 || disabled}
              onClick={handleConfirm}
              size="sm"
            >
              {disabled ? <Spinner className="" size="sm" /> : null}
              Add {selected.size > 0 ? `(${selected.size})` : ""}
            </Button>
          </div>
        </PopoverContent>
      </Popover>
      <EvaluatorDetailDialog
        evaluatorId={detailId ?? undefined}
        onOpenChange={(next) => !next && setDetailId(null)}
        open={!!detailId}
      />
    </>
  );
}
