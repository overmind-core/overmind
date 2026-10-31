"""The product coverage map: every test file covers exactly one entry.

A promise is one product vertical, owned by one journey. Invariants hold across
verticals, contracts guard wire formats and boundaries, logic covers pure
computation with real edge cases.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class Severity(StrEnum):
    # A security breach, a wrong charge, or lost or changed data.
    CRITICAL = "S1"
    # A vertical, or the platform, stops for every customer.
    HIGH = "S2"
    # One path, integration, model family or displayed fact is wrong.
    MEDIUM = "S3"
    # A heuristic or a presentation detail is wrong.
    LOW = "S4"


class Kind(StrEnum):
    PROMISE = "promise"
    INVARIANT = "invariant"
    CONTRACT = "contract"
    LOGIC = "logic"


@dataclass(frozen=True)
class Entry:
    severity: Severity
    kind: Kind
    holds: str
    variants: tuple[str, ...] = ()


# Pytest wall time of the journeys run on a 4-vCPU CI runner; one journey may take a third.
JOURNEY_BUDGET_SECONDS = 300

S1, S2, S3, S4 = Severity.CRITICAL, Severity.HIGH, Severity.MEDIUM, Severity.LOW
PROMISE, INVARIANT, CONTRACT, LOGIC = Kind.PROMISE, Kind.INVARIANT, Kind.CONTRACT, Kind.LOGIC

COVERAGE_MAP: dict[str, Entry] = {
    "tenancy": Entry(
        S1,
        INVARIANT,
        "No other account and no anonymous caller reads or writes a project's rows: "
        "REST, MCP, Console and ingest.",
    ),
    "authentication": Entry(
        S1,
        INVARIANT,
        "Each surface admits only its own credentials, with their permissions: API key "
        "scopes, MCP OAuth, sessions, the guest gate, invites and seats, Overwatch tokens.",
    ),
    "privacy": Entry(
        S1,
        INVARIANT,
        "Secrets and private content never leave: keys, error text, tool arguments, raw "
        "worker text, analytics from a self-hosted install.",
    ),
    "ledger": Entry(
        S1,
        INVARIANT,
        "Each charge is metered once from measured usage at the current price; the hosted "
        "cap stops spend at zero; a top-up, renewal or webhook applies once.",
    ),
    "work-once": Entry(
        S1,
        INVARIANT,
        "A request key returns the same record and a changed payload conflicts; "
        "redelivery never repeats work; an unknown provider ack is reconciled, never "
        "resubmitted.",
    ),
    "spend-stops": Entry(
        S1,
        INVARIANT,
        "A failed or cancelled GPU job stops its provider call, deploys nothing, and its "
        "volume data is purged.",
    ),
    "lineage": Entry(
        S1,
        INVARIANT,
        "A used dataset version never changes; landing and publication are atomic; "
        "consumers pin versions; a sealed artifact cannot change under its identity.",
    ),
    "migrations": Entry(
        S1,
        INVARIANT,
        "The migration graph check passes; a data migration ships a replay test until a "
        "release applies it.",
    ),
    "observe": Entry(
        S2,
        PROMISE,
        "A coding agent connects and syncs the agent; its traces arrive as rooted trees "
        "bound to capabilities, are scored once per evidence and raise signals.",
    ),
    "data": Entry(
        S2,
        PROMISE,
        "A source lands, a pipeline publishes a dataset version, and a consumer's use freezes it.",
    ),
    "evaluate": Entry(
        S2,
        PROMISE,
        "An eval set runs every candidate and backtests it against the recorded baseline.",
    ),
    "optimise": Entry(S2, PROMISE, "Failing traces become a measured winning diff."),
    "train": Entry(
        S2,
        PROMISE,
        "A dataset version trains a model; the native benchmark measures it; it deploys "
        "and switches in by alias or pin.",
    ),
    "serve": Entry(
        S2,
        PROMISE,
        "The SDK and the gateway reach served, frontier and dedicated models, and each "
        "call is metered.",
    ),
    "decide": Entry(
        S2,
        PROMISE,
        "A decision dataset trains a decision model; its quality is measured before and "
        "after training; it answers through /api/v1/systemone.",
    ),
    "boot": Entry(
        S2,
        INVARIANT,
        "Hosted and self-hosted installs boot from their environment and answer health; "
        "a missing variable stops startup by name.",
    ),
    "topology": Entry(
        S2,
        INVARIANT,
        "Celery routes, compose, make worker and the worker topology agree; every "
        "scheduled task is registered; a release drains workers.",
    ),
    "recovery": Entry(
        S2,
        INVARIANT,
        "Work never stays stuck: lost tasks, worker loss and provider interruptions are "
        "reconciled; locks expire; periodic tasks recover.",
    ),
    "capacity": Entry(
        S2,
        INVARIANT,
        "Per-project limits hold under concurrency, one project never starves another, "
        "and the declared scale stays inside its query and task budgets.",
    ),
    "identity": Entry(
        S2,
        INVARIANT,
        "OTLP never creates a capability, a scan never deletes one, rebinding is by ID "
        "only, and DELETE is soft.",
    ),
    "sdk-install": Entry(
        S2,
        INVARIANT,
        "The published SDK never breaks its host: the default install imports and runs, and "
        "a missing extra, provider library, key or analytics backend degrades to a no-op or "
        "an install hint.",
    ),
    "measured-facts": Entry(
        S3,
        INVARIANT,
        "Missing usage, progress, cost, ETA and scores stay unknown; partial coverage "
        "reads as partial.",
    ),
    "live-console": Entry(
        S3,
        CONTRACT,
        "The open Console receives live updates and keeps its state after a reload.",
    ),
    "otlp-ingest": Entry(
        S3, CONTRACT, "Each OTLP dialect lands as its native span type, with sessions and errors."
    ),
    "sdk-wire": Entry(
        S3,
        CONTRACT,
        "SDK decorators and scopes build the trace the server reads (run boundaries, turns, "
        "handoffs, capability scope), and the SDK and the server agree on span attributes, "
        "usage keys, model names and scanned codebase cards.",
    ),
    "cli-contract": Entry(
        S3,
        CONTRACT,
        "Each CLI command validates its input before any request, changes only its own files, "
        "and exits non-zero with a machine-readable error.",
    ),
    "vendor-connectors": Entry(
        S3,
        CONTRACT,
        "Each vendor maps to rooted, capability-bound traces, with backfill, polling and "
        "rate limits.",
    ),
    "llm-gateway": Entry(
        S3,
        CONTRACT,
        "Each provider works through the gateway (streaming, tool calls, schemas, "
        "deadlines, routing); the model catalog and registry resolve.",
    ),
    "mcp-surface": Entry(
        S3,
        CONTRACT,
        "MCP schemas, manifest size, contract version, transport, prompts, resources and "
        "error shapes hold; every command handed to an agent parses.",
    ),
    "http-api": Entry(
        S3,
        CONTRACT,
        "REST matches the OpenAPI schema, the generated client is in sync, and every "
        "Console route renders without errors.",
    ),
    "model-runtime": Entry(
        S3,
        CONTRACT,
        "Training and serving runtimes work per model family: chat templates, hooks, "
        "adapter and weight formats, stacks, pinned releases.",
    ),
    "sandbox": Entry(
        S3,
        CONTRACT,
        "A pipeline step runs from its retained package with its pinned runtime and no "
        "network; a failed exit never reports success.",
    ),
    "metrics-math": Entry(
        S3,
        LOGIC,
        "Scores, judges, signals, benchmark grading, decision metrics, token accuracy, "
        "span cost and usage aggregation.",
    ),
    "data-logic": Entry(
        S3,
        LOGIC,
        "Transforms, splits, partitions, fan-in, fingerprints, file typing and the train "
        "and eval row contracts.",
    ),
    "agent-code": Entry(
        S3,
        LOGIC,
        "The CLI reads agent code into the call graphs, anchors and trajectories that exist, "
        "never overwrites config from an incomplete scan, and rewrites entrypoints that still run.",
    ),
    "plans": Entry(
        S3,
        LOGIC,
        "Hyperparameters, context budgets, batching, eval schedules and monitoring plans.",
    ),
    "state-machines": Entry(
        S3,
        LOGIC,
        "Job lifecycle, the optimiser, the native evaluation plan and model activation.",
    ),
    "recommendations": Entry(
        S4,
        LOGIC,
        "Model ranking and constraints, task-type classification, dataset profiling.",
    ),
    "console-logic": Entry(
        S4,
        LOGIC,
        "Formatters, table state, polling, upload state, charts and trajectory layout.",
    ),
}
