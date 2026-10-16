import { Fragment, type ReactNode, useMemo, useState } from "react";

import { toast } from "sonner";

import { ScriptCode } from "@/components/datasets/notebook/script-code";
import { EvaluationMethodBadge } from "@/components/evaluations/evaluation-method-badge";
import { MATRIX_TYPE_LABEL } from "@/components/evaluations/matrix-row-label";
import {
  PYTHON_CHECK_EXAMPLES,
  PYTHON_CHECK_HINT,
  type PythonCheckExample,
} from "@/components/evaluations/python-check-examples";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { DismissibleAlert } from "@/components/ui/dismissible-alert";
import { EmptyState } from "@/components/ui/empty-state";
import { Icon } from "@/components/ui/icons";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { ListToolbar } from "@/components/ui/list-toolbar";
import { SearchInput } from "@/components/ui/search-input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { LoadingState } from "@/components/ui/spinner";
import { Switch } from "@/components/ui/switch";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { TableSectionHeader } from "@/components/ui/table-section-header";
import { Textarea } from "@/components/ui/textarea";
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip";
import {
  useCreateEvalMatrixRowMutation,
  useDeleteEvalMatrixRowMutation,
  useEvalMatrixQuery,
} from "@/hooks/use-evaluations";
import { useGuestGate } from "@/hooks/use-guest-gate";
import { errorMessage } from "@/lib/notify";
import type { Capability, EvalMatrix, EvalMatrixTypeEnum } from "@/openapi";
import { evaluationName, presentEvaluations, type ScopeKind } from "./evaluation-presentation";

const SOURCE_LABEL: Record<string, string> = {
  deterministic: "Python check",
  llm: "Expectation",
};

const SOURCE_PLACEHOLDER: Record<string, string> = {
  deterministic: 'def evaluate(span):\n    return True, "reason"',
  llm: "The answer cites the policy it relies on.",
};

const DEFINITION_LABEL: Record<string, string> = {
  code: "Agent code",
  config: "Repository configuration",
  custom: "Platform / API",
  stock: "Overmind catalogue",
  unknown: "Not recorded",
};

const DEFINITION_DETAIL: Record<string, string> = {
  code: "Declared in an SDK decorator. Change the repository declaration and run overmind sync to update it.",
  config:
    "Defined in overmind.toml. Change the repository configuration and run overmind sync to update it.",
  custom: "Added through the platform or API.",
  stock: "Supplied by Overmind and shared across capabilities in this project.",
  unknown: "No matching saved declaration or recorded creator is available.",
};

const SCOPE_META: Record<ScopeKind, { label: string; tone: string }> = {
  model: { label: "Model call", tone: "border-cat-4/40 bg-cat-4/10 text-cat-4" },
  other: {
    label: "Other scope",
    tone: "border-muted-foreground/40 bg-wash-raised text-muted-foreground",
  },
  step: { label: "Execution step", tone: "border-cat-2/40 bg-cat-2/10 text-cat-2" },
  tool: { label: "Tool call", tone: "border-cat-3/40 bg-cat-3/10 text-cat-3" },
  whole: { label: "Whole agent", tone: "border-cat-6/40 bg-cat-6/10 text-cat-6" },
};

const SCOPE_GROUP_ORDER: ScopeKind[] = ["whole", "model", "step", "tool", "other"];

const SCOPE_GROUP_DETAIL: Record<ScopeKind, string> = {
  model: "Rules that assess model decisions",
  other: "Rules that match spans without a unique step",
  step: "Rules attached to a specific execution step",
  tool: "Rules that check a tool call and its result",
  whole: "Rules that assess the complete agent run",
};

const JUDGE_PROBABILITY_LABELS = [
  { detail: "Fully meets the expectation", label: "Yes", score: "1.00" },
  { detail: "Meets it with gaps or errors", label: "Partial", score: "0.50" },
  { detail: "Does not meet the expectation", label: "No", score: "0.00" },
] as const;

function ScopeIcon({ kind, className }: { kind: ScopeKind; className?: string }) {
  if (kind === "tool") return <Icon.tool className={className} />;
  if (kind === "model") return <Icon.model className={className} />;
  if (kind === "step") return <Icon.listBox className={className} />;
  if (kind === "whole") return <Icon.capability className={className} />;
  return <Icon.info className={className} />;
}

