import { useEffect, useRef, useState } from "react";

import { useMutation } from "@tanstack/react-query";
import {
  Cancel as X,
  ChevronLeft,
  ChevronRight,
  Delete as Trash2,
  Loader as Loader2,
  PenSquare as Pencil,
  Plus,
} from "pixelarticons/react";
import { toast } from "sonner";

import apiClient from "@/client";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader } from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";

interface Props {
  agentSlug: string;
  promptId: string;
  projectId?: string;
}

const MAX_RULES = 5;

function capitalize(s: string) {
  return s.charAt(0).toUpperCase() + s.slice(1);
}

export function AgentCriteriaCard({ agentSlug, promptId, projectId }: Props) {
  // Full criteria map: metric -> rules[]
  const [criteriaMap, setCriteriaMap] = useState<Record<string, string[]>>({});
  const [metricIndex, setMetricIndex] = useState(0);
  const [isLoading, setIsLoading] = useState(true);
  const [fetchError, setFetchError] = useState<string | null>(null);

  // Edit state
  const [isEditing, setIsEditing] = useState(false);
  const [editRules, setEditRules] = useState<string[]>([]);
  const [newRule, setNewRule] = useState("");

  // Re-eval dialog
  const [showReEvalDialog, setShowReEvalDialog] = useState(false);
  const pendingSaveRef = useRef<Record<string, string[]>>({});

  useEffect(() => {
    setIsLoading(true);
    setFetchError(null);
    apiClient.agentReviews
      .getSpansForReviewApiV1AgentReviewsPromptSlugReviewSpansGet({
        promptSlug: agentSlug,
        projectId: projectId,
      })
      .then((e) => {
        setCriteriaMap(e.evaluationCriteria ?? {});
        setMetricIndex(0);
      })
      .catch((err: Error) => setFetchError(err.message))
      .finally(() => setIsLoading(false));
  }, [agentSlug, projectId]);

  const metrics = Object.keys(criteriaMap);
  const currentMetric = metrics[metricIndex] ?? "correctness";
  const currentRules = criteriaMap[currentMetric] ?? [];

  function goToPrev() {
    if (isEditing) return;
    setMetricIndex((i) => (i - 1 + Math.max(metrics.length, 1)) % Math.max(metrics.length, 1));
  }

  function goToNext() {
    if (isEditing) return;
    setMetricIndex((i) => (i + 1) % Math.max(metrics.length, 1));
  }

  function startEditing() {
    setEditRules([...currentRules]);
    setNewRule("");
    setIsEditing(true);
  }

  function cancelEditing() {
    setIsEditing(false);
    setNewRule("");
  }

  function addRule() {
    const trimmed = newRule.trim();
    if (!trimmed || editRules.length >= MAX_RULES) return;
    setEditRules((prev) => [...prev, trimmed]);
    setNewRule("");
  }

  function removeRule(idx: number) {
    setEditRules((prev) => prev.filter((_, i) => i !== idx));
  }

  function updateRule(idx: number, value: string) {
    setEditRules((prev) => prev.map((r, i) => (i === idx ? value : r)));
  }

  function rulesChanged(): boolean {
    const normalize = (r: string) => r.trim().toLowerCase();
    if (editRules.length !== currentRules.length) return true;
    return editRules.some((r, i) => normalize(r) !== normalize(currentRules[i]));
  }

  function handleSaveClick() {
    pendingSaveRef.current = { ...criteriaMap, [currentMetric]: editRules };
    if (rulesChanged()) {
      setShowReEvalDialog(true);
    } else {
      // Nothing changed — just exit edit mode silently
      setIsEditing(false);
      setNewRule("");
    }
  }

  const saveMutation = useMutation({
    mutationFn: async (reEvaluate: boolean) => {
      await apiClient.prompts.updatePromptCriteriaApiV1PromptsPromptIdCriteriaPut({
        promptId,
        updateCriteriaRequest: {
          evaluationCriteria: pendingSaveRef.current,
          reEvaluate,
        },
      });
    },
    onSuccess: (_, reEvaluate) => {
      setCriteriaMap(pendingSaveRef.current);
      setIsEditing(false);
      setShowReEvalDialog(false);
      setNewRule("");
      if (reEvaluate) {
        toast.success("Criteria saved. Re-evaluation of recent spans has started.");
      } else {
        toast.success("Criteria saved successfully.");
      }
    },
    onError: (err: Error) => {
      toast.error(err.message ?? "Failed to save criteria.");
    },
  });

  const hasMultipleMetrics = metrics.length > 1;

  return (
    <>
      <Card className="border-border flex max-h-[360px] flex-col">
        <CardHeader className="pb-2 shrink-0">
          <div className="flex items-center justify-between gap-2">
            <div className="flex items-center gap-1 min-w-0">
              {hasMultipleMetrics && (
                <button
                  className="p-0.5 rounded hover:bg-muted transition-colors disabled:opacity-40"
                  disabled={isEditing || metrics.length <= 1}
                  onClick={goToPrev}
                  type="button"
                >
                  <ChevronLeft className="size-4 text-muted-foreground" />
                </button>
              )}
              <div className="min-w-0 px-1">
                <p className="text-[10px] font-semibold uppercase tracking-widest text-muted-foreground leading-none mb-0.5">
                  Evaluation Criteria
                </p>
                <div className="flex items-baseline gap-1.5">
                  <h3 className="text-sm font-semibold truncate">{capitalize(currentMetric)}</h3>
                  {hasMultipleMetrics && (
                    <span className="text-xs text-muted-foreground shrink-0">
                      {metricIndex + 1}/{metrics.length}
                    </span>
                  )}
                </div>
              </div>
              {hasMultipleMetrics && (
                <button
                  className="p-0.5 rounded hover:bg-muted transition-colors disabled:opacity-40"
                  disabled={isEditing || metrics.length <= 1}
                  onClick={goToNext}
                  type="button"
                >
                  <ChevronRight className="size-4 text-muted-foreground" />
                </button>
              )}
            </div>

            {!isEditing ? (
              <Button
                className="shrink-0 h-7 px-2 text-xs"
                disabled={isLoading || !!fetchError}
                onClick={startEditing}
                size="sm"
                variant="ghost"
              >
                <Pencil className="size-3" />
                Edit
              </Button>
            ) : (
              <Button
                className="shrink-0 h-7 px-2 text-xs"
                onClick={cancelEditing}
                size="sm"
                variant="ghost"
              >
                <X className="size-3" />
                Cancel
              </Button>
            )}
          </div>
        </CardHeader>

        <CardContent className="flex-1 overflow-y-auto pt-0">
          {isLoading ? (
            <div className="flex items-center gap-2 py-4 text-sm text-muted-foreground">
              <Loader2 className="size-4 animate-spin" />
              Loading…
            </div>
          ) : fetchError ? (
            <p className="text-sm text-destructive py-2">{fetchError}</p>
          ) : !isEditing ? (
            currentRules.length === 0 ? (
              <p className="text-sm italic text-muted-foreground py-2">
                No rules yet. Click Edit to add some.
              </p>
            ) : (
              <ol className="space-y-2.5 mt-1">
                {currentRules.map((rule, idx) => (
                  <li className="flex items-start gap-2 text-sm" key={idx}>
                    <span className="mt-0.5 shrink-0 text-[11px] font-semibold text-muted-foreground w-4">
                      {idx + 1}.
                    </span>
                    <span className="leading-snug text-foreground/90">{rule}</span>
                  </li>
                ))}
              </ol>
            )
          ) : (
            <div className="space-y-2 mt-1">
              {editRules.map((rule, idx) => (
                <div className="flex items-center gap-1.5" key={idx}>
                  <span className="shrink-0 text-[11px] font-semibold text-muted-foreground w-4">
                    {idx + 1}.
                  </span>
                  <Input
                    className="flex-1 h-8 text-sm"
                    onChange={(e) => updateRule(idx, e.target.value)}
                    value={rule}
                  />
                  <button
                    className="shrink-0 p-1 rounded hover:bg-muted transition-colors"
                    onClick={() => removeRule(idx)}
                    title="Remove"
                    type="button"
                  >
                    <Trash2 className="size-3.5 text-muted-foreground hover:text-destructive" />
                  </button>
                </div>
              ))}

              {editRules.length < MAX_RULES ? (
                <div className="flex gap-1.5 pt-1">
                  <Input
                    className="flex-1 h-8 text-sm"
                    onChange={(e) => setNewRule(e.target.value)}
                    onKeyDown={(e) => {
                      if (e.key === "Enter") {
                        e.preventDefault();
                        addRule();
                      }
                    }}
                    placeholder="New rule… (Enter to add)"
                    value={newRule}
                  />
                  <Button
                    className="h-8 px-2"
                    disabled={!newRule.trim()}
                    onClick={addRule}
                    size="sm"
                    variant="outline"
                  >
                    <Plus className="size-3.5" />
                  </Button>
                </div>
              ) : (
                <p className="text-xs text-muted-foreground pt-1">Max {MAX_RULES} rules reached.</p>
              )}

              <div className="flex items-center justify-between pt-2 border-t border-border">
                <span className="text-xs text-muted-foreground">
                  {editRules.length}/{MAX_RULES} rules
                </span>
                <Button
                  className="h-7 px-3 text-xs"
                  disabled={saveMutation.isPending}
                  onClick={handleSaveClick}
                  size="sm"
                >
                  Save
                </Button>
              </div>
            </div>
          )}
        </CardContent>
      </Card>

      <Dialog onOpenChange={(open) => !open && setShowReEvalDialog(false)} open={showReEvalDialog}>
        <DialogContent className="max-w-md">
          <DialogHeader>
            <DialogTitle>Re-evaluate recent spans?</DialogTitle>
            <DialogDescription>
              Your criteria changes have been saved. Would you like to re-evaluate the most recent
              spans using the updated criteria? This will re-score the last 50 spans in the
              background.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter className="flex-col-reverse gap-2 sm:flex-row sm:justify-end">
            <Button
              disabled={saveMutation.isPending}
              onClick={() => saveMutation.mutate(false)}
              variant="outline"
            >
              {saveMutation.isPending && saveMutation.variables === false && (
                <Loader2 className="size-4 animate-spin" />
              )}
              No, just save
            </Button>
            <Button disabled={saveMutation.isPending} onClick={() => saveMutation.mutate(true)}>
              {saveMutation.isPending && saveMutation.variables === true && (
                <Loader2 className="size-4 animate-spin" />
              )}
              Yes, re-evaluate
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}
