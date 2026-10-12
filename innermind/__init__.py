"""
The brain of innermind: everything runs on spans already in the database.

Pipeline, one cron each:
    span_scoring_queue        span -> SpanProcessor -> Evaluators (Jev expectations
                              + DeterministicCheck scripts) -> span.eval_attributes
    trajectory_scoring_queue  trace -> TrajectoryProcessor -> TrajectoryEvaluators
                              -> root.eval_attributes["trajectory"]
    signal_queue              session -> Signal -> printed

    @cron_decorator registers a job; run_forever drives them all in one event loop:
        uv run python -m innermind.span_processor

Shape of the code:
    - Factories resolve what to run: SpanProcessorFactory by span.kind,
      TrajectoryProcessorFactory by trace shape, SignalFactory by name.
    - A processor picks a span's evaluators; each evaluator returns an EvaluatorResult of
      Metrics; a signal returns a message or None. A new behaviour is one new class plus
      one factory entry.
    - Evaluators are LLM judges (LLMEvaluator, over the span's own expectations) or
      deterministic (OvermindEvaluator, one per matching DeterministicCheck row, run in
      the innermind.deterministic sandbox). Checks live only in the database.
    - score_* functions compute and return; only the queues write to the database.

Storage:
    - DeterministicCheck holds check definitions only. Results live in eval_attributes
      on a span: eval_attributes["evaluators"][name] per evaluator, and "score", the mean
      of the evaluator scores. eval_attributes["trajectory"] sits on a trace's root span.
    - A span is retried until eval_attributes has a "score" key. Write "score" only once
      the result is final; temporary failures (Jev 429/5xx, transport errors, a parent
      or root that has not arrived) must raise or return early without it.
    - A trajectory is rescored when its trace's span count changes.
    - Queues mark rows in_use, catch failures per item, and always clear in_use.

How to write code here:
    - Simple and readable over clever. No abstraction until a second real caller needs it;
      small module-level helpers are fine, base classes beyond the factories are not.
    - Reuse what exists: read_input/read_output/read_expectations/read_intent for span
      data, JevEvaluator.judge for any Jev call, deterministic.run for any check script,
      the signal helpers for grouping and scores.
    - LLM calls go through OpenRouter, bounded by openrouter_slots.
    - Thresholds are named constants or class attributes, never inline numbers.
    - Comments only for what the code cannot say: a wire-format quirk, a retry rule.
"""
