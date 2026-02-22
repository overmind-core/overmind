import { useCallback, useEffect, useRef, useState } from "react";

import {
  ThumbsDown,
  ThumbsUp,
  Loader2,
  CheckCircle2,
  AlertCircle,
  ChevronLeft,
  ChevronRight,
  X,
} from "lucide-react";

import type { AgentOut, SpanForReview } from "@/api";
import apiClient from "@/client";
// import { type SpanForReview, agentReviewsApi } from "@/api/agentReviews";
import { Badge } from "@/components/ui/badge";
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

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const MAX_ITERATIONS = 3;
const JOB_POLL_INTERVAL_MS = 2_000;
const JOB_POLL_TIMEOUT_MS = 60_000;

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

type Vote = "up" | "down";
type FeedbackEntry = { vote: Vote; text: string };

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

type ToolCallItem = {
  id?: string;
  function?: { name?: string; arguments?: string };
};

type ChatMessage = {
  role: string;
  content?: string | null;
  tool_calls?: ToolCallItem[];
  tool_call_id?: string;
};

function parseChatMessages(value: unknown): ChatMessage[] | null {
  try {
    const parsed = typeof value === "string" ? JSON.parse(value) : value;
    if (
      Array.isArray(parsed) &&
      parsed.length > 0 &&
      typeof parsed[0] === "object" &&
      "role" in parsed[0]
    ) {
      return parsed as ChatMessage[];
    }
  } catch {
    // not JSON
  }
  return null;
}

function formatPlain(value: unknown): string {
  if (value === null || value === undefined) return "";
  if (typeof value === "string") return value;
  return JSON.stringify(value, null, 2);
}

function ScoreChip({ score }: { score: number | null }) {
  if (score === null) return <span className="text-xs text-muted-foreground">unscored</span>;
  const pct = Math.round(score * 100);
  const color =
    pct >= 70
      ? "bg-emerald-500/15 text-emerald-700 dark:text-emerald-400"
      : pct >= 40
        ? "bg-amber-500/15 text-amber-700"
        : "bg-destructive/15 text-destructive";
  return (
    <span className={cn("rounded-full px-2.5 py-0.5 text-sm font-semibold tabular-nums", color)}>
      {pct}%
    </span>
  );
}

// ---------------------------------------------------------------------------
// ChatBubbles
// ---------------------------------------------------------------------------

function ToolCallBubble({ tc }: { tc: ToolCallItem }) {
  const prettyArgs = (() => {
    if (!tc.function?.arguments) return "";
    try {
      return JSON.stringify(JSON.parse(tc.function.arguments), null, 2);
    } catch {
      return tc.function.arguments;
    }
  })();

  return (
    <div className="rounded-lg border border-border/60 bg-muted/50 px-3 py-2 font-mono text-xs">
      <span className="font-semibold text-foreground">{tc.function?.name ?? "tool"}</span>
      {tc.id && <span className="ml-2 text-muted-foreground/60">({tc.id.slice(0, 12)}…)</span>}
      {prettyArgs && (
        <pre className="mt-1 whitespace-pre-wrap wrap-break-word text-muted-foreground">
          {prettyArgs}
        </pre>
      )}
    </div>
  );
}

