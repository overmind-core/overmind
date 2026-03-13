import { useState } from "react";

import { diffLines } from "diff";
import { Loader as Loader2, Sparkles } from "pixelarticons/react";
import { toast } from "sonner";

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
import { Textarea } from "@/components/ui/textarea";
import { cn } from "@/lib/utils";

interface Props {
  isOpen: boolean;
  onClose: () => void;
  /** The criteria that was saved before this session started — diff always compares against this */
  originalCriteria: Record<string, string[]>;
  /** The latest LLM-suggested criteria */
  initialSuggestedCriteria: Record<string, string[]>;
  promptId: string;
  /** Called when user clicks Accept — the caller is responsible for saving */
  onAccepted: (newCriteria: Record<string, string[]>) => void;
}

function criteriaToText(criteria: Record<string, string[]>): string {
  return Object.entries(criteria)
    .flatMap(([, rules]) => rules)
    .join("\n");
}

type DiffRow = {
  kind: "added" | "removed" | "context";
  text: string;
};

function CriteriaDiff({
  originalCriteria,
  suggestedCriteria,
}: {
  originalCriteria: Record<string, string[]>;
  suggestedCriteria: Record<string, string[]>;
}) {
  const oldText = criteriaToText(originalCriteria);
  const newText = criteriaToText(suggestedCriteria);

  const hunks = diffLines(oldText, newText, { newlineIsToken: false });

  const addedCount = hunks.filter((h) => h.added).reduce((n, h) => n + (h.count ?? 0), 0);
  const removedCount = hunks.filter((h) => h.removed).reduce((n, h) => n + (h.count ?? 0), 0);

  const rows: DiffRow[] = [];
  for (const hunk of hunks) {
    const lines = hunk.value.replace(/\n$/, "").split("\n");
    for (const line of lines) {
      if (hunk.added) {
        rows.push({ kind: "added", text: line });
      } else if (hunk.removed) {
        rows.push({ kind: "removed", text: line });
      } else {
        rows.push({ kind: "context", text: line });
      }
    }
  }

  if (addedCount === 0 && removedCount === 0) {
    return (
      <div className="border border-border bg-muted/20 p-4 text-center text-xs text-muted-foreground">
        No changes from current criteria.
      </div>
    );
  }

  return (
    <div className="overflow-hidden border border-border font-mono text-xs">
      <div className="flex items-center gap-3 border-b border-border bg-muted/30 px-3 py-1.5 text-[11px] text-muted-foreground">
        <span className="text-green-600 dark:text-green-400">+{addedCount} added</span>
        <span className="text-red-500 dark:text-red-400">−{removedCount} removed</span>
      </div>
      <div className="max-h-[280px] overflow-y-auto">
        <table className="w-full border-collapse">
          <tbody>
            {rows.map((row, i) => (
              <tr
                className={cn(
                  row.kind === "added" && "bg-green-500/10 dark:bg-green-400/10",
                  row.kind === "removed" && "bg-red-500/10 dark:bg-red-400/10"
                )}
                key={i}
              >
                <td
                  className={cn(
                    "w-5 select-none py-0.5 pl-2 pr-1 font-bold",
                    row.kind === "added" && "text-green-600 dark:text-green-400",
                    row.kind === "removed" && "text-red-500 dark:text-red-400",
                    row.kind === "context" && "text-muted-foreground/40"
                  )}
                >
                  {row.kind === "added" ? "+" : row.kind === "removed" ? "−" : " "}
                </td>
                <td
                  className={cn(
                    "py-0.5 pl-1 pr-3 whitespace-pre-wrap break-all",
                    row.kind === "added" && "text-green-800 dark:text-green-200",
                    row.kind === "removed" && "text-red-800 dark:text-red-200",
                    row.kind === "context" && "text-foreground/80"
                  )}
                >
                  {row.text}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

export function CriteriaDiffDialog({
  isOpen,
  onClose,
  originalCriteria,
  initialSuggestedCriteria,
  promptId,
  onAccepted,
}: Props) {
  const [suggestedCriteria, setSuggestedCriteria] = useState(initialSuggestedCriteria);
  // Each entry is one round of instructions; appended on every regenerate
  const [instructionHistory, setInstructionHistory] = useState<string[]>([]);
  const [currentInstructions, setCurrentInstructions] = useState("");
  const [isRegenerating, setIsRegenerating] = useState(false);

  function handleClose() {
    // Clear accumulated instruction history when the dialog is dismissed
    setInstructionHistory([]);
    setCurrentInstructions("");
    onClose();
  }

  async function handleRegenerate() {
    const trimmed = currentInstructions.trim();
    if (!trimmed) return;

    // Append the new instructions to the history and send the full accumulated context
    const updatedHistory = [...instructionHistory, trimmed];
    const combinedInstructions = updatedHistory.join("\n\nFollow-up: ");

    setIsRegenerating(true);
    try {
      const result =
        await apiClient.prompts.suggestPromptCriteriaApiV1PromptsPromptIdCriteriaSuggestPost({
          promptId,
          suggestCriteriaRequest: { userInstructions: combinedInstructions },
        });
      setSuggestedCriteria(result.suggestedCriteria as Record<string, string[]>);
      setInstructionHistory(updatedHistory);
      setCurrentInstructions("");
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to regenerate criteria.");
    } finally {
      setIsRegenerating(false);
    }
  }

  function handleAccept() {
    onAccepted(suggestedCriteria);
    handleClose();
  }

  return (
    <Dialog onOpenChange={(open) => !open && !isRegenerating && handleClose()} open={isOpen}>
      <DialogContent className="max-w-2xl">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <Sparkles className="size-4" />
            AI Criteria Suggestion
          </DialogTitle>
          <DialogDescription>
            Review the suggested changes below. You can refine them with follow-up instructions
            before accepting — each round builds on the previous ones.
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-4">
          <CriteriaDiff originalCriteria={originalCriteria} suggestedCriteria={suggestedCriteria} />

          {instructionHistory.length > 0 && (
            <div className="space-y-1">
              <p className="text-[11px] font-medium uppercase tracking-widest text-muted-foreground">
                Instructions so far
              </p>
              <ol className="space-y-0.5">
                {instructionHistory.map((inst, i) => (
                  <li className="text-xs text-muted-foreground" key={i}>
                    <span className="font-semibold">{i + 1}.</span> {inst}
                  </li>
                ))}
              </ol>
            </div>
          )}

          <div className="space-y-2">
            <p className="text-xs font-medium text-muted-foreground">
              {instructionHistory.length === 0
                ? "Refine with new instructions"
                : "Add follow-up instructions"}
            </p>
            <div className="flex gap-2">
              <Textarea
                className="flex-1 min-h-[72px] text-sm resize-none"
                disabled={isRegenerating}
                onChange={(e) => setCurrentInstructions(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
                    e.preventDefault();
                    handleRegenerate();
                  }
                }}
                placeholder={
                  instructionHistory.length === 0
                    ? "e.g. Add a rule about response length, remove the rule about citations…"
                    : "e.g. Also make the rules more concise…"
                }
                value={currentInstructions}
              />
              <Button
                className="self-end h-9 px-3 shrink-0"
                disabled={isRegenerating || !currentInstructions.trim()}
                onClick={handleRegenerate}
                size="sm"
                variant="outline"
              >
                {isRegenerating ? (
                  <Loader2 className="size-4 animate-spin" />
                ) : (
                  <Sparkles className="size-4" />
                )}
                {isRegenerating ? "Generating…" : "Regenerate"}
              </Button>
            </div>
            <p className="text-[11px] text-muted-foreground">Tip: Press Cmd+Enter to regenerate</p>
          </div>
        </div>

        <DialogFooter className="flex-col-reverse gap-2 sm:flex-row sm:justify-end">
          <Button disabled={isRegenerating} onClick={handleClose} variant="outline">
            Discard
          </Button>
          <Button disabled={isRegenerating} onClick={handleAccept}>
            Accept &amp; Save
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
