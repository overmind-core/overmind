import { useEffect, useState } from "react";

import { toast } from "sonner";

import { PromptTemplateEditor } from "@/components/evaluations/prompt-template-editor";
import { type Option, SearchableSelect } from "@/components/evaluations/searchable-select";
import { TaskScopePicker, type TaskScopeValue } from "@/components/evaluations/task-scope-picker";
import {
  getModelProviderInfo,
  getProviderIcon,
  ProviderLogo,
} from "@/components/model-provider-chip";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Icon } from "@/components/ui/icons";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Spinner } from "@/components/ui/spinner";
import { Textarea } from "@/components/ui/textarea";
import { useBehavioursQuery } from "@/hooks/use-behaviours";
import {
  useAuthorJudgeEvaluatorMutation,
  useEditJudgeEvaluatorMutation,
  useEvalSetsQuery,
  useGenerateEvaluatorPromptMutation,
  useModelCatalogQuery,
  useProjectCapabilitiesQuery,
} from "@/hooks/use-evaluations";
import { notify } from "@/lib/notify";
import { PROSE } from "@/lib/typography";
import { cn } from "@/lib/utils";
import type { ApplicableRoleEnum, Evaluator, ScoreTypeD08Enum } from "@/openapi";

type ScoreType = ScoreTypeD08Enum;
type AuthoringMode = "manual" | "generate";
type Role = ApplicableRoleEnum;

// Generative and trace scoring aren't interoperable — author exactly one.
const ROLE_OPTIONS: Option[] = [
  { label: "Generative tests", value: "generative" },
  { label: "Trace scoring", value: "trace_scoring" },
];
const DEFAULT_ROLE: Role = "generative";

/** Legacy multi/empty roles collapse to generative. */
const roleFromEvaluator = (evaluator: Evaluator): Role => {
  const raw: unknown[] = Array.isArray(evaluator.applicableRoles) ? evaluator.applicableRoles : [];
  if (raw.includes("trace_scoring") && !raw.includes("generative")) return "trace_scoring";
  return DEFAULT_ROLE;
};

function JudgeModelRow({ modelId, isDefault }: { modelId: string; isDefault: boolean }) {
  const info = getModelProviderInfo(modelId);
  return (
    <div className="flex min-w-0 flex-1 items-center gap-2">
      <ProviderLogo
        Icon={getProviderIcon(info.id)}
        providerLabel={info.providerLabel}
        providerSlug={info.providerSlug}
      />
      <span className="truncate">{info.modelLabel}</span>
      {isDefault ? (
        <span className="ml-auto shrink-0 text-xs text-muted-foreground">default</span>
      ) : null}
    </div>
  );
}

const SCORE_TYPE_OPTIONS: Option[] = [
  { label: "Categorical", value: "categorical" },
  { label: "Boolean", value: "boolean" },
  { label: "Numeric", value: "numeric" },
];

const REASONING_DEFAULTS: Record<ScoreType, string> = {
  boolean: "Explain briefly why the answer does or does not satisfy the criteria.",
  categorical: "Explain why the selected category is the best match.",
  numeric: "Explain the assigned score in one concise sentence.",
};
const NUMERIC_OUTPUT_DEFAULT =
  "Return a numeric score between 0 and 1, where 0 is the worst outcome and 1 is the best outcome.";
const BOOLEAN_VERDICT_DEFAULT =
  "Return true if the answer satisfies the criteria, otherwise return false.";
const SELECTION_DEFAULT_SINGLE = "Choose exactly one category from the provided list.";
const SELECTION_DEFAULT_MULTI = "Choose every category from the provided list that applies.";

const REASONING_HELP =
  "How the LLM explains its evaluation. The explanation is prompted before the score.";

const CAPABILITY_NONE = "none";
const NO_EVAL_SET = "__none__";

function readString(source: Record<string, unknown>, key: string): string {
  const value = source[key];
  return typeof value === "string" ? value : "";
}

/** Edit mode needs `editEvaluator` plus a controlled `open`/`onOpenChange`, and
 * is only valid for `kind === "llm_judge"`. */
