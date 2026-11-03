"""Hourly rollups of every span and issue: the absolute numbers behind rates and trends.

Recomputed from the source rows, never incremented, so reading a span twice can't drift them.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from django.db import connection, transaction

from overbae.models import Issue, IssueHour, IssueTrace, ModelHour, Span, TrafficHour

COST_KEYS = ("cost", "response_cost", "gen_ai.usage.cost", "genai.cost", "overmind.cost")
TOKEN_KEYS = ("genai.total_tokens", "genai.usage.total_tokens", "gen_ai.usage.total_tokens")
MODEL_KEYS = (
    "genai.model",
    "gen_ai.response.model",
    "genai.response.model",
    "gen_ai.request.model",
    "llm.model",
    "model",
)
HOUR_NS = 3_600_000_000_000
# Attributes come from arbitrary producers: a non-numeric value counts as absent, not as a crash.
NUMBER = r"^-?[0-9]+(\.[0-9]+)?([eE][-+]?[0-9]+)?$"


def hour_of(time_ns: int) -> datetime:
    return datetime.fromtimestamp((time_ns - time_ns % HOUR_NS) // 1_000_000_000, tz=UTC)


def text_of(column: str, keys: tuple[str, ...]) -> str:
    return "COALESCE(" + ", ".join(f"{column}->>'{key}'" for key in keys) + ")"


def number(column: str, keys: tuple[str, ...]) -> str:
    text = text_of(column, keys)
    return f"CASE WHEN {text} ~ '{NUMBER}' THEN ({text})::double precision END"


def rollup_hours(project_id, hours: set[datetime]) -> None:
    if not hours:
        return
    listed = sorted(hours)
    first, last = listed[0], listed[-1] + timedelta(hours=1)
    low = int(first.timestamp()) * 1_000_000_000
    high = int(last.timestamp()) * 1_000_000_000
    spans = Span._meta.db_table
    traces = IssueTrace._meta.db_table
    issues = Issue._meta.db_table
    bucket = "date_trunc('hour', to_timestamp(start_time_ns / 1e9))"
    score = number("eval_attributes", ("score",))
    with transaction.atomic(), connection.cursor() as cursor:
        TrafficHour.objects.filter(project_id=project_id, hour__in=listed).delete()
        ModelHour.objects.filter(project_id=project_id, hour__in=listed).delete()
        IssueHour.objects.filter(project_id=project_id, hour__in=listed).delete()
        cursor.execute(
            f"""
            INSERT INTO {IssueHour._meta.db_table} (project_id, issue_id, hour, traces)
            SELECT project_id, issue_id, date_trunc('hour', occurred_at) AS at, count(*)
            FROM {traces}
            WHERE project_id = %s AND occurred_at >= %s AND occurred_at < %s
              AND date_trunc('hour', occurred_at) = ANY(%s)
            GROUP BY project_id, issue_id, at
            """,
            [project_id, first, last, listed],
        )
        # Roots and failures can each exist in an hour without the other; UNION ALL then
        # GROUP BY joins them, because a capability can be NULL on either side.
        cursor.execute(
            f"""
            INSERT INTO {TrafficHour._meta.db_table}
                (project_id, capability_id, hour, traces, scored, score_sum, errored,
                 failing, trainable, flagged)
            SELECT %s, capability_id, at, sum(traces), sum(scored), sum(score_sum), sum(errored),
                   sum(failing), sum(trainable), sum(flagged)
            FROM (
                SELECT capability_id, at, count(*) AS traces, count(score) AS scored,
                       COALESCE(sum(score), 0) AS score_sum,
                       count(*) FILTER (WHERE status_code = 2) AS errored,
                       0 AS failing, 0 AS trainable, 0 AS flagged
                FROM (
                    SELECT capability_id, status_code, {bucket} AS at, {score} AS score
                    FROM {spans}
                    WHERE project_id = %s AND parent_span_id IS NULL
                      AND start_time_ns >= %s AND start_time_ns < %s
                ) roots
                WHERE at = ANY(%s)
                GROUP BY capability_id, at
                UNION ALL
                SELECT issue.capability_id, date_trunc('hour', trace.occurred_at) AS at,
                       0, 0, 0, 0,
                       count(DISTINCT trace.trace_id) FILTER (WHERE issue.cause <> 'data'),
                       count(DISTINCT trace.trace_id) FILTER (WHERE issue.cause = 'model'),
                       count(DISTINCT trace.trace_id)
                FROM {traces} trace
                JOIN {issues} issue ON issue.id = trace.issue_id
                WHERE trace.project_id = %s AND trace.occurred_at >= %s AND trace.occurred_at < %s
                  AND date_trunc('hour', trace.occurred_at) = ANY(%s)
                GROUP BY issue.capability_id, at
            ) measured
            GROUP BY capability_id, at
            """,
            [project_id, project_id, low, high, listed, project_id, first, last, listed],
        )
        cursor.execute(
            f"""
            INSERT INTO {ModelHour._meta.db_table}
                (project_id, capability_id, hour, model, calls, tokens, cost)
            SELECT project_id, capability_id, at, model, count(*),
                   COALESCE(sum(tokens), 0)::bigint, sum(cost)
            FROM (
                SELECT project_id, capability_id, {bucket} AS at,
                       {text_of("usage", MODEL_KEYS)} AS model,
                       {number("usage", TOKEN_KEYS)} AS tokens,
                       {number("usage", COST_KEYS)} AS cost
                FROM {spans}
                WHERE project_id = %s AND usage IS NOT NULL
                  AND start_time_ns >= %s AND start_time_ns < %s
            ) calls
            WHERE at = ANY(%s) AND model IS NOT NULL
            GROUP BY project_id, capability_id, at, model
            """,
            [project_id, low, high, listed],
        )
