import { useEffect, useRef, useState } from "react";

import { useMutation } from "@tanstack/react-query";
import { Loader2, Plus, Trash2, X } from "lucide-react";

import type { AgentOut } from "@/api";

function sortedStringArrayEqual(a: string[], b: string[]): boolean {
  if (a.length !== b.length) return false;
  const sa = [...a].sort();
  const sb = [...b].sort();
  return sa.every((v, i) => v === sb[i]);
}
import apiClient from "@/client";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { Badge } from "@/components/ui/badge";

interface Props {
  agent: AgentOut;
  onConfirm: () => void;
  onClose: () => void;
  projectId?: string;
}

export function AgentCriteriaReviewDialog({ agent, onConfirm, onClose, projectId }: Props) {
  const [description, setDescription] = useState("");
  const [criteria, setCriteria] = useState<string[]>([]);
  const [newRule, setNewRule] = useState("");
  const [isLoading, setIsLoading] = useState(true);
  const [fetchError, setFetchError] = useState<string | null>(null);

  const originalCriteriaRef = useRef<string[]>([]);

  useEffect(() => {
    setIsLoading(true);
    setFetchError(null);
    apiClient.agentReviews
      .getSpansForReviewApiV1AgentReviewsPromptSlugReviewSpansGet({
        promptSlug: agent.slug,
        projectId: projectId,
      })
      .then((e) => {
        setDescription(e.agentDescription ?? "");
        const rules = e.evaluationCriteria?.correctness ?? [];
        setCriteria(rules);
        originalCriteriaRef.current = rules;
      })
      .catch((err: Error) => setFetchError(err.message))
      .finally(() => setIsLoading(false));
  }, [agent.slug]);

  const updateMutation = useMutation({
    mutationFn: async () => {
      const criteriaPayload = { correctness: criteria };
      const criteriaChanged = !sortedStringArrayEqual(criteria, originalCriteriaRef.current);

      await apiClient.agentReviews.updateAgentDescriptionAndCriteriaApiV1AgentReviewsPromptSlugUpdateDescriptionPost(
        {
          promptSlug: agent.slug,
          agentDescriptionUpdateRequest: {
            description: description,
            criteria: criteriaPayload,
          },
          projectId: projectId,
        }
      );

      if (criteriaChanged) {
        await apiClient.prompts.updatePromptCriteriaApiV1PromptsPromptIdCriteriaPut({
          promptId: agent.promptId,
          updateCriteriaRequest: {
            evaluationCriteria: criteriaPayload,
            reEvaluate: true,
          },
        });
      }
    },
    onSuccess: () => {
      onConfirm();
    },
  });

  const MAX_RULES = 5;

  function addRule() {
    const trimmed = newRule.trim();
    if (!trimmed || criteria.length >= MAX_RULES) return;
    setCriteria((prev) => [...prev, trimmed]);
    setNewRule("");
  }

  function removeRule(idx: number) {
    setCriteria((prev) => prev.filter((_, i) => i !== idx));
  }

  function updateRule(idx: number, value: string) {
    setCriteria((prev) => prev.map((r, i) => (i === idx ? value : r)));
  }

  return (
    <Dialog open>
      <DialogContent
        className="flex max-h-[90vh] w-full max-w-2xl flex-col gap-0 overflow-hidden p-0"
        onInteractOutside={(e) => e.preventDefault()}
        onEscapeKeyDown={(e) => e.preventDefault()}
      >
        <DialogHeader className="shrink-0 border-b border-border px-6 py-4">
          <div className="flex items-center justify-between gap-2">
            <div className="flex items-center gap-2">
              <DialogTitle className="text-lg font-semibold">
                Review Agent: {agent.name}
              </DialogTitle>
              <Badge className="text-xs" variant="secondary">
                v{agent.version}
              </Badge>
            </div>
            <Button
              className="shrink-0 text-muted-foreground hover:text-foreground"
              onClick={onClose}
              size="icon"
              variant="ghost"
            >
              <X className="size-4" />
            </Button>
          </div>
          <DialogDescription>
            Review and refine the generated description and evaluation criteria before scoring.
          </DialogDescription>
        </DialogHeader>

        <div className="flex-1 overflow-y-auto px-6 py-4">
          {isLoading ? (
            <div className="flex items-center justify-center py-12">
              <Loader2 className="size-6 animate-spin text-muted-foreground" />
              <span className="ml-2 text-sm text-muted-foreground">Loading agent data…</span>
            </div>
          ) : fetchError ? (
            <p className="rounded-md border border-destructive/30 bg-destructive/10 px-4 py-3 text-sm text-destructive">
              {fetchError}
            </p>
          ) : (
            <div className="space-y-6">
              {/* Description */}
              <div className="space-y-2">
                <Label className="text-sm font-medium" htmlFor="agent-description">
                  Agent Description
                </Label>
                <Textarea
                  className="min-h-[120px] resize-y text-sm"
                  id="agent-description"
                  onChange={(e) => setDescription(e.target.value)}
                  placeholder="Describe what this agent does…"
                  value={description}
                />
              </div>

              {/* Criteria */}
              <div className="space-y-3">
                <Label className="text-sm font-medium">
                  Evaluation Criteria
                  <span className="ml-1.5 text-xs font-normal text-muted-foreground">
                    ({criteria.length} / {MAX_RULES} rules)
                  </span>
                </Label>

                <div className="space-y-2">
                  {criteria.map((rule, idx) => (
                    <div className="flex items-start gap-2" key={idx}>
                      <div className="mt-2.5 shrink-0 text-xs font-medium text-muted-foreground">
                        {idx + 1}.
                      </div>
                      <Input
                        className="flex-1 text-sm"
                        onChange={(e) => updateRule(idx, e.target.value)}
                        value={rule}
                      />
                      <Button
                        className="mt-0.5 shrink-0"
                        onClick={() => removeRule(idx)}
                        size="icon"
                        title="Remove rule"
                        variant="ghost"
                      >
                        <Trash2 className="size-4 text-muted-foreground hover:text-destructive" />
                      </Button>
                    </div>
                  ))}
                </div>

                {/* Add new rule */}
                {criteria.length >= MAX_RULES ? (
                  <p className="text-xs text-muted-foreground">
                    Maximum of {MAX_RULES} rules reached. Remove one to add another.
                  </p>
                ) : (
                  <div className="flex gap-2">
                    <Input
                      className="flex-1 text-sm"
                      onKeyDown={(e) => {
                        if (e.key === "Enter") {
                          e.preventDefault();
                          addRule();
                        }
                      }}
                      onChange={(e) => setNewRule(e.target.value)}
                      placeholder="Add a new criterion and press Enter…"
                      value={newRule}
                    />
                    <Button
                      disabled={!newRule.trim()}
                      onClick={addRule}
                      size="sm"
                      variant="outline"
                    >
                      <Plus className="size-4" />
                      Add
                    </Button>
                  </div>
                )}
              </div>
            </div>
          )}
        </div>

        <DialogFooter className="shrink-0 border-t border-border px-6 py-4">
          {updateMutation.isError && (
            <p className="mr-auto text-xs text-destructive">
              {(updateMutation.error as Error).message}
            </p>
          )}
          <Button
            disabled={isLoading || !!fetchError || updateMutation.isPending || !description.trim()}
            onClick={() => updateMutation.mutate()}
          >
            {updateMutation.isPending ? (
              <>
                <Loader2 className="size-4 animate-spin" />
                Saving…
              </>
            ) : (
              "Confirm & Review Spans"
            )}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