function CodeSnippet({ source }: { source?: string }) {
  return (
    <div className="overflow-hidden rounded-md border border-border bg-code-surface">
      <div className="px-4 pt-3 text-xs text-muted-foreground">Python</div>
      <div className="p-4">
        <ScriptCode
          aria-label="Python evaluation definition"
          className="[&_.font-mono]:[font-family:ui-monospace,SFMono-Regular,Menlo,Monaco,Consolas,monospace] [&_ol]:w-auto [&_ol]:min-w-5 [&_ol]:text-muted-foreground [&_pre]:w-max [&_pre]:min-w-full [&_pre]:whitespace-pre [&_pre]:break-normal"
          source={source ?? ""}
        />
      </div>
    </div>
  );
}

export function EvalMatrixTable({
  projectId,
  capabilityId,
  capability,
  actions,
}: {
  projectId: string;
  capabilityId?: string;
  capability?: Capability;
  actions?: ReactNode;
}) {
  const matrix = useEvalMatrixQuery(projectId, capabilityId);
  const guard = useGuestGate();
  const remove = useDeleteEvalMatrixRowMutation();
  const [search, setSearch] = useState("");
  const [status, setStatus] = useState("enabled");
  const [part, setPart] = useState("all");
  const [method, setMethod] = useState("all");
  const presented = useMemo(
    () => presentEvaluations(matrix.data ?? [], capability),
    [matrix.data, capability]
  );
  const [creating, setCreating] = useState(false);
  const [viewing, setViewing] = useState<EvalMatrix | null>(null);
  const query = search.trim().toLowerCase();
  const rows = presented.filter(({ row, name, description, scope }) => {
    const enabled = row.enabled !== false;
    return (
      (part === "all" || scope.kind === part) &&
      (method === "all" || row.type === method) &&
      (status === "all" || (status === "enabled" ? enabled : !enabled)) &&
      `${name} ${description} ${scope.label} ${scope.detail} ${row.name} ${row.type} ${row.spanType ?? ""} ${row.spanName ?? ""} ${DEFINITION_LABEL[row.definitionSource ?? "unknown"]}`
        .toLowerCase()
        .includes(query)
    );
  });
  const groupedRows = SCOPE_GROUP_ORDER.map((kind) => ({
    kind,
    rows: rows.filter(({ scope }) => scope.kind === kind),
  })).filter((group) => group.rows.length > 0);
  const hasActiveFilters =
    search.trim() !== "" || part !== "all" || method !== "all" || status !== "enabled";
  const clearFilters = () => {
    setSearch("");
    setPart("all");
    setMethod("all");
    setStatus("enabled");
  };

  return (
    <TooltipProvider>
      <div className="space-y-3">
        <TableSectionHeader
          actions={
            <div className="flex items-center gap-2">
              {actions}
              <Button onClick={guard(() => setCreating(true))} size="sm">
                <Icon.add />
                Add evaluation
              </Button>
            </div>
          }
          count={presented.length}
          description="See what is scored, where it runs, and how the score is produced."
          icon={
            <Icon.evaluations
              aria-hidden
              className="size-6 shrink-0 [image-rendering:pixelated] invert"
            />
          }
          title="Evaluation coverage"
          toolbar={
            <ListToolbar
              filters={
                <>
                  <Select onValueChange={setPart} value={part}>
                    <SelectTrigger
                      aria-label="Evaluation applies to"
                      className="w-44"
                      size="default"
                    >
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="all">All parts of the agent</SelectItem>
                      <SelectItem value="whole">Whole agent</SelectItem>
                      <SelectItem value="model">Model calls</SelectItem>
                      <SelectItem value="step">Execution steps</SelectItem>
                      <SelectItem value="tool">Tool calls</SelectItem>
                      <SelectItem value="other">Other scopes</SelectItem>
                    </SelectContent>
                  </Select>
                  <Select onValueChange={setMethod} value={method}>
                    <SelectTrigger aria-label="Evaluation method" className="w-36" size="default">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="all">All methods</SelectItem>
                      <SelectItem value="deterministic">Code checks</SelectItem>
                      <SelectItem value="llm">Model judges</SelectItem>
                    </SelectContent>
                  </Select>
                  <Select onValueChange={setStatus} value={status}>
                    <SelectTrigger aria-label="Evaluation status" className="w-36" size="default">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="enabled">Enabled</SelectItem>
                      <SelectItem value="disabled">Disabled</SelectItem>
                      <SelectItem value="all">All statuses</SelectItem>
                    </SelectContent>
                  </Select>
                </>
              }
              hasActiveFilters={hasActiveFilters}
              onClearFilters={clearFilters}
              search={
                <SearchInput
                  className="min-w-[220px] flex-1"
                  label="Search evaluations"
                  onChange={(e) => setSearch(e.target.value)}
                  onClear={() => setSearch("")}
                  placeholder="Search rules, steps or tools…"
                  value={search}
                />
              }
            />
          }
        />
        {matrix.isLoading ? (
          <LoadingState />
        ) : matrix.error ? (
          <DismissibleAlert
            error={new Error(errorMessage(matrix.error, "Couldn't load evaluations."))}
            variant="destructive"
          />
        ) : rows.length === 0 ? (
          <EmptyState
            className="rounded-md border border-dashed bg-wash-subtle"
            icon={Icon.evaluations}
            iconClassName="invert [image-rendering:pixelated]"
            size="section"
            title="No evaluations match these filters"
          />
        ) : (
          <Table
            className="table-fixed min-w-[760px]"
            containerClassName="overflow-hidden rounded-md border border-border bg-card"
          >
            <TableHeader>
              <TableRow>
                <TableHead className="w-[44%]">Evaluation</TableHead>
                <TableHead className="w-[28%]">Where it runs</TableHead>
                <TableHead className="w-40">How it is scored</TableHead>
                <TableHead className="w-32 text-right">
                  <span className="sr-only">Actions</span>
                </TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {groupedRows.map(({ kind, rows: group }) => (
                <Fragment key={kind}>
                  <TableRow className="bg-wash-subtle/60 hover:bg-wash-subtle/60">
                    <TableCell className="border-t border-border/70 px-3 py-2" colSpan={4}>
                      <div className="flex min-w-0 items-center gap-2">
                        <span
                          className={`flex size-6 shrink-0 items-center justify-center rounded-sm border ${SCOPE_META[kind].tone}`}
                        >
                          <ScopeIcon className="size-3" kind={kind} />
                        </span>
                        <span className="text-sm font-medium text-foreground">
                          {SCOPE_META[kind].label}
                        </span>
                        <Badge size="chip" variant="neutral">
                          {group.length}
                        </Badge>
                        <span className="truncate text-xs text-muted-foreground">
                          {SCOPE_GROUP_DETAIL[kind]}
                        </span>
                      </div>
                    </TableCell>
                  </TableRow>
                  {group.map(({ row, name, scope }) => (
                    <TableRow key={row.id}>
                      <TableCell className="py-2.5">
                        <Tooltip>
                          <TooltipTrigger asChild>
                            <div className="flex min-w-0 items-center gap-2">
                              <span className="block min-w-0 truncate text-sm font-medium text-foreground">
                                {name}
                              </span>
                              {row.enabled === false && (
                                <Badge className="shrink-0" size="chip" variant="neutral">
                                  Disabled
                                </Badge>
                              )}
                            </div>
                          </TooltipTrigger>
                          <TooltipContent className="max-w-lg break-words">{name}</TooltipContent>
                        </Tooltip>
                      </TableCell>
                      <TableCell className="py-2.5">
                        <Tooltip>
                          <TooltipTrigger asChild>
                            <div className="flex min-w-0 items-center gap-2">
                              <span
                                className={`flex size-6 shrink-0 items-center justify-center rounded-sm border ${SCOPE_META[scope.kind].tone}`}
                              >
                                <ScopeIcon className="size-3" kind={scope.kind} />
                              </span>
                              <span className="truncate text-sm text-foreground">
                                {scope.label}
                              </span>
                            </div>
                          </TooltipTrigger>
                          <TooltipContent className="max-w-sm break-words">
                            {scope.detail} · {row.spanName || row.spanType || "All matching spans"}
                          </TooltipContent>
                        </Tooltip>
                      </TableCell>
                      <TableCell className="py-2.5">
                        <EvaluationMethodBadge type={row.type} />
                      </TableCell>
                      <TableCell className="py-2.5">
                        <div className="flex items-center justify-end gap-1">
                          <Button
                            aria-label={`Inspect ${name}`}
                            onClick={() => setViewing(row)}
                            size="sm"
                            variant="outline"
                          >
                            <Icon.view />
                            Inspect
                          </Button>
                          <ConfirmDialog
                            confirmLabel="Delete evaluation"
                            description={`${row.name} stops scoring new spans and leaves every eval set. Existing scores are kept.`}
                            destructive
                            isPending={remove.isPending && remove.variables === row.id}
                            onConfirm={() => remove.mutate(row.id)}
                            title="Delete evaluation?"
                            trigger={
                              <Button
                                aria-label={`Delete ${row.name}`}
                                className="text-muted-foreground hover:text-destructive"
                                onClick={guard(() => undefined)}
                                size="icon-sm"
                                variant="ghost"
                              >
                                <Icon.delete className="size-3.5" />
                              </Button>
                            }
                          />
                        </div>
                      </TableCell>
                    </TableRow>
                  ))}
                </Fragment>
              ))}
            </TableBody>
          </Table>
        )}
        <Dialog onOpenChange={(open) => !open && setCreating(false)} open={creating}>
          {creating && (
            <MatrixRowForm
              capabilityId={capabilityId}
              onClose={() => setCreating(false)}
              projectId={projectId}
            />
          )}
        </Dialog>
        <MatrixRowDetail capability={capability} onClose={() => setViewing(null)} row={viewing} />
      </div>
    </TooltipProvider>
  );
}