function ChatBubbles({ messages }: { messages: ChatMessage[] }) {
  return (
    <div className="space-y-2.5">
      {messages.map((m, i) => {
        const isUser = m.role === "user";
        const isSystem = m.role === "system";
        const isTool = m.role === "tool";
        const hasToolCalls = (m.tool_calls?.length ?? 0) > 0;

        const roleLabel = isTool
          ? `tool${m.tool_call_id ? ` · ${m.tool_call_id.slice(0, 12)}…` : ""}`
          : m.role;

        return (
          <div key={i} className={cn("flex flex-col gap-1", isUser ? "items-end" : "items-start")}>
            <span className="text-[10px] font-semibold uppercase tracking-widest text-muted-foreground">
              {roleLabel}
            </span>
            {/* Tool calls emitted by the assistant */}
            {hasToolCalls && (
              <div className="max-w-[85%] space-y-1.5">
                {m.tool_calls!.map((tc, tcIdx) => (
                  <ToolCallBubble key={tcIdx} tc={tc} />
                ))}
              </div>
            )}
            {/* Regular text content (may coexist with tool_calls in some models) */}
            {m.content != null && m.content !== "" && (
              <div
                className={cn(
                  "max-w-[85%] rounded-xl px-3.5 py-2.5 text-sm leading-relaxed",
                  isUser
                    ? "bg-primary/10 text-foreground"
                    : isSystem
                      ? "bg-muted/50 text-muted-foreground italic"
                      : isTool
                        ? "bg-amber-500/10 font-mono text-xs text-foreground"
                        : "bg-muted text-foreground"
                )}
              >
                {m.content}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}

// ---------------------------------------------------------------------------
// SpanSection
// ---------------------------------------------------------------------------

function SpanSection({ label, value }: { label: string; value: unknown }) {
  const messages = parseChatMessages(value);
  return (
    <div>
      <p className="mb-2 text-[10px] font-semibold uppercase tracking-widest text-muted-foreground">
        {label}
      </p>
      {messages ? (
        <ChatBubbles messages={messages} />
      ) : (
        <pre className="whitespace-pre-wrap wrap-break-word rounded-xl bg-muted/50 px-4 py-3 font-mono text-xs leading-relaxed text-foreground">
          {formatPlain(value)}
        </pre>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main dialog
// ---------------------------------------------------------------------------

type Phase = "loading" | "reviewing" | "refreshing" | "done" | "thanked" | "error";

interface Props {
  agent: AgentOut;
  onComplete: () => void;
  onClose: () => void;
  projectId?: string;
}

export function SpanFeedbackDialog({ agent, onComplete, onClose, projectId }: Props) {
  const [phase, setPhase] = useState<Phase>("loading");
  const [spans, setSpans] = useState<SpanForReview[]>([]);
  // feedback: spanId → { vote, text }
  const [feedback, setFeedback] = useState<Record<string, FeedbackEntry | null>>({});
  // index of the currently visible span
  const [currentIdx, setCurrentIdx] = useState(0);
  // when user clicks thumbs-down, show text input for this span id
  const [pendingDownId, setPendingDownId] = useState<string | null>(null);
  // per-span draft text — persists while navigating between spans
  const [pendingTexts, setPendingTexts] = useState<Record<string, string>>({});
  const [iteration, setIteration] = useState(0);
  const [statusMsg, setStatusMsg] = useState("");
  const [fetchError, setFetchError] = useState<string | null>(null);

  const fixedSpanIdsRef = useRef<string[]>([]);

  const loadSpans = useCallback(async () => {
    setPhase("loading");
    setFetchError(null);
    setPendingDownId(null);
    setPendingTexts({});
    try {
      const data =
        await apiClient.agentReviews.getSpansForReviewApiV1AgentReviewsPromptSlugReviewSpansGet({
          promptSlug: agent.slug,
          projectId: projectId,
        });
      const allSpans = [...data.worstSpans, ...data.bestSpans];
      if (allSpans.length === 0) {
        setFetchError("No scored spans found. Run evaluation first.");
        return;
      }
      setSpans(allSpans);
      setCurrentIdx(0);
      if (fixedSpanIdsRef.current.length === 0) {
        fixedSpanIdsRef.current = allSpans.map((s) => s.spanId);
      }
      const init: Record<string, FeedbackEntry | null> = {};
      allSpans.forEach((s) => {
        init[s.spanId] = null;
      });
      setFeedback(init);
      setPhase("reviewing");
    } catch (err) {
      setFetchError((err as Error).message);
    }
  }, [agent.slug, projectId]);

  useEffect(() => {
    loadSpans();
  }, [loadSpans]);

  // ---- vote helpers --------------------------------------------------------

  function voteUp(spanId: string) {
    if (pendingDownId === spanId) setPendingDownId(null);
    setFeedback((prev) => ({ ...prev, [spanId]: { vote: "up", text: "" } }));
  }

  function startVoteDown(spanId: string) {
    setPendingDownId(spanId);
    // seed draft from confirmed text if re-opening an already-confirmed down vote
    if (feedback[spanId]?.vote === "down" && !pendingTexts[spanId]) {
      setPendingTexts((prev) => ({ ...prev, [spanId]: feedback[spanId]?.text ?? "" }));
    }
  }

  function confirmVoteDown(spanId: string) {
    const text = (pendingTexts[spanId] ?? "").trim();
    if (!text) return;
    setFeedback((prev) => ({ ...prev, [spanId]: { vote: "down", text } }));
    setPendingDownId(null);
  }

  function cancelVoteDown(spanId: string) {
    setPendingDownId(null);
    // clear the draft for this span and revert to unvoted if not already confirmed
    setPendingTexts((prev) => {
      const n = { ...prev };
      delete n[spanId];
      return n;
    });
    setFeedback((prev) => {
      if (prev[spanId]?.vote === "down") return prev; // already confirmed — keep it
      return { ...prev, [spanId]: null };
    });
  }

  // ---- derived state -------------------------------------------------------

  const allVoted = spans.length > 0 && spans.every((s) => feedback[s.spanId] !== null);
  const allPositive = allVoted && spans.every((s) => feedback[s.spanId]?.vote === "up");
  const votedCount = Object.values(feedback).filter((v) => v !== null).length;

  const currentSpan = spans[currentIdx] ?? null;
  const currentVote = currentSpan ? feedback[currentSpan.spanId] : null;
  // When the thumbs-down text input is open, treat the button as already
  // selected even before the user types and hits Confirm.
  const effectiveVote =
    currentSpan && pendingDownId === currentSpan.spanId ? "down" : currentVote?.vote;

  // label for current span (worst / best)
  const worstCount = Math.ceil(spans.length / 2);
  const spanLabel = currentIdx < worstCount ? "Lowest scored" : "Highest scored";

  // ---- navigation ----------------------------------------------------------

  function goTo(idx: number) {
    // auto-confirm if there's pending text, otherwise cancel
    if (pendingDownId) {
      const text = (pendingTexts[pendingDownId] ?? "").trim();
      if (text) {
        setFeedback((prev) => ({ ...prev, [pendingDownId]: { vote: "down", text } }));
      } else {
        setFeedback((prev) => {
          if (prev[pendingDownId]?.vote === "down") return prev;
          return { ...prev, [pendingDownId]: null };
        });
      }
      setPendingDownId(null);
    }
    setCurrentIdx(idx);
  }

  // ---- helpers -------------------------------------------------------------

  async function persistFeedback() {
    await Promise.all(
      spans.map((s) => {
        const entry = feedback[s.spanId]!;
        return apiClient.spans.submitSpanFeedbackApiV1SpansSpanIdFeedbackPatch({
          spanId: s.spanId,
          spanFeedbackRequest: {
            feedbackType: "judge",
            rating: entry.vote,
            text: entry.text || undefined,
          },
        });
      })
    );
  }

  // ---- submit --------------------------------------------------------------

  async function handleConfirm() {
    if (!allVoted) return;

    setPhase("refreshing");

    if (allPositive) {
      // All scores look correct — persist votes then close.
      setStatusMsg("Saving feedback…");
      await persistFeedback();
      setStatusMsg("Completing review…");
      await apiClient.agentReviews.markInitialReviewCompleteApiV1AgentReviewsPromptSlugMarkInitialReviewCompletePost(
        {
          promptSlug: agent.slug,
          projectId: projectId,
        }
      );
      setPhase("done");
      setTimeout(onComplete, 800);
      return;
    }

    if (iteration >= MAX_ITERATIONS - 1) {
      // Final iteration — persist votes then close.
      setStatusMsg("Saving feedback…");
      await persistFeedback();
      setStatusMsg("Completing review…");
      await apiClient.agentReviews.markInitialReviewCompleteApiV1AgentReviewsPromptSlugMarkInitialReviewCompletePost(
        {
          promptSlug: agent.slug,
          projectId: projectId,
        }
      );
      setPhase("thanked");
      return;
    }

    // Intermediate iteration — pass feedback inline to avoid writing stale
    // judge_feedback to the DB before the session is complete.
    const negativeSpans = spans.filter((s) => feedback[s.spanId]?.vote === "down");
    const negativeSpanIds = negativeSpans.map((s) => s.spanId);
    const inlineFeedback = Object.fromEntries(
      negativeSpans.map((s) => [s.spanId, { rating: "down", text: feedback[s.spanId]?.text ?? "" }])
    );

    setStatusMsg("Updating agent description based on feedback…");
    await apiClient.agentReviews.syncRefreshDescriptionApiV1AgentReviewsPromptSlugSyncRefreshDescriptionPost(
      {
        promptSlug: agent.slug,
        syncRefreshDescriptionRequest: {
          spanIds: negativeSpanIds,
          feedback: inlineFeedback,
        },
        projectId: projectId,
      }
    );

    setStatusMsg("Re-scoring spans…");
    const evalResult = await apiClient.spans.evaluateSpansApiV1SpansEvaluatePost({
      requestBody: negativeSpanIds,
    });

    setStatusMsg("Waiting for scores to update…");
    const jobId = (evalResult as { job_id?: string }).job_id;
    if (jobId) {
      const deadline = Date.now() + JOB_POLL_TIMEOUT_MS;
      while (Date.now() < deadline) {
        await new Promise((resolve) => setTimeout(resolve, JOB_POLL_INTERVAL_MS));
        try {
          const job = await apiClient.jobs.getJobApiV1JobsJobIdGet({ jobId });
          if (job.status === "completed" || job.status === "failed") break;
        } catch {
          // ignore transient errors
        }
      }
    }

    setStatusMsg("Loading updated spans…");
    try {
      const refreshed =
        await apiClient.agentReviews.getSpansForReviewApiV1AgentReviewsPromptSlugReviewSpansGet({
          promptSlug: agent.slug,
          projectId: projectId,
        });

      const refreshedAll = [...refreshed.worstSpans, ...refreshed.bestSpans];
      const byId = new Map(refreshedAll.map((s) => [s.spanId, s]));
      const ordered = fixedSpanIdsRef.current.map((id) => byId.get(id) ?? refreshedAll[0]);
      setSpans(ordered.filter(Boolean));
    } catch {
      // keep existing spans
    }

    setIteration((i) => i + 1);
    setCurrentIdx(0);
    const fresh: Record<string, FeedbackEntry | null> = {};
    fixedSpanIdsRef.current.forEach((id) => {
      fresh[id] = null;
    });
    setFeedback(fresh);
    setPendingDownId(null);
    setPendingTexts({});
    setPhase("reviewing");
    setStatusMsg("");
  }

  // ---- render --------------------------------------------------------------

  return (
    <Dialog open>
      <DialogContent
        className="flex max-h-[92vh] w-full max-w-6xl flex-col gap-0 overflow-hidden p-0"
        onInteractOutside={(e) => e.preventDefault()}
        onEscapeKeyDown={(e) => e.preventDefault()}
      >
        {/* Header */}
        <DialogHeader className="shrink-0 border-b border-border px-6 py-4">
          <div className="flex items-center justify-between gap-2">
            <div className="flex items-center gap-2">
              <DialogTitle className="text-base font-semibold">
                Span Review — {agent.name}
              </DialogTitle>
              <Badge className="text-xs" variant="secondary">
                v{agent.version}
              </Badge>
              {iteration > 0 && (
                <Badge className="text-xs" variant="outline">
                  Round {iteration + 1} / {MAX_ITERATIONS}
                </Badge>
              )}
            </div>
            <Button
              className="shrink-0 text-muted-foreground hover:text-foreground"
              disabled={phase === "loading" || phase === "refreshing"}
              onClick={onClose}
              size="icon"
              variant="ghost"
            >
              <X className="size-4" />
            </Button>
          </div>
          <DialogDescription className="text-xs">
            Rate whether each score looks correct. Use the arrows to move between spans.
          </DialogDescription>
        </DialogHeader>

        {/* Body */}
        <div className="flex-1 overflow-y-auto px-6 py-5">
          {/* Loading / refreshing */}
          {(phase === "loading" || phase === "refreshing") && (
            <div className="flex flex-col items-center justify-center gap-3 py-24">
              <Loader2 className="size-8 animate-spin text-muted-foreground" />
              <p className="text-sm text-muted-foreground">{statusMsg || "Loading spans…"}</p>
            </div>
          )}

          {/* Done */}
          {phase === "done" && (
            <div className="flex flex-col items-center justify-center gap-3 py-24">
              <CheckCircle2 className="size-10 text-emerald-500" />
              <p className="text-base font-semibold">Review complete!</p>
              <p className="text-sm text-muted-foreground">
                Agent description and criteria have been confirmed.
              </p>
            </div>
          )}

          {/* Thanked (final round complete) */}
          {phase === "thanked" && (
            <div className="flex flex-col items-center justify-center gap-3 py-24">
              <CheckCircle2 className="size-10 text-emerald-500" />
              <p className="text-base font-semibold">Thanks for the review!</p>
              <p className="max-w-sm text-center text-sm text-muted-foreground">
                We've captured your feedback and will update your agent shortly.
              </p>
              <Button className="mt-2" onClick={onComplete} size="sm" variant="outline">
                Close
              </Button>
            </div>
          )}

          {/* Error */}
          {phase === "error" && (
            <div className="flex flex-col items-center justify-center gap-3 py-24">
              <AlertCircle className="size-10 text-amber-500" />
              <p className="text-base font-semibold">Something went wrong</p>
              <p className="max-w-sm text-center text-sm text-muted-foreground">
                An unexpected error occurred during the review. You can try again from the agent
                detail page.
              </p>
              <Button className="mt-2" onClick={onComplete} size="sm" variant="outline">
                Close
              </Button>
            </div>
          )}

          {/* Fetch error */}
          {fetchError && (
            <p className="rounded-lg border border-destructive/30 bg-destructive/10 px-4 py-3 text-sm text-destructive">
              {fetchError}
            </p>
          )}

          {/* Reviewing — single span carousel */}
          {phase === "reviewing" && currentSpan && (
            <div className="space-y-4">
              {iteration > 0 && (
                <div className="rounded-lg border border-amber-400/40 bg-amber-400/10 px-4 py-3 text-sm text-amber-700">
                  Description updated based on your feedback — spans have been re-scored. Please
                  review again.
                </div>
              )}

              {/* Carousel nav bar */}
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-2">
                  <span className="text-xs font-semibold uppercase tracking-widest text-muted-foreground">
                    {spanLabel}
                  </span>
                  <span className="text-xs text-muted-foreground">
                    {currentIdx + 1} / {spans.length}
                  </span>
                </div>

                {/* Dot indicators */}
                <div className="flex items-center gap-1.5">
                  {spans.map((s, i) => {
                    const entry = feedback[s.spanId];
                    return (
                      <button
                        key={s.spanId}
                        onClick={() => goTo(i)}
                        title={`Span ${i + 1}`}
                        type="button"
                        className={cn(
                          "size-2.5 rounded-full border transition-all",
                          i === currentIdx
                            ? "scale-125 border-foreground bg-foreground"
                            : entry?.vote === "up"
                              ? "border-emerald-500 bg-emerald-500"
                              : entry?.vote === "down"
                                ? "border-destructive bg-destructive"
                                : "border-muted-foreground/40 bg-transparent"
                        )}
                      />
                    );
                  })}
                </div>

                {/* Arrow buttons */}
                <div className="flex gap-1">
                  <Button
                    disabled={currentIdx === 0}
                    onClick={() => goTo(currentIdx - 1)}
                    size="icon"
                    variant="outline"
                    className="size-8"
                  >
                    <ChevronLeft className="size-4" />
                  </Button>
                  <Button
                    disabled={currentIdx === spans.length - 1}
                    onClick={() => goTo(currentIdx + 1)}
                    size="icon"
                    variant="outline"
                    className="size-8"
                  >
                    <ChevronRight className="size-4" />
                  </Button>
                </div>
              </div>

              {/* Span card */}
              <div
                className={cn(
                  "rounded-xl border p-5 transition-all",
                  effectiveVote === "up" && "border-emerald-500/50 bg-emerald-500/5",
                  effectiveVote === "down" && "border-destructive/40 bg-destructive/5",
                  !effectiveVote && "border-border bg-card"
                )}
              >
                {/* Score + vote buttons */}
                <div className="mb-4 flex items-center justify-between gap-3">
                  <ScoreChip score={currentSpan.correctnessScore!} />

                  <div className="flex gap-2">
                    <button
                      type="button"
                      onClick={() => voteUp(currentSpan.spanId)}
                      title="Score looks right"
                      className={cn(
                        "flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-sm font-medium transition-colors",
                        effectiveVote === "up"
                          ? "bg-emerald-500/20 text-emerald-700"
                          : "border border-border text-muted-foreground hover:border-emerald-400/60 hover:bg-emerald-500/10 hover:text-emerald-700"
                      )}
                    >
                      <ThumbsUp className="size-4" />
                      Looks right
                    </button>
                    <button
                      type="button"
                      onClick={() => startVoteDown(currentSpan.spanId)}
                      title="Score is wrong"
                      className={cn(
                        "flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-sm font-medium transition-colors",
                        effectiveVote === "down"
                          ? "bg-destructive/20 text-destructive"
                          : "border border-border text-muted-foreground hover:border-destructive/40 hover:bg-destructive/10 hover:text-destructive"
                      )}
                    >
                      <ThumbsDown className="size-4" />
                      Wrong score
                    </button>
                  </div>
                </div>

                {/* Thumbs-down text feedback inline */}
                {pendingDownId === currentSpan.spanId && (
                  <div className="mb-4 space-y-2 rounded-lg border border-destructive/30 bg-destructive/5 p-3">
                    <p className="text-xs font-medium text-destructive">
                      What's wrong with this score?{" "}
                      <span className="font-normal text-destructive/70">Required</span>
                    </p>
                    <Textarea
                      autoFocus
                      className="min-h-[72px] resize-none text-sm"
                      placeholder="e.g. The output is actually correct, the judge is being too strict…"
                      value={pendingTexts[currentSpan.spanId] ?? ""}
                      onChange={(e) =>
                        setPendingTexts((prev) => ({
                          ...prev,
                          [currentSpan.spanId]: e.target.value,
                        }))
                      }
                      onKeyDown={(e) => {
                        if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
                          e.preventDefault();
                          confirmVoteDown(currentSpan.spanId);
                        }
                        if (e.key === "Escape") cancelVoteDown(currentSpan.spanId);
                      }}
                    />
                    <div className="flex justify-end gap-2">
                      <Button
                        size="sm"
                        variant="ghost"
                        onClick={() => cancelVoteDown(currentSpan.spanId)}
                      >
                        Cancel
                      </Button>
                      <Button
                        size="sm"
                        variant="destructive"
                        disabled={!(pendingTexts[currentSpan.spanId] ?? "").trim()}
                        onClick={() => confirmVoteDown(currentSpan.spanId)}
                      >
                        Confirm
                      </Button>
                    </div>
                  </div>
                )}

                {/* Span content */}
                <div className="space-y-5 overflow-y-auto" style={{ maxHeight: 380 }}>
                  <SpanSection label="Input" value={currentSpan.input} />
                  <SpanSection label="Output" value={currentSpan.output} />
                </div>
              </div>

              {/* Auto-advance hint */}
              {currentVote && !pendingDownId && currentIdx < spans.length - 1 && (
                <p className="text-center text-xs text-muted-foreground">
                  Rated — use{" "}
                  <button
                    type="button"
                    className="underline underline-offset-2 hover:text-foreground"
                    onClick={() => goTo(currentIdx + 1)}
                  >
                    next arrow
                  </button>{" "}
                  to continue.
                </p>
              )}
            </div>
          )}
        </div>

        {/* Footer */}
        {phase === "reviewing" && (
          <DialogFooter className="shrink-0 border-t border-border px-6 py-4">
            <div className="flex w-full items-center justify-between gap-4">
              <p className="text-xs text-muted-foreground">
                {!allVoted
                  ? `${votedCount} of ${spans.length} rated`
                  : allPositive
                    ? "All scores look correct — ready to confirm."
                    : iteration < MAX_ITERATIONS - 1
                      ? "Some scores flagged — description will be updated and re-scored."
                      : "Final round — your feedback will be used to update the agent."}
              </p>
              <Button disabled={!allVoted} onClick={handleConfirm} size="sm">
                {allPositive
                  ? "Confirm & Finish"
                  : iteration < MAX_ITERATIONS - 1
                    ? "Submit & Refine"
                    : "Submit Feedback"}
              </Button>
            </div>
          </DialogFooter>
        )}
      </DialogContent>
    </Dialog>
  );
}
