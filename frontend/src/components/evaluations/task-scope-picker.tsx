import { type Option, SearchableSelect } from "@/components/evaluations/searchable-select";
import { Label } from "@/components/ui/label";
import { useBehaviourAuthoringContextQuery } from "@/hooks/use-behaviours";
import { PROSE } from "@/lib/typography";
import { cn } from "@/lib/utils";
import type { Behaviour } from "@/openapi";

type BehaviourRole = "outcome" | "step";

export type TaskScopeValue = {
  behaviourId: string;
  behaviourRole: BehaviourRole;
  anchorSegment: string[];
};

const WHOLE_TASK = "__outcome__";

/** Only meaningful under the "Trace scoring" test type — a live trace binds
 *  to a task, so scoping only ever applies there. Generative tests grade a
 *  dataset row, not a bound execution, so the caller should not mount this
 *  at all when the test type is "Generative tests". `value` is `null` until
 *  a task is picked. */
export function TaskScopePicker({
  capabilityId,
  onCapabilityChange,
  allBehaviours,
  behavioursLoading,
  value,
  onChange,
}: {
  capabilityId: string;
  onCapabilityChange: (id: string) => void;
  /** Unfiltered so the caller can also resolve a `behaviour_key` (edit-mode
   *  prefill) against the same list without a second fetch. */
  allBehaviours: Behaviour[];
  behavioursLoading?: boolean;
  value: TaskScopeValue | null;
  onChange: (value: TaskScopeValue | null) => void;
}) {
  const behaviours = allBehaviours.filter((b) => b.capability === capabilityId);

  const contextQuery = useBehaviourAuthoringContextQuery(
    value?.behaviourId ?? "",
    !!value?.behaviourId
  );
  const context = contextQuery.data;

  const handleBehaviourChange = (behaviourId: string) => {
    const behaviour = behaviours.find((b) => b.id === behaviourId);
    if (behaviour && behaviour.capability !== capabilityId)
      onCapabilityChange(behaviour.capability);
    onChange({
      anchorSegment: [],
      behaviourId,
      behaviourRole: "outcome",
    });
  };

  const handleStepChange = (stepValue: string) => {
    if (!value) return;
    if (stepValue === WHOLE_TASK) {
      onChange({ ...value, anchorSegment: [], behaviourRole: "outcome" });
      return;
    }
    const step = (context?.contract?.steps ?? []).find((s) => segmentKey(s.anchors) === stepValue);
    onChange({ ...value, anchorSegment: step?.anchors ?? [], behaviourRole: "step" });
  };

  const behaviourOptions: Option[] = behaviours.map((b) => ({
    label: b.displayName || b.key,
    value: b.id,
  }));
  const usableSteps = (context?.contract?.steps ?? []).filter((s) => (s.anchors ?? []).length > 0);
  const stepOptions: Option[] = [
    { label: "Whole task (outcome)", value: WHOLE_TASK },
    ...usableSteps.map((s) => ({ label: s.step, value: segmentKey(s.anchors) })),
  ];

  return (
    <div className="space-y-3 rounded-md border p-3">
      <p className={cn(PROSE, "text-xs text-muted-foreground")}>
        Trace scoring grades a bound execution — pick the task this evaluator targets.
      </p>

      {!capabilityId ? (
        <p className={cn(PROSE, "text-xs text-muted-foreground")}>
          Select a capability above to scope to one of its tasks.
        </p>
      ) : (
        <>
          <div className="space-y-1.5">
            <Label htmlFor="scope-behaviour">Task</Label>
            <SearchableSelect
              ariaLabel="Select a task to scope this evaluator to"
              onChange={handleBehaviourChange}
              options={behaviourOptions}
              placeholder={
                behavioursLoading
                  ? "Loading tasks…"
                  : behaviourOptions.length === 0
                    ? "No tasks for this capability"
                    : "Select a task…"
              }
              searchPlaceholder="Search tasks…"
              triggerClassName="w-full"
              value={value?.behaviourId ?? ""}
            />
          </div>

          {value?.behaviourId && (
            <div className="space-y-1.5">
              <Label htmlFor="scope-step">Step</Label>
              <SearchableSelect
                ariaLabel="Grade the whole task or one of its steps"
                onChange={handleStepChange}
                options={stepOptions}
                placeholder={contextQuery.isLoading ? "Loading steps…" : undefined}
                searchPlaceholder="Search steps…"
                triggerClassName="w-full"
                value={
                  value.behaviourRole === "step" ? segmentKey(value.anchorSegment) : WHOLE_TASK
                }
              />
            </div>
          )}
        </>
      )}
    </div>
  );
}

function segmentKey(anchors: string[] | undefined): string {
  return (anchors ?? []).join("|");
}
