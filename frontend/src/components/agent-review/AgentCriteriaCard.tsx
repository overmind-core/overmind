import { useEffect, useRef, useState } from "react";

import { useMutation } from "@tanstack/react-query";
import {
  ChevronLeft,
  ChevronRight,
  Loader as Loader2,
  PenSquare as Pencil,
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
import { CriteriaEditDialog } from "./CriteriaEditDialog";

interface Props {
  promptId: string;
  projectId?: string;
}

function capitalize(s: string) {
  return s.charAt(0).toUpperCase() + s.slice(1);
}

export function AgentCriteriaCard({ promptId, projectId }: Props) {
  const [criteriaMap, setCriteriaMap] = useState<Record<string, string[]>>({});
  const [metricIndex, setMetricIndex] = useState(0);
  const [isLoading, setIsLoading] = useState(true);
  const [fetchError, setFetchError] = useState<string | null>(null);

  const [showEditDialog, setShowEditDialog] = useState(false);

  // Re-eval dialog
  const [showReEvalDialog, setShowReEvalDialog] = useState(false);
  const pendingSaveRef = useRef<Record<string, string[]>>({});

  useEffect(() => {
    setIsLoading(true);
    setFetchError(null);
    apiClient.prompts
      .getPromptCriteriaApiV1PromptsPromptIdCriteriaGet({ promptId })
      .then((e) => {
        setCriteriaMap(e.evaluationCriteria ?? {});
        setMetricIndex(0);
      })
      .catch((err: Error) => setFetchError(err.message))
      .finally(() => setIsLoading(false));
  }, [promptId]);

  const metrics = Object.keys(criteriaMap);
  const currentMetric = metrics[metricIndex] ?? "correctness";
  const currentRules = criteriaMap[currentMetric] ?? [];
  const hasMultipleMetrics = metrics.length > 1;

  function goToPrev() {
    setMetricIndex((i) => (i - 1 + metrics.length) % metrics.length);
  }

  function goToNext() {
    setMetricIndex((i) => (i + 1) % metrics.length);
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
    onError: (err: Error) => {
      toast.error(err.message ?? "Failed to save criteria.");
    },
    onSuccess: (_, reEvaluate) => {
      setCriteriaMap(pendingSaveRef.current);
      setShowReEvalDialog(false);
      if (reEvaluate) {
        toast.success("Criteria saved. Re-evaluation of recent spans has started.");
      } else {
        toast.success("Criteria saved successfully.");
      }
    },
  });

  function handleSaveFromDialog(newCriteria: Record<string, string[]>) {
    pendingSaveRef.current = newCriteria;
    setShowReEvalDialog(true);
  }

  return (
    <>
      <Card className="border-border flex max-h-[360px] flex-col">
        <CardHeader className="pb-2 shrink-0">
          <div className="flex items-center justify-between gap-2">
            <div className="flex items-center gap-1 min-w-0">
              {hasMultipleMetrics && (
                <button
                  className="p-0.5 rounded hover:bg-muted transition-colors disabled:opacity-40"
                  disabled={metrics.length <= 1}
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
                  disabled={metrics.length <= 1}
                  onClick={goToNext}
                  type="button"
                >
                  <ChevronRight className="size-4 text-muted-foreground" />
                </button>
              )}
            </div>

            <Button
              className="shrink-0 h-7 px-2 text-xs"
              disabled={isLoading || !!fetchError}
              onClick={() => setShowEditDialog(true)}
              size="sm"
              variant="ghost"
            >
              <Pencil className="size-3" />
              Edit
            </Button>
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
          ) : currentRules.length === 0 ? (
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
          )}
        </CardContent>
      </Card>

      {showEditDialog && (
        <CriteriaEditDialog
          isOpen={showEditDialog}
          onClose={() => setShowEditDialog(false)}
          onSave={handleSaveFromDialog}
          promptId={promptId}
          savedCriteria={criteriaMap}
        />
      )}

      <Dialog onOpenChange={(open) => !open && setShowReEvalDialog(false)} open={showReEvalDialog}>
        <DialogContent className="max-w-md">
          <DialogHeader>
            <DialogTitle>Re-evaluate recent spans?</DialogTitle>
            <DialogDescription>
              Save your criteria changes and optionally re-evaluate the most recent spans. This will
              re-score the last 50 spans in the background using the updated criteria.
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
