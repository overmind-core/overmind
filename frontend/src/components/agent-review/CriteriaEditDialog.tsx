import type React from "react";
import { useState } from "react";

import { diffWordsWithSpace } from "diff";
import { Loader as Loader2, Plus, Redo, Sparkles, Cancel as Trash2 } from "pixelarticons/react";
import { toast } from "sonner";

import apiClient from "@/client";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { cn } from "@/lib/utils";

type RuleEntry = { id: string; value: string };

function makeEntry(value: string): RuleEntry {
  return { id: crypto.randomUUID(), value };
}

interface Props {
  isOpen: boolean;
  onClose: () => void;
  /** The criteria as it exists in the DB — diff always compares against this */
  savedCriteria: Record<string, string[]>;
  /** Which metric the user was viewing when they clicked Edit */
  currentMetric: string;
  promptId: string;
  /** Called when user confirms save — caller handles the actual PUT + re-eval dialog */
  onSave: (newCriteria: Record<string, string[]>) => void;
}

// ─── Diff panel ──────────────────────────────────────────────────────────────

/**
 * Computes a side-by-side, rule-by-rule diff with word-level highlighting.
 *
 * Each rule in the old list is paired with the corresponding rule in the new
 * list. Deleted rules show as a full red row; added rules as a full green row;
 * changed rules show both the old (red, word-level strikethrough) and new
 * (green, word-level highlight) versions.
 */