export function MatrixRowDetail({
  row,
  onClose,
  capability,
}: {
  row: EvalMatrix | null;
  onClose: () => void;
  capability?: Capability;
}) {
  const presentation = row ? presentEvaluations([row], capability)[0] : null;
  const definition = row?.definitionSource ?? "unknown";
  return (
    <Dialog onOpenChange={(open) => !open && onClose()} open={!!row}>
      <DialogContent size="lg">
        <DialogHeader className="items-start">
          <div className="min-w-0 space-y-2">
            <div className="flex flex-wrap items-center gap-2">
              {row && <EvaluationMethodBadge type={row.type} />}
              <Badge size="chip" variant={row?.enabled === false ? "neutral" : "success"}>
                {row?.enabled === false ? "Disabled" : "Enabled"}
              </Badge>
            </div>
            <DialogTitle>{row ? evaluationName(row) : "Evaluation"}</DialogTitle>
            <DialogDescription className="max-w-2xl">
              {DEFINITION_DETAIL[definition]}
            </DialogDescription>
          </div>
        </DialogHeader>
        <DialogBody className="space-y-4">
          <dl className="grid gap-x-6 gap-y-3 rounded-md bg-wash-subtle px-4 py-3 text-sm sm:grid-cols-3">
            <div>
              <dt className="text-xs text-muted-foreground">Defined in</dt>
              <dd className="mt-1">{DEFINITION_LABEL[definition]}</dd>
            </div>
            <div>
              <dt className="text-xs text-muted-foreground">Target</dt>
              <dd className="mt-1">{presentation?.scope.label}</dd>
              <dd className="mt-1 break-all font-mono text-xs text-muted-foreground">
                {[row?.spanType, row?.spanName].filter(Boolean).join(" · ") || "All matching spans"}
              </dd>
            </div>
            <div>
              <dt className="text-xs text-muted-foreground">Coverage</dt>
              <dd className="mt-1">{row?.capability ? "This capability" : "This project"}</dd>
            </div>
          </dl>
          {row?.type === "llm" && (
            <section className="space-y-3 rounded-md border border-border bg-wash-subtle p-4">
              <div className="flex items-start gap-2.5">
                <Icon.eval className="mt-0.5 size-4 shrink-0 text-cat-4" />
                <div className="min-w-0">
                  <p className="text-sm font-medium text-foreground">How the judge scores</p>
                  <p className="mt-1 text-xs text-muted-foreground">
                    The judge returns a probability for each label. The final score is the weighted
                    sum of those probabilities.
                  </p>
                </div>
              </div>
              <div className="grid gap-px overflow-hidden rounded-sm border border-border bg-border sm:grid-cols-3">
                {JUDGE_PROBABILITY_LABELS.map(({ detail, label, score }) => (
                  <div className="bg-card px-3 py-2.5" key={label}>
                    <div className="flex items-center justify-between gap-2">
                      <span className="text-sm font-medium">{label}</span>
                      <span className="font-mono text-xs tabular-nums text-cat-4">{score}</span>
                    </div>
                    <p className="mt-1 text-xs text-muted-foreground">{detail}</p>
                  </div>
                ))}
              </div>
            </section>
          )}
          <section className="space-y-2">
            <p className="text-sm font-medium text-foreground">
              {row?.type === "llm" ? "Criterion" : "Check definition"}
            </p>
            {row?.type === "llm" ? (
              <p className="whitespace-pre-wrap rounded-sm bg-wash-subtle p-3 text-sm leading-relaxed">
                {row.source}
              </p>
            ) : (
              <CodeSnippet source={row?.source} />
            )}
          </section>
        </DialogBody>
      </DialogContent>
    </Dialog>
  );
}

