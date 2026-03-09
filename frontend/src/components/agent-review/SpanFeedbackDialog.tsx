import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import {
  WarningDiamond as AlertCircle,
  Check as CheckCircle2,
  ChevronLeft,
  ChevronRight,
  Loader as Loader2,
  ThumbsDown,
  ThumbsUp,
  Cancel as X,
} from "pixelarticons/react";

import type { AgentOut, SpanForReview } from "@/api";
import apiClient from "@/client";
import { Badge } from "@/components/ui/badge";
import { BlockActions } from "@/components/ui/block-actions";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { isLikelyMarkdown, MarkdownContent } from "@/components/ui/markdown";
import { Textarea } from "@/components/ui/textarea";
import { cn } from "@/lib/utils";
import type { ChatMessage, ToolCallItem } from "@/types/chat";

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
  const prettyArgs = useMemo(() => {
    if (!tc.function?.arguments) return "";
    try {
      return JSON.stringify(JSON.parse(tc.function.arguments), null, 2);
    } catch {
      return tc.function.arguments;
    }
  }, [tc.function?.arguments]);

  return (
    <div className="rounded-lg border border-border/60 bg-muted/50 px-3 py-2 font-mono text-xs">
      <span className="font-semibold text-foreground">{tc.function?.name ?? "tool"}</span>
      {tc.id && <span className="ml-2 text-muted-foreground/60">({tc.id.slice(0, 12)}…)</span>}
      {prettyArgs && (
        <pre className="mt-1 whitespace-pre-wrap break-words text-muted-foreground">
          {prettyArgs}
        </pre>
      )}
    </div>
  );
}

function MessageBubble({ m }: { m: ChatMessage }) {
  const isUser = m.role === "user";
  const isSystem = m.role === "system";
  const isTool = m.role === "tool";
  const hasToolCalls = (m.tool_calls?.length ?? 0) > 0;
  const content = m.content ?? "";
  const canMarkdown = !isTool && content.length > 0 && isLikelyMarkdown(content);
  const [mode, setMode] = useState<"raw" | "markdown">(canMarkdown ? "markdown" : "raw");

  const roleLabel = isTool
    ? `tool${m.tool_call_id ? ` · ${m.tool_call_id.slice(0, 12)}…` : ""}`
    : m.role;

  return (
    <div className={cn("flex flex-col gap-1", isUser ? "items-end" : "items-start")}>
      <div className={cn("flex items-center gap-1", isUser ? "flex-row-reverse" : "flex-row")}>
        <span className="text-[10px] font-semibold uppercase tracking-widest text-muted-foreground">
          {roleLabel}
        </span>
        {content.length > 0 && (
          <BlockActions
            mode={mode}
            onToggleMode={() => setMode((prev) => (prev === "raw" ? "markdown" : "raw"))}
            showToggle={canMarkdown}
            text={content}
          />
        )}
      </div>
      {/* Tool calls emitted by the assistant */}
      {hasToolCalls && (
        <div className="max-w-[85%] space-y-1.5">
          {m.tool_calls!.map((tc, tcIdx) => (
            <ToolCallBubble key={tcIdx} tc={tc} />
          ))}
        </div>
      )}
      {/* Regular text content */}
      {content !== "" && (
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
          {isTool || mode === "raw" ? (
            <span className="whitespace-pre-wrap">{content}</span>
          ) : (
            <MarkdownContent>{content}</MarkdownContent>
          )}
        </div>
      )}
    </div>
  );
}

function ChatBubbles({ messages }: { messages: ChatMessage[] }) {
  return (
    <div className="space-y-2.5">
      {messages.map((m, i) => (
        <MessageBubble key={i} m={m} />
      ))}
    </div>
  );
}

// ---------------------------------------------------------------------------
// SpanSection
// ---------------------------------------------------------------------------