export const CreateEvaluatorDialog = ({
  projectId,
  editEvaluator,
  open: controlledOpen,
  onOpenChange,
}: {
  projectId?: string | undefined;
  editEvaluator?: Evaluator;
  open?: boolean;
  onOpenChange?: (open: boolean) => void;
}) => {
  const isEdit = !!editEvaluator;
  const resolvedProjectId = isEdit ? (editEvaluator?.project ?? undefined) : projectId;

  const [internalOpen, setInternalOpen] = useState(false);
  const open = isEdit ? (controlledOpen ?? false) : internalOpen;

  const author = useAuthorJudgeEvaluatorMutation(resolvedProjectId);
  const edit = useEditJudgeEvaluatorMutation(resolvedProjectId);
  const generate = useGenerateEvaluatorPromptMutation();
  const capabilitiesQuery = useProjectCapabilitiesQuery(resolvedProjectId);
  const capabilities = capabilitiesQuery.data?.results ?? [];
  const behavioursQuery = useBehavioursQuery(open);
  const allBehaviours = behavioursQuery.data?.results ?? [];

  const [mode, setMode] = useState<AuthoringMode>("manual");
  const [name, setName] = useState("");
  const [judgeModel, setJudgeModel] = useState("");
  const [evaluationPrompt, setEvaluationPrompt] = useState("");
  const [scoreType, setScoreType] = useState<ScoreType>("numeric");
  const [reasoningPrompt, setReasoningPrompt] = useState(REASONING_DEFAULTS.numeric);
  const [outputPrompt, setOutputPrompt] = useState(NUMERIC_OUTPUT_DEFAULT);
  const [verdictPrompt, setVerdictPrompt] = useState(BOOLEAN_VERDICT_DEFAULT);
  const [categories, setCategories] = useState<string[]>(["", ""]);
  const [allowMultiple, setAllowMultiple] = useState(false);
  const [selectionPrompt, setSelectionPrompt] = useState(SELECTION_DEFAULT_SINGLE);
  // "" = none. A tagged capability also grounds prompt generation.
  const [capabilityId, setCapabilityId] = useState<string>("");
  const [description, setDescription] = useState("");
  const [applicableRole, setApplicableRole] = useState<Role>(DEFAULT_ROLE);
  const [taskScope, setTaskScope] = useState<TaskScopeValue | null>(null);
  const [evalSetId, setEvalSetId] = useState("");

  const evalSetsQuery = useEvalSetsQuery(resolvedProjectId);
  const capabilityNameById = new Map(capabilities.map((a) => [a.id, a.name || a.slug] as const));
  // Same gate as the Eval sets tab: drop sets whose capability is gone, and
  // only the selected capability's sets when one is tagged.
  const capabilitiesLoaded = !!capabilitiesQuery.data;
  const evalSets = (evalSetsQuery.data?.results ?? [])
    .filter(
      (s) =>
        s.project === resolvedProjectId &&
        (!s.capability || !capabilitiesLoaded || capabilityNameById.has(s.capability)) &&
        (!capabilityId || !s.capability || s.capability === capabilityId)
    )
    .sort((a, b) => {
      const capCmp = (
        capabilityNameById.get(a.capability ?? "") ??
        a.capability ??
        ""
      ).localeCompare(capabilityNameById.get(b.capability ?? "") ?? b.capability ?? "");
      return capCmp || a.name.localeCompare(b.name);
    });

  const usingDefaultModel = judgeModel === "";
  const modelDefaults = useModelCatalogQuery(open).data?.defaults;
  const defaultJudgeModel = modelDefaults?.judgeModel ?? "";
  const judgeModels = [
    ...(modelDefaults?.judgeModels ?? []),
    ...(judgeModel && !modelDefaults?.judgeModels.includes(judgeModel) ? [judgeModel] : []),
  ];

  const capabilityOptions: Option[] = [
    { label: "No capability (generic)", value: CAPABILITY_NONE },
    ...capabilities.map((a) => ({ label: a.name, value: a.id })),
  ];
  const evalSetOptions: Option[] = [
    { label: "Add to eval set", value: NO_EVAL_SET },
    ...evalSets.map((s) => {
      const capabilityName = capabilityNameById.get(s.capability ?? "");
      return {
        label: capabilityId || !capabilityName ? s.name : `${s.name} · ${capabilityName}`,
        value: s.id,
      };
    }),
  ];

  const handleCapabilityChange = (next: string) => {
    setCapabilityId(next);
    if (!evalSetId) return;
    const match = evalSetsQuery.data?.results?.find((s) => s.id === evalSetId);
    if (match?.capability && next && match.capability !== next) setEvalSetId("");
  };

  const resetForm = () => {
    setMode("manual");
    setName("");
    setJudgeModel("");
    setEvaluationPrompt("");
    setScoreType("numeric");
    setReasoningPrompt(REASONING_DEFAULTS.numeric);
    setOutputPrompt(NUMERIC_OUTPUT_DEFAULT);
    setVerdictPrompt(BOOLEAN_VERDICT_DEFAULT);
    setCategories(["", ""]);
    setAllowMultiple(false);
    setSelectionPrompt(SELECTION_DEFAULT_SINGLE);
    setCapabilityId("");
    setDescription("");
    setApplicableRole(DEFAULT_ROLE);
    setTaskScope(null);
    setEvalSetId("");
  };

  // Resolves `config.behaviour`'s behaviour_key back to a Behaviour id against
  // the loaded list, so it may re-run once `allBehaviours` finishes loading
  // without disturbing the unrelated prompt/score fields the other effect owns.
  useEffect(() => {
    if (!isEdit || !editEvaluator || !open) return;
    // Task scoping only ever renders under trace scoring — never prefill it
    // for a generative row, even if it happens to carry a stale binding.
    if (roleFromEvaluator(editEvaluator) !== "trace_scoring") {
      setTaskScope(null);
      return;
    }
    const config = (editEvaluator.config ?? {}) as Record<string, unknown>;
    const binding = (config.behaviour ?? null) as {
      behaviour_key?: string;
      role?: string;
      anchor_segment?: string[];
    } | null;
    if (!binding?.behaviour_key) {
      setTaskScope(null);
      return;
    }
    const behaviour = allBehaviours.find(
      (b) => b.key === binding.behaviour_key && b.capability === editEvaluator.capability
    );
    if (!behaviour) return;
    setTaskScope({
      anchorSegment: binding.anchor_segment ?? [],
      behaviourId: behaviour.id,
      behaviourRole: binding.role === "step" ? "step" : "outcome",
    });
  }, [isEdit, editEvaluator, open, allBehaviours]);

  useEffect(() => {
    if (!isEdit || !editEvaluator || !open) return;
    const config = (editEvaluator.config ?? {}) as Record<string, unknown>;
    const authoring = (config.authoring ?? {}) as Record<string, unknown>;
    const nextType = (editEvaluator.scoreType ?? "numeric") as ScoreType;
    const storedCategories = Array.isArray(authoring.categories)
      ? (authoring.categories as unknown[]).map((c) => String(c))
      : [];
    const choiceLabels = Array.isArray(editEvaluator.choices)
      ? (editEvaluator.choices as unknown[]).map((c) =>
          readString((c ?? {}) as Record<string, unknown>, "label")
        )
      : [];
    const cats = storedCategories.length >= 2 ? storedCategories : choiceLabels;

    setMode("manual");
    setName(editEvaluator.name);
    setJudgeModel(editEvaluator.judgeModel ?? "");
    setEvaluationPrompt(
      readString(authoring, "evaluation_prompt") || (editEvaluator.rubricMd ?? "")
    );
    setScoreType(nextType);
    setReasoningPrompt(
      readString(authoring, "score_reasoning_prompt") || REASONING_DEFAULTS[nextType]
    );
    setOutputPrompt(readString(authoring, "score_output_prompt") || NUMERIC_OUTPUT_DEFAULT);
    setVerdictPrompt(readString(authoring, "boolean_verdict_prompt") || BOOLEAN_VERDICT_DEFAULT);
    setCategories(cats.length >= 2 ? cats : ["", ""]);
    setAllowMultiple(authoring.allow_multiple === true);
    setSelectionPrompt(
      readString(authoring, "category_selection_prompt") || SELECTION_DEFAULT_SINGLE
    );
    setCapabilityId(editEvaluator.capability ?? "");
    setDescription("");
    setApplicableRole(roleFromEvaluator(editEvaluator));
  }, [isEdit, editEvaluator, open]);

  const setOpen = (next: boolean) => {
    if (isEdit) {
      onOpenChange?.(next);
    } else {
      setInternalOpen(next);
      if (!next) resetForm();
    }
  };

  const triggerButton = (
    <Button>
      <Icon.eval /> New evaluator
    </Button>
  );

  const handleScoreTypeChange = (value: string) => {
    const next = value as ScoreType;
    setScoreType(next);
    setReasoningPrompt(REASONING_DEFAULTS[next]);
  };

  const handleAllowMultipleChange = (checked: boolean) => {
    setAllowMultiple(checked);
    setSelectionPrompt((prev) => {
      if (prev === SELECTION_DEFAULT_SINGLE || prev === SELECTION_DEFAULT_MULTI) {
        return checked ? SELECTION_DEFAULT_MULTI : SELECTION_DEFAULT_SINGLE;
      }
      return prev;
    });
  };

  // Task scoping only applies to trace scoring — a generative judge grades a
  // dataset row, not a bound execution — so switching away drops it rather
  // than silently carrying a stale task-scope into a save that ignores it.
  const handleApplicableRoleChange = (next: Role) => {
    setApplicableRole(next);
    if (next !== "trace_scoring") setTaskScope(null);
  };

  const cleanCategories = categories.map((c) => c.trim()).filter(Boolean);
  const categoriesValid = scoreType !== "categorical" || cleanCategories.length >= 2;
  const traceScoringTaskValid = applicableRole !== "trace_scoring" || !!taskScope?.behaviourId;
  const saving = author.isPending || edit.isPending;
  const canSave =
    !!resolvedProjectId &&
    name.trim().length > 0 &&
    evaluationPrompt.trim().length > 0 &&
    categoriesValid &&
    traceScoringTaskValid &&
    !saving;
  // Surfaced next to the Save button so a disabled state is never a silent dead end.
  const saveBlockedReason = saving
    ? ""
    : !name.trim()
      ? "Name this evaluator to save it."
      : !evaluationPrompt.trim()
        ? "Write an evaluation prompt to save it."
        : !categoriesValid
          ? "Add at least two categories to save it."
          : !traceScoringTaskValid
            ? "Select a task to scope this trace-scoring evaluator."
            : "";

  const handleGenerate = async () => {
    if (!description.trim()) return;
    try {
      const result = await generate.mutateAsync({
        applicableRole,
        capability: capabilityId || undefined,
        description: description.trim(),
      });
      const nextType = result.scoreType as ScoreType;
      setEvaluationPrompt(result.prompt);
      setScoreType(nextType);
      setReasoningPrompt(result.scoreReasoningPrompt || REASONING_DEFAULTS[nextType]);
      setOutputPrompt(result.scoreOutputPrompt || NUMERIC_OUTPUT_DEFAULT);
      setVerdictPrompt(result.booleanVerdictPrompt || BOOLEAN_VERDICT_DEFAULT);
      setAllowMultiple(result.allowMultiple);
      setSelectionPrompt(
        result.categorySelectionPrompt ||
          (result.allowMultiple ? SELECTION_DEFAULT_MULTI : SELECTION_DEFAULT_SINGLE)
      );
      setCategories(result.categories.length >= 2 ? result.categories : ["", ""]);
      setMode("manual");
      toast.success(
        result.grounded
          ? "Evaluator generated from the capability's context"
          : "Evaluator generated"
      );
    } catch (err) {
      notify.error(err, "Failed to generate an evaluator");
    }
  };

  const buildInput = () => ({
    applicableRoles: [applicableRole],
    capability: capabilityId || undefined,
    evaluationPrompt: evaluationPrompt.trim(),
    judgeModel,
    name: name.trim(),
    project: resolvedProjectId!,
    scoreReasoningPrompt: reasoningPrompt,
    scoreType,
    ...(scoreType === "numeric" && { scoreOutputPrompt: outputPrompt }),
    ...(scoreType === "boolean" && { booleanVerdictPrompt: verdictPrompt }),
    ...(scoreType === "categorical" && {
      allowMultiple,
      categories: cleanCategories,
      categorySelectionPrompt: selectionPrompt,
    }),
    ...(taskScope?.behaviourId && {
      anchorSegment: taskScope.behaviourRole === "step" ? taskScope.anchorSegment : undefined,
      behaviour: taskScope.behaviourId,
      behaviourRole: taskScope.behaviourRole,
    }),
    // Attaches as whichever role this evaluator is already being authored for —
    // a second, independent role choice here would just invite the two disagreeing
    // and failing save with no visible reason why.
    ...(evalSetId && {
      evalSet: evalSetId,
      evalSetRole: applicableRole,
    }),
  });

  const handleSave = async () => {
    if (!canSave || !resolvedProjectId) return;
    const input = buildInput();
    try {
      if (isEdit && editEvaluator) {
        await edit.mutateAsync({ id: editEvaluator.id, input });
        toast.success("Evaluator updated");
      } else {
        await author.mutateAsync(input);
        toast.success("Evaluator created");
        resetForm();
      }
      setOpen(false);
    } catch (e) {
      notify.error(e, isEdit ? "Failed to update evaluator" : "Failed to create evaluator");
    }
  };

  return (
    <Dialog onOpenChange={setOpen} open={open}>
      {!isEdit && <DialogTrigger asChild>{triggerButton}</DialogTrigger>}
      <DialogContent size="lg">
        <DialogHeader>
          <DialogTitle>{isEdit ? "Edit evaluator" : "New evaluator"}</DialogTitle>
          <DialogDescription className="sr-only">
            Author a runnable LLM-as-a-judge evaluator.
          </DialogDescription>
        </DialogHeader>

        <DialogBody className="space-y-5">
          {/* Same half/half grid as Capability + Test type, so Model left-aligns with it. */}
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
            <div className="space-y-1.5">
              <Label htmlFor="evaluator-name">Name</Label>
              <Input
                id="evaluator-name"
                onChange={(e) => setName(e.target.value)}
                placeholder="Name this evaluator"
                value={name}
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="judge-model">Model</Label>
              <div className="flex items-center gap-2">
                <Select onValueChange={setJudgeModel} value={judgeModel || defaultJudgeModel}>
                  <SelectTrigger
                    aria-label="Select judge model"
                    className="min-w-0 flex-1"
                    id="judge-model"
                    size="default"
                  >
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {judgeModels.map((id) => (
                      <SelectItem key={id} textValue={id} value={id}>
                        <JudgeModelRow isDefault={id === defaultJudgeModel} modelId={id} />
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
                <Button
                  disabled={usingDefaultModel}
                  onClick={() => setJudgeModel("")}
                  title="Reset to the platform default judge model"
                  type="button"
                  variant="secondary"
                >
                  <Icon.undo />
                  Use default
                </Button>
              </div>
            </div>
          </div>

          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
            <div className="space-y-1.5">
              <Label htmlFor="capability-tag">Capability (optional)</Label>
              <SearchableSelect
                ariaLabel="Tag this evaluator to a capability"
                onChange={(v) => handleCapabilityChange(v === CAPABILITY_NONE ? "" : v)}
                options={capabilityOptions}
                placeholder="No capability (generic)"
                searchPlaceholder="Search capabilities…"
                triggerClassName="w-full"
                value={capabilityId || CAPABILITY_NONE}
              />
              <p className={cn(PROSE, "text-xs text-muted-foreground")}>
                {capabilityId
                  ? "Tagged to this capability — it appears on that capability's eval metrics, and generation is grounded in its context."
                  : "Leave unset for a generic, project-level evaluator."}
              </p>
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="applicable-role">Test type</Label>
              <SearchableSelect
                ariaLabel="Select test type: generative tests or trace scoring"
                onChange={(v) => handleApplicableRoleChange(v as Role)}
                options={ROLE_OPTIONS}
                searchPlaceholder="Search…"
                triggerClassName="w-full"
                value={applicableRole}
              />
              <p className={cn(PROSE, "text-xs text-muted-foreground")}>
                {applicableRole === "trace_scoring"
                  ? "Grades the capability's live traces."
                  : "Runs when candidate models generate over the dataset (optimiser, backtest, eval runs)."}
              </p>
            </div>
          </div>

          {resolvedProjectId && applicableRole === "trace_scoring" && (
            <TaskScopePicker
              allBehaviours={allBehaviours}
              behavioursLoading={behavioursQuery.isLoading}
              capabilityId={capabilityId}
              onCapabilityChange={handleCapabilityChange}
              onChange={setTaskScope}
              value={taskScope}
            />
          )}

          <div className="space-y-4">
            <div className="flex items-center justify-between gap-3">
              <h3 className="text-sm font-semibold">Prompt</h3>
              <div className="inline-flex rounded-md border p-0.5">
                <button
                  className={cn(
                    "rounded-sm px-3 py-1 text-xs font-medium",
                    mode === "manual"
                      ? "bg-primary text-primary-foreground"
                      : "text-muted-foreground hover:text-foreground"
                  )}
                  onClick={() => setMode("manual")}
                  type="button"
                >
                  Write it yourself
                </button>
                <button
                  className={cn(
                    "rounded-sm px-3 py-1 text-xs font-medium",
                    mode === "generate"
                      ? "bg-primary text-primary-foreground"
                      : "text-muted-foreground hover:text-foreground"
                  )}
                  onClick={() => setMode("generate")}
                  type="button"
                >
                  Generate from a description
                </button>
              </div>
            </div>

            {mode === "generate" && (
              <div className="min-w-0 space-y-3">
                <Textarea
                  aria-label="Describe the test you want to create"
                  className="min-h-40 rounded-md"
                  id="generate-description"
                  onChange={(e) => setDescription(e.target.value)}
                  placeholder="Describe the test you want to create"
                  value={description}
                />
                <div className="flex justify-end">
                  <Button
                    disabled={!description.trim() || generate.isPending}
                    onClick={handleGenerate}
                    type="button"
                    variant="secondary"
                  >
                    {generate.isPending ? (
                      <>
                        <Spinner size="sm" /> Generating…
                      </>
                    ) : (
                      "Generate"
                    )}
                  </Button>
                </div>
              </div>
            )}

            {mode === "manual" && (
              <div className="min-w-0 space-y-1.5">
                <Label htmlFor="evaluation-prompt">Evaluation prompt</Label>
                <PromptTemplateEditor
                  id="evaluation-prompt"
                  onChange={setEvaluationPrompt}
                  placeholder="Evaluate {{output}} for…"
                  value={evaluationPrompt}
                />
              </div>
            )}

            <div className="space-y-1.5">
              <Label htmlFor="score-type">Score type</Label>
              <p className={cn(PROSE, "text-xs text-muted-foreground")}>
                Choose whether the evaluator should return a numeric score, a boolean verdict, or
                one of a fixed set of categories.
              </p>
              <SearchableSelect
                ariaLabel="Select score type"
                onChange={handleScoreTypeChange}
                options={SCORE_TYPE_OPTIONS}
                searchPlaceholder="Search types…"
                triggerClassName="w-56"
                value={scoreType}
              />
            </div>

            {scoreType === "categorical" && (
              <div className="space-y-4">
                <div className="space-y-2">
                  <Label>Categories</Label>
                  <p className={cn(PROSE, "text-xs text-muted-foreground")}>
                    Add the allowed category values the model may return. Categories must be
                    exhaustive. If you need a catch-all outcome (e.g. 'No match'), add it explicitly
                    as one of the categories.
                  </p>
                  <div className="space-y-2">
                    {categories.map((cat, i) => (
                      <div className="flex items-center gap-2" key={i}>
                        <Input
                          aria-label={`Category ${i + 1}`}
                          onChange={(e) =>
                            setCategories((prev) =>
                              prev.map((c, j) => (j === i ? e.target.value : c))
                            )
                          }
                          placeholder="Category"
                          value={cat}
                        />
                        <Button
                          aria-label="Remove category"
                          disabled={categories.length <= 1}
                          onClick={() => setCategories((prev) => prev.filter((_, j) => j !== i))}
                          size="icon"
                          type="button"
                          variant="ghost"
                        >
                          <Icon.delete />
                        </Button>
                      </div>
                    ))}
                  </div>
                  <Button
                    onClick={() => setCategories((prev) => [...prev, ""])}
                    size="sm"
                    type="button"
                    variant="secondary"
                  >
                    <Icon.add /> Add category
                  </Button>
                  {!categoriesValid && (
                    <p className="text-xs text-destructive">
                      Add at least two categories — they must be exhaustive.
                    </p>
                  )}
                </div>

                <label className="flex items-start gap-2" htmlFor="allow-multiple">
                  <Checkbox
                    checked={allowMultiple}
                    className="mt-0.5"
                    id="allow-multiple"
                    onCheckedChange={(c) => handleAllowMultipleChange(c === true)}
                  />
                  <span className="space-y-0.5">
                    <span className="block text-sm font-medium">Allow multiple matches</span>
                    <span className="block text-xs text-muted-foreground">
                      Lets the model return more than one category. One score will be created for
                      each selected match.
                    </span>
                  </span>
                </label>

                <div className="space-y-1.5">
                  <Label htmlFor="reasoning-prompt-cat">Score reasoning prompt</Label>
                  <p className={cn(PROSE, "text-xs text-muted-foreground")}>{REASONING_HELP}</p>
                  <Input
                    id="reasoning-prompt-cat"
                    onChange={(e) => setReasoningPrompt(e.target.value)}
                    value={reasoningPrompt}
                  />
                </div>

                <div className="space-y-1.5">
                  <Label htmlFor="selection-prompt">Category selection prompt</Label>
                  <p className={cn(PROSE, "text-xs text-muted-foreground")}>
                    Define how the LLM should choose
                    {allowMultiple ? " the matching categories" : " exactly one category"} from the
                    list below.
                  </p>
                  <Input
                    id="selection-prompt"
                    onChange={(e) => setSelectionPrompt(e.target.value)}
                    value={selectionPrompt}
                  />
                </div>
              </div>
            )}

            {scoreType === "boolean" && (
              <div className="space-y-4">
                <div className="space-y-1.5">
                  <Label htmlFor="reasoning-prompt-bool">Score reasoning prompt</Label>
                  <p className={cn(PROSE, "text-xs text-muted-foreground")}>{REASONING_HELP}</p>
                  <Input
                    id="reasoning-prompt-bool"
                    onChange={(e) => setReasoningPrompt(e.target.value)}
                    value={reasoningPrompt}
                  />
                </div>
                <div className="space-y-1.5">
                  <Label htmlFor="verdict-prompt">Boolean verdict prompt</Label>
                  <p className={cn(PROSE, "text-xs text-muted-foreground")}>
                    Define how the LLM should return either true or false based on the evaluation
                    criteria.
                  </p>
                  <Input
                    id="verdict-prompt"
                    onChange={(e) => setVerdictPrompt(e.target.value)}
                    value={verdictPrompt}
                  />
                </div>
              </div>
            )}

            {scoreType === "numeric" && (
              <div className="space-y-4">
                <div className="space-y-1.5">
                  <Label htmlFor="reasoning-prompt-num">Score reasoning prompt</Label>
                  <p className={cn(PROSE, "text-xs text-muted-foreground")}>{REASONING_HELP}</p>
                  <Input
                    id="reasoning-prompt-num"
                    onChange={(e) => setReasoningPrompt(e.target.value)}
                    value={reasoningPrompt}
                  />
                </div>
                <div className="space-y-1.5">
                  <Label htmlFor="output-prompt">Score output prompt</Label>
                  <p className={cn(PROSE, "text-xs text-muted-foreground")}>
                    Define how the LLM should return the evaluation score in natural language. Needs
                    to yield a numeric value.
                  </p>
                  <Input
                    id="output-prompt"
                    onChange={(e) => setOutputPrompt(e.target.value)}
                    value={outputPrompt}
                  />
                </div>
              </div>
            )}
          </div>
        </DialogBody>

        <DialogFooter className="flex-col items-stretch gap-2">
          {saveBlockedReason && (
            <p className={cn(PROSE, "text-center text-xs text-muted-foreground")}>
              {saveBlockedReason}
            </p>
          )}
          <div className="flex items-center gap-2">
            <SearchableSelect
              ariaLabel="Add to an eval set (optional)"
              onChange={(v) => setEvalSetId(v === NO_EVAL_SET ? "" : v)}
              options={evalSetOptions}
              placeholder="Add to eval set"
              searchPlaceholder="Search eval sets…"
              triggerClassName="min-w-0 flex-1"
              value={evalSetId || NO_EVAL_SET}
            />
            <Button className="shrink-0" disabled={!canSave} onClick={handleSave}>
              {saving ? (
                <>
                  <Spinner size="sm" /> {isEdit ? "Saving…" : "Creating…"}
                </>
              ) : isEdit ? (
                "Save changes"
              ) : (
                "Create evaluator"
              )}
            </Button>
          </div>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
};