function MatrixRowForm({
  projectId,
  capabilityId,
  onClose,
}: {
  projectId: string;
  capabilityId?: string;
  onClose: () => void;
}) {
  const create = useCreateEvalMatrixRowMutation();
  const [name, setName] = useState("");
  const [type, setType] = useState<EvalMatrixTypeEnum>("llm");
  const [source, setSource] = useState("");
  const [spanType, setSpanType] = useState("");
  const [spanName, setSpanName] = useState("");
  const [projectWide, setProjectWide] = useState(!capabilityId);
  const [error, setError] = useState<Error | null>(null);
  const pending = create.isPending;
  const ready = !!name.trim() && !!source.trim() && !pending;

  function loadExample(example: PythonCheckExample) {
    setSource(example.source);
    if (!spanType.trim()) setSpanType(example.spanType);
  }

  async function submit() {
    if (!ready) return;
    setError(null);
    const body = {
      capability: projectWide ? null : (capabilityId ?? null),
      name: name.trim(),
      source,
      spanName: spanName.trim(),
      spanType: spanType.trim(),
      type,
    };
    try {
      await create.mutateAsync({ ...body, project: projectId });
      toast.success("Evaluation added");
      onClose();
    } catch (err) {
      setError(new Error(errorMessage(err, "Couldn't save the evaluation.")));
    }
  }

  return (
    <DialogContent showCloseButton={!pending} size="lg">
      <DialogHeader>
        <div>
          <DialogTitle>Add evaluation</DialogTitle>
        </div>
      </DialogHeader>
      <DialogBody className="space-y-4">
        <fieldset className="grid gap-4 sm:grid-cols-2" disabled={pending}>
          <div className="space-y-2">
            <Label htmlFor="matrix-name">Name</Label>
            <Input
              id="matrix-name"
              maxLength={255}
              onChange={(e) => setName(e.target.value)}
              value={name}
            />
          </div>
          <div className="space-y-2">
            <Label htmlFor="matrix-type">Type</Label>
            <Select onValueChange={(v) => setType(v as EvalMatrixTypeEnum)} value={type}>
              <SelectTrigger className="w-full" id="matrix-type" size="default">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {Object.entries(MATRIX_TYPE_LABEL).map(([value, label]) => (
                  <SelectItem key={value} value={value}>
                    {label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div className="space-y-2 sm:col-span-2">
            <Label htmlFor="matrix-source">{SOURCE_LABEL[type]}</Label>
            <Textarea
              className="min-h-40 font-mono text-xs"
              id="matrix-source"
              onChange={(e) => setSource(e.target.value)}
              placeholder={SOURCE_PLACEHOLDER[type]}
              value={source}
            />
            {type === "deterministic" && (
              <div className="space-y-2">
                <p className="text-xs text-muted-foreground">{PYTHON_CHECK_HINT}</p>
                <div className="flex flex-wrap items-center gap-1.5">
                  <span className="text-xs text-muted-foreground">Examples</span>
                  {PYTHON_CHECK_EXAMPLES.map((example) => (
                    <Button
                      key={example.id}
                      onClick={() => loadExample(example)}
                      size="xs"
                      type="button"
                      variant="secondary"
                    >
                      {example.label}
                    </Button>
                  ))}
                </div>
              </div>
            )}
          </div>
          <div className="space-y-2">
            <Label htmlFor="matrix-span-type">Span type</Label>
            <Input
              id="matrix-span-type"
              onChange={(e) => setSpanType(e.target.value)}
              placeholder="Every span"
              value={spanType}
            />
          </div>
          <div className="space-y-2">
            <Label htmlFor="matrix-span-name">Span name</Label>
            <Input
              id="matrix-span-name"
              onChange={(e) => setSpanName(e.target.value)}
              placeholder="Every span"
              value={spanName}
            />
          </div>
          {capabilityId && (
            <label className="flex items-center gap-2 text-sm sm:col-span-2">
              <Switch checked={projectWide} onCheckedChange={setProjectWide} size="sm" />
              Project-wide
            </label>
          )}
        </fieldset>
        <DismissibleAlert error={error} variant="destructive" />
      </DialogBody>
      <DialogFooter>
        <Button disabled={pending} onClick={onClose} variant="secondary">
          Cancel
        </Button>
        <Button disabled={!ready} onClick={() => void submit()}>
          {pending ? "Creating…" : "Add evaluation"}
        </Button>
      </DialogFooter>
    </DialogContent>
  );
}