function SpanSection({ label, value }: { label: string; value: unknown }) {
  const messages = parseChatMessages(value);
  const plain = formatPlain(value);
  const canMarkdown =
    !messages && typeof value === "string" && plain.length > 0 && isLikelyMarkdown(plain);
  const [mode, setMode] = useState<"raw" | "markdown">(canMarkdown ? "markdown" : "raw");

  return (
    <div>
      <div className="mb-2 flex items-center justify-between gap-2">
        <p className="text-[10px] font-semibold uppercase tracking-widest text-muted-foreground">
          {label}
        </p>
        {!messages && (
          <BlockActions
            mode={mode}
            onToggleMode={() => setMode((prev) => (prev === "raw" ? "markdown" : "raw"))}
            showToggle={canMarkdown}
            text={plain}
          />
        )}
      </div>
      {messages ? (
        <ChatBubbles messages={messages} />
      ) : mode === "markdown" && canMarkdown ? (
        <div className="rounded-xl bg-muted/50 px-4 py-3 leading-relaxed text-foreground">
          <MarkdownContent>{plain}</MarkdownContent>
        </div>
      ) : (
        <pre className="whitespace-pre-wrap break-words rounded-xl bg-muted/50 px-4 py-3 font-mono text-xs leading-relaxed text-foreground">
          {plain}
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
  /** True when this is a follow-up periodic review (initial_review_completed already set). */
  isPeriodicReview?: boolean;
  /** Current scored span count, required to advance the threshold on periodic review completion. */
  scoredSpanCount?: number;
}

export function SpanFeedbackDialog({
  agent,
  onComplete,
  onClose,
  projectId,
  isPeriodicReview = false,
  scoredSpanCount,
}: Props) {
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
  const mountedRef = useRef(true);
  useEffect(() => {
    return () => {
      mountedRef.current = false;
    };
  }, []);

  const loadSpans = useCallback(async () => {
    setPhase("loading");
    setFetchError(null);
    setPendingDownId(null);
    setPendingTexts({});
    try {
      const data =
        await apiClient.agentReviews.getSpansForReviewApiV1AgentReviewsPromptSlugReviewSpansGet({
          projectId: projectId,
          promptSlug: agent.slug,
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
    setFeedback((prev) => ({ ...prev, [spanId]: { text: "", vote: "up" } }));
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
    setFeedback((prev) => ({ ...prev, [spanId]: { text, vote: "down" } }));
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
        setFeedback((prev) => ({ ...prev, [pendingDownId]: { text, vote: "down" } }));
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
          spanFeedbackRequest: {
            feedbackType: "judge",
            rating: entry.vote,
            text: entry.text || undefined,
          },
          spanId: s.spanId,
        });
      })
    );
  }

  // ---- submit --------------------------------------------------------------

  async function completeReview() {
    if (isPeriodicReview) {
      await apiClient.agentReviews.completePeriodicReviewApiV1AgentReviewsPromptSlugCompleteReviewPost(
        {
          currentSpanCount: scoredSpanCount ?? 0,
          projectId: projectId,
          promptSlug: agent.slug,
        }
      );
    } else {
      await apiClient.agentReviews.markInitialReviewCompleteApiV1AgentReviewsPromptSlugMarkInitialReviewCompletePost(
        {
          projectId: projectId,
          promptSlug: agent.slug,
        }
      );
    }
  }

  async function handleConfirm() {
    if (!allVoted) return;

    setPhase("refreshing");

    try {
      if (allPositive) {
        // All scores look correct — persist votes then close.
        setStatusMsg("Saving feedback…");
        await persistFeedback();
        setStatusMsg("Completing review…");
        await completeReview();
        setPhase("done");
        setTimeout(onComplete, 800);
        return;
      }

      if (iteration >= MAX_ITERATIONS - 1) {
        // Final iteration — persist votes then close.
        setStatusMsg("Saving feedback…");
        await persistFeedback();
        setStatusMsg("Completing review…");
        await completeReview();
        setPhase("thanked");
        return;
      }

      // Intermediate iteration — pass feedback inline to avoid writing stale
      // judge_feedback to the DB before the session is complete.
      const negativeSpans = spans.filter((s) => feedback[s.spanId]?.vote === "down");
      const negativeSpanIds = negativeSpans.map((s) => s.spanId);
      const inlineFeedback = Object.fromEntries(
        negativeSpans.map((s) => [
          s.spanId,
          { rating: "down", text: feedback[s.spanId]?.text ?? "" },
        ])
      );

      setStatusMsg("Updating agent description based on feedback…");
      await apiClient.agentReviews.syncRefreshDescriptionApiV1AgentReviewsPromptSlugSyncRefreshDescriptionPost(
        {
          projectId: projectId,
          promptSlug: agent.slug,
          syncRefreshDescriptionRequest: {
            feedback: inlineFeedback,
            spanIds: negativeSpanIds,
          },
        }
      );

      if (!mountedRef.current) return;
      setStatusMsg("Re-scoring spans…");
      const evalResult = await apiClient.spans.evaluateSpansApiV1SpansEvaluatePost({
        requestBody: negativeSpanIds,
      });

      if (!mountedRef.current) return;
      setStatusMsg("Waiting for scores to update…");
      const jobId = (evalResult as { job_id?: string }).job_id;
      if (jobId) {
        const deadline = Date.now() + JOB_POLL_TIMEOUT_MS;
        while (Date.now() < deadline && mountedRef.current) {
          await new Promise((resolve) => setTimeout(resolve, JOB_POLL_INTERVAL_MS));
          if (!mountedRef.current) return;
          try {
            const job = await apiClient.jobs.getJobApiV1JobsJobIdGet({ jobId });
            if (job.status === "completed" || job.status === "failed") break;
          } catch {
            // ignore transient poll errors
          }
        }
      }

      if (!mountedRef.current) return;
      setStatusMsg("Loading updated spans…");
      try {
        const refreshed =
          await apiClient.agentReviews.getSpansForReviewApiV1AgentReviewsPromptSlugReviewSpansGet({
            projectId: projectId,
            promptSlug: agent.slug,
            // Pass the fixed span IDs so the backend returns exactly the same
            // spans with updated scores rather than a new dynamic worst/best
            // selection — prevents duplicates when re-scored spans shift rank.
            spanIds: fixedSpanIdsRef.current,
          });

        if (!mountedRef.current) return;
        const refreshedAll = [...refreshed.worstSpans, ...refreshed.bestSpans];
        // Re-order to match the original display order so the user sees the
        // same spans in the same positions.
        const byId = new Map(refreshedAll.map((s) => [s.spanId, s]));
        const ordered = fixedSpanIdsRef.current.flatMap((id) => {
          const span = byId.get(id);
          return span ? [span] : [];
        });
        setSpans(ordered.length > 0 ? ordered : refreshedAll);
      } catch {
        // keep existing spans
      }

      if (!mountedRef.current) return;
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
    } catch {
      if (!mountedRef.current) return;
      setPhase("error");
      setStatusMsg("");
    }
  }

  // ---- render --------------------------------------------------------------

  return (
    <Dialog open>
      <DialogContent
        className="flex max-h-[95vh] w-full max-w-6xl flex-col gap-0 overflow-hidden p-0"
        onEscapeKeyDown={onClose}
        onInteractOutside={onClose}
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
                        key={s.spanId}
                        onClick={() => goTo(i)}
                        title={`Span ${i + 1}`}
                        type="button"
                      />
                    );
                  })}
                </div>

                {/* Arrow buttons */}
                <div className="flex gap-1">
                  <Button
                    className="size-8"
                    disabled={currentIdx === 0}
                    onClick={() => goTo(currentIdx - 1)}
                    size="icon"
                    variant="outline"
                  >
                    <ChevronLeft className="size-4" />
                  </Button>
                  <Button
                    className="size-8"
                    disabled={currentIdx === spans.length - 1}
                    onClick={() => goTo(currentIdx + 1)}
                    size="icon"
                    variant="outline"
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
                      className={cn(
                        "flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-sm font-medium transition-colors",
                        effectiveVote === "up"
                          ? "bg-emerald-500/20 text-emerald-700"
                          : "border border-border text-muted-foreground hover:border-emerald-400/60 hover:bg-emerald-500/10 hover:text-emerald-700"
                      )}
                      onClick={() => voteUp(currentSpan.spanId)}
                      title="Score looks right"
                      type="button"
                    >
                      <ThumbsUp className="size-4" />
                      Looks right
                    </button>
                    <button
                      className={cn(
                        "flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-sm font-medium transition-colors",
                        effectiveVote === "down"
                          ? "bg-destructive/20 text-destructive"
                          : "border border-border text-muted-foreground hover:border-destructive/40 hover:bg-destructive/10 hover:text-destructive"
                      )}
                      onClick={() => startVoteDown(currentSpan.spanId)}
                      title="Score is wrong"
                      type="button"
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
                      placeholder="e.g. The output is actually correct, the judge is being too strict…"
                      value={pendingTexts[currentSpan.spanId] ?? ""}
                    />
                    <div className="flex justify-end gap-2">
                      <Button
                        onClick={() => cancelVoteDown(currentSpan.spanId)}
                        size="sm"
                        variant="ghost"
                      >
                        Cancel
                      </Button>
                      <Button
                        disabled={!(pendingTexts[currentSpan.spanId] ?? "").trim()}
                        onClick={() => confirmVoteDown(currentSpan.spanId)}
                        size="sm"
                        variant="destructive"
                      >
                        Confirm
                      </Button>
                    </div>
                  </div>
                )}

                {/* Span content */}
                <div className="space-y-5">
                  <SpanSection label="Input" value={currentSpan.input} />
                  <SpanSection label="Output" value={currentSpan.output} />
                </div>
              </div>

              {/* Auto-advance hint */}
              {currentVote && !pendingDownId && currentIdx < spans.length - 1 && (
                <p className="text-center text-xs text-muted-foreground">
                  Rated — use{" "}
                  <button
                    className="underline underline-offset-2 hover:text-foreground"
                    onClick={() => goTo(currentIdx + 1)}
                    type="button"
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