function LiveDiff({
  savedRules,
  workingRules,
}: {
  /** Rules for the primary metric only — other metrics are unaffected by this edit */
  savedRules: string[];
  workingRules: RuleEntry[];
}) {
  const oldRules = savedRules;
  const newRules = workingRules.map((r) => r.value).filter(Boolean);

  const maxLen = Math.max(oldRules.length, newRules.length);
  const hasAnyChange =
    oldRules.length !== newRules.length ||
    oldRules.some((r, i) => r.trim() !== (newRules[i] ?? "").trim());

  if (!hasAnyChange) {
    return (
      <div className="flex h-full items-center justify-center text-sm text-muted-foreground">
        No changes yet.
      </div>
    );
  }

  type RowKind = "unchanged" | "removed" | "added" | "changed-old" | "changed-new";

  const rows: { kind: RowKind; idx: number; content: React.ReactNode }[] = [];

  for (let i = 0; i < maxLen; i++) {
    const oldRule = oldRules[i] ?? null;
    const newRule = newRules[i] ?? null;

    if (oldRule === null && newRule !== null) {
      // purely added
      rows.push({ content: newRule, idx: i, kind: "added" });
    } else if (newRule === null && oldRule !== null) {
      // purely removed
      rows.push({ content: oldRule, idx: i, kind: "removed" });
    } else if (oldRule === newRule) {
      // unchanged
      rows.push({ content: oldRule, idx: i, kind: "unchanged" });
    } else {
      // changed — show old then new with word-level diff
      const hunks = diffWordsWithSpace(oldRule!, newRule!);

      const oldSpans = hunks
        .filter((h) => !h.added)
        .map((h, j) => (
          <span
            className={cn(h.removed && "bg-red-500/20 text-red-700 dark:text-red-300 line-through")}
            key={j}
          >
            {h.value}
          </span>
        ));

      const newSpans = hunks
        .filter((h) => !h.removed)
        .map((h, j) => (
          <span
            className={cn(h.added && "bg-green-500/20 text-green-700 dark:text-green-300")}
            key={j}
          >
            {h.value}
          </span>
        ));

      rows.push({ content: oldSpans, idx: i, kind: "changed-old" });
      rows.push({ content: newSpans, idx: i, kind: "changed-new" });
    }
  }

  const addedCount = rows.filter((r) => r.kind === "added").length;
  const removedCount = rows.filter((r) => r.kind === "removed").length;
  // Each modified rule produces one changed-old + one changed-new row; count pairs.
  const modifiedCount = rows.filter((r) => r.kind === "changed-new").length;

  return (
    <div className="flex h-full flex-col overflow-hidden border border-border text-sm">
      <div className="flex shrink-0 items-center gap-3 border-b border-border bg-muted/30 px-3 py-1.5 text-[11px] text-muted-foreground">
        {addedCount > 0 && (
          <span className="text-green-600 dark:text-green-400">+{addedCount} added</span>
        )}
        {modifiedCount > 0 && (
          <span className="text-blue-600 dark:text-blue-400">~{modifiedCount} modified</span>
        )}
        {removedCount > 0 && (
          <span className="text-red-500 dark:text-red-400">−{removedCount} removed</span>
        )}
      </div>
      <div className="flex-1 overflow-y-auto">
        {rows.map((row, i) => (
          <div
            className={cn(
              "flex gap-2 px-3 py-2 leading-snug border-b border-border/40 last:border-0",
              row.kind === "unchanged" && "text-foreground/60",
              (row.kind === "removed" || row.kind === "changed-old") &&
                "bg-red-500/8 dark:bg-red-400/8",
              (row.kind === "added" || row.kind === "changed-new") &&
                "bg-green-500/8 dark:bg-green-400/8"
            )}
            key={i}
          >
            <span
              className={cn(
                "shrink-0 w-4 font-bold select-none",
                row.kind === "unchanged" && "text-muted-foreground/30",
                (row.kind === "removed" || row.kind === "changed-old") &&
                  "text-red-500 dark:text-red-400",
                (row.kind === "added" || row.kind === "changed-new") &&
                  "text-green-600 dark:text-green-400"
              )}
            >
              {row.kind === "removed" || row.kind === "changed-old"
                ? "−"
                : row.kind === "added" || row.kind === "changed-new"
                  ? "+"
                  : " "}
            </span>
            <span className="flex-1 break-words">{row.content}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

// ─── Main dialog ─────────────────────────────────────────────────────────────

export function CriteriaEditDialog({
  isOpen,
  onClose,
  savedCriteria,
  currentMetric,
  promptId,
  onSave,
}: Props) {
  // This dialog edits one metric at a time — the one the user was viewing.
  // Other metrics are preserved untouched on save — see handleSave.
  // The parent passes key={String(isOpen)} so the component remounts on each open,
  // guaranteeing workingRules is freshly initialised from savedCriteria every time.
  const primaryMetric =
    currentMetric in savedCriteria
      ? currentMetric
      : (Object.keys(savedCriteria)[0] ?? "correctness");
  const savedRules = savedCriteria[primaryMetric] ?? [];

  const [workingRules, setWorkingRules] = useState<RuleEntry[]>(() => savedRules.map(makeEntry));
  const [newRule, setNewRule] = useState("");

  // AI state
  const [aiInstructions, setAiInstructions] = useState("");
  const [isGeneratingAi, setIsGeneratingAi] = useState(false);
  const [instructionHistory, setInstructionHistory] = useState<string[]>([]);

  function handleClose() {
    setInstructionHistory([]);
    onClose();
  }

  // ── Rule editing ──

  function updateRule(id: string, value: string) {
    setWorkingRules((prev) => prev.map((r) => (r.id === id ? { ...r, value } : r)));
  }

  function removeRule(id: string) {
    setWorkingRules((prev) => prev.filter((r) => r.id !== id));
  }

  function addRule() {
    const trimmed = newRule.trim();
    if (!trimmed) return;
    setWorkingRules((prev) => [...prev, makeEntry(trimmed)]);
    setNewRule("");
  }

  function handleRestore() {
    setWorkingRules(savedRules.map(makeEntry));
    setNewRule("");
    setInstructionHistory([]);
    setAiInstructions("");
  }

  // ── AI update ──

  async function handleAiGenerate() {
    const trimmed = aiInstructions.trim();
    if (!trimmed) return;

    // Keep the last 5 instructions to bound prompt size. If the combined string
    // still exceeds 1800 chars, drop the oldest full entry one at a time until it
    // fits — avoids truncating mid-word/sentence unlike a raw slice(-1800).
    const updatedHistory = [...instructionHistory, trimmed].slice(-5);
    let trimmedHistory = updatedHistory;
    while (trimmedHistory.length > 1 && trimmedHistory.join("\n\nFollow-up: ").length > 1800) {
      trimmedHistory = trimmedHistory.slice(1);
    }
    const combinedInstructions = trimmedHistory.join("\n\nFollow-up: ");

    setIsGeneratingAi(true);
    try {
      const result =
        await apiClient.prompts.suggestPromptCriteriaApiV1PromptsPromptIdCriteriaSuggestPost({
          promptId,
          suggestCriteriaRequest: {
            // Only send the primary metric — the backend ignores other metrics
            // and sending them wastes request payload.
            currentCriteria: {
              [primaryMetric]: workingRules.map((r) => r.value).filter(Boolean),
            },
            userInstructions: combinedInstructions,
          },
        });

      const suggested = result.suggestedCriteria as Record<string, string[]>;
      setWorkingRules((suggested[primaryMetric] ?? []).slice(0, 5).map(makeEntry));
      setInstructionHistory(updatedHistory);
      setAiInstructions("");
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to generate criteria.");
    } finally {
      setIsGeneratingAi(false);
    }
  }

  // ── Save ──

  function handleSave() {
    // Preserve all metrics from savedCriteria; only replace the primary one being edited.
    onSave({ ...savedCriteria, [primaryMetric]: workingRules.map((r) => r.value).filter(Boolean) });
    handleClose();
  }

  const hasChanges = (() => {
    const normalize = (r: string) => r.trim();
    const a = savedRules.map(normalize);
    const b = workingRules
      .map((r) => r.value)
      .filter(Boolean)
      .map(normalize);
    // Positional comparison — order, content, and case changes all count
    return a.length !== b.length || a.some((v, i) => v !== b[i]);
  })();

  return (
    <Dialog onOpenChange={(open) => !open && !isGeneratingAi && handleClose()} open={isOpen}>
      <DialogContent className="w-[90vw] max-w-[90vw] sm:max-w-[90vw] p-0 gap-0 overflow-hidden flex flex-col max-h-[85vh]">
        <DialogHeader className="px-6 pt-6 pb-5 border-b border-border shrink-0">
          <DialogTitle>Edit Evaluation Criteria</DialogTitle>
        </DialogHeader>

        {/* Two-column body — scrollable */}
        <div className="grid grid-cols-2 divide-x divide-border flex-1 min-h-0">
          {/* Left: editable rules */}
          <div className="flex flex-col p-6 overflow-y-auto gap-6">
            <div className="space-y-3">
              <p className="text-[11px] font-semibold uppercase tracking-widest text-muted-foreground">
                Rules
              </p>

              <div className="space-y-2.5">
                {workingRules.map((rule, idx) => (
                  <div className="flex items-center gap-2" key={rule.id}>
                    <span className="shrink-0 text-[11px] font-semibold text-muted-foreground w-5 text-right">
                      {idx + 1}.
                    </span>
                    <Input
                      className="flex-1 h-9 text-sm"
                      disabled={isGeneratingAi}
                      onChange={(e) => updateRule(rule.id, e.target.value)}
                      value={rule.value}
                    />
                    <button
                      className="shrink-0 p-1.5 rounded hover:bg-muted transition-colors disabled:opacity-40"
                      disabled={isGeneratingAi}
                      onClick={() => removeRule(rule.id)}
                      title="Remove"
                      type="button"
                    >
                      <Trash2 className="size-3.5 text-muted-foreground hover:text-destructive" />
                    </button>
                  </div>
                ))}
              </div>

              {workingRules.length < 5 && (
                <div className="flex gap-2 pt-1">
                  <Input
                    className="flex-1 h-9 text-sm"
                    disabled={isGeneratingAi}
                    onChange={(e) => setNewRule(e.target.value)}
                    onKeyDown={(e) => {
                      if (e.key === "Enter") {
                        e.preventDefault();
                        addRule();
                      }
                    }}
                    placeholder="Add a rule… (Enter to add)"
                    value={newRule}
                  />
                  <Button
                    className="h-9 px-3"
                    disabled={isGeneratingAi || !newRule.trim()}
                    onClick={addRule}
                    size="sm"
                    variant="outline"
                  >
                    <Plus className="size-3.5" />
                  </Button>
                </div>
              )}
            </div>

            {/* AI section — separated with clear visual gap */}
            <div className="space-y-3 border-t border-border pt-5">
              <p className="text-[11px] font-semibold uppercase tracking-widest text-muted-foreground flex items-center gap-1.5">
                <Sparkles className="size-3" />
                Update with AI
              </p>
              <Textarea
                className="text-sm resize-none min-h-[80px]"
                disabled={isGeneratingAi}
                onChange={(e) => setAiInstructions(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
                    e.preventDefault();
                    handleAiGenerate();
                  }
                }}
                placeholder={
                  instructionHistory.length === 0
                    ? "Describe how to update the criteria… (Cmd+Enter to run)"
                    : "Add follow-up instructions… (Cmd+Enter to run)"
                }
                value={aiInstructions}
              />
              {/* History is intentionally append-only and bounded to the last 5
                  entries (capped at 1800 chars) so each AI call builds on prior
                  context without exceeding the backend max_length limit. */}
              {instructionHistory.length > 0 && (
                <ol className="space-y-1">
                  {instructionHistory.map((inst, i) => (
                    <li className="text-xs text-muted-foreground leading-snug" key={i}>
                      <span className="font-semibold">{i + 1}.</span> {inst}
                    </li>
                  ))}
                </ol>
              )}
              <Button
                className="w-full h-9 text-sm"
                disabled={isGeneratingAi || !aiInstructions.trim()}
                onClick={handleAiGenerate}
                size="sm"
                variant="outline"
              >
                {isGeneratingAi ? (
                  <Loader2 className="size-4 animate-spin" />
                ) : (
                  <Sparkles className="size-4" />
                )}
                {isGeneratingAi ? "Generating…" : "Generate"}
              </Button>
            </div>
          </div>

          {/* Right: live diff */}
          <div className="flex flex-col p-6 gap-4 overflow-hidden">
            <p className="text-[11px] font-semibold uppercase tracking-widest text-muted-foreground shrink-0">
              Changes vs. saved
            </p>
            <div className="flex-1 overflow-hidden rounded-sm">
              <LiveDiff savedRules={savedRules} workingRules={workingRules} />
            </div>
          </div>
        </div>

        <DialogFooter className="px-6 py-4 border-t border-border flex-row justify-between gap-2 shrink-0">
          <Button
            disabled={isGeneratingAi || !hasChanges}
            onClick={handleRestore}
            title="Restore original saved criteria"
            variant="ghost"
          >
            <Redo className="size-3.5" />
            Restore original
          </Button>
          <div className="flex gap-2">
            <Button disabled={isGeneratingAi} onClick={handleClose} variant="outline">
              Cancel
            </Button>
            <Button disabled={isGeneratingAi || !hasChanges} onClick={handleSave}>
              Save changes
            </Button>
          </div>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
