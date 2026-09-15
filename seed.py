"""Run: docker compose exec -T api python manage.py shell < seed.py

Full-platform demo workspace (Undermind, a fictional fintech). Deterministic
(seeded RNG, stable ids, timestamps anchored to NOW) and idempotent (re-running
deletes the Undermind projects/users and their cascade first).

Beat-safety — workers and beat stay up while this runs:
- every job/run/experiment is TERMINAL, or the reconcilers re-drive it;
- every span has a non-null ``feedback_score`` and a backdated ``received_at``,
  or the trace-scoring sweep picks it up;
- every context node carries an embedding, or the unembedded-node sweep
  re-drives it against the real provider;
- the Langfuse connector has ``auto_sync_enabled=False``.
"""

import hashlib
import json
import random
import re
import shutil
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.db import connection
from django.utils import timezone

from overbae.models import (
    Annotation,
    APIToken,
    BacktestRun,
    BillingService,
    BillingTelemetry,
    Capability,
    Cell,
    ConnectorCredential,
    Conversation,
    Dataset,
    DatasetContext,
    DeployedModel,
    EvalRun,
    EvalSample,
    EvalSet,
    EvalSetMember,
    Evaluator,
    EvalVariant,
    Feedback,
    FinetuningJob,
    FinetuningJobEval,
    FinetuningJobEvent,
    InferenceCall,
    JudgeCache,
    ModelRef,
    OptimizerCandidate,
    OptimizerCommand,
    OptimizerExperiment,
    OptimizerIteration,
    Project,
    ProjectMembership,
    Prompt,
    RunEvaluator,
    Score,
    Span,
    Subscription,
    TaskExecution,
    UserOnboarding,
)
from overbae.models.traces import usage_slice
from overbae.services.benchmarks.classify import classify_task_type
from overbae.services.codebase.flow import capability_tool_names
from overbae.services.datasets import land as dataset_land
from overbae.services.datasets import lifecycle as dataset_lifecycle
from overbae.services.datasets import paths as dataset_paths
from overbae.services.datasets import rows as row_store
from overbae.services.datasets.notebook import run as notebook_run
from overbae.services.eval import composition, profiler
from overbae.services.eval import dispatch as eval_dispatch
from overbae.services.eval import snapshots as eval_snapshots
from overbae.services.eval.managed import upsert_managed_evaluators
from overbae.tasks import eval as eval_tasks

User = get_user_model()


random.seed(20260501)

# Quantised so a re-run within the hour reproduces identical timestamps.
NOW = timezone.now().replace(minute=0, second=0, microsecond=0)


def days_ago(d: float, *, h: float = 0.0, m: float = 0.0) -> datetime:
    return NOW - timedelta(days=d, hours=h, minutes=m)


# Project birth dates (days before NOW). NOW is end-of-July at authoring time,
# so these span early May → mid-July.
D_SUPPORT = 87
D_PAYMENTS = 70
D_EXPENSE = 55
D_GROWTH = 30
D_ONBOARD = 18

SEED_PROJECT_SLUGS = (
    "support-copilot",
    "payments-analyst",
    "expense-audit",
    "growth-outreach",
    "merchant-onboarding",
)
SEED_USER_DOMAIN = "undermindlab.ai"


def rnd(lo: float, hi: float, nd: int = 2) -> float:
    return round(random.uniform(lo, hi), nd)


def hexid(nbytes: int) -> str:
    # Seeded RNG, not secrets: stable ids across re-seeds; local demo data.
    return f"{random.getrandbits(nbytes * 8):0{nbytes * 2}x}"


def pick(seq):
    return seq[random.randrange(len(seq))]


def backdate(model, pairs, column: str = "created_at") -> None:
    """Set an auto_now_add/auto_now column via SQL (ORM create() ignores it)."""
    if not pairs:
        return
    table = model._meta.db_table
    col = model._meta.get_field(column).column
    pk = model._meta.pk.column
    with connection.cursor() as cur:
        cur.executemany(
            f'UPDATE "{table}" SET "{col}" = %s WHERE "{pk}" = %s',  # noqa: S608
            [(dt, str(pk_val)) for pk_val, dt in pairs],
        )


def stamp(obj, created_at, updated_at=None, **extra_cols):
    """Backdate one row's created_at (+ optional updated_at / other columns)."""
    fields = {"created_at": created_at}
    if updated_at is not None and any(
        f.name == "updated_at" for f in obj._meta.get_fields() if hasattr(f, "column")
    ):
        fields["updated_at"] = updated_at
    fields.update(extra_cols)
    type(obj).objects.filter(pk=obj.pk).update(**fields)


def business_hour(day: datetime) -> datetime:
    """A weighted moment inside that day: mostly 09:00–19:00 UTC, thin tails."""
    r = random.random()
    if r < 0.78:
        h = random.randint(9, 18)
    elif r < 0.92:
        h = random.randint(19, 22)
    else:
        h = random.randint(6, 8)
    return day.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(
        hours=h, minutes=random.randint(0, 59), seconds=random.randint(0, 59)
    )


def daily_volume(base: int, age_days: int, day_index: int, weekday: int) -> int:
    """Trace volume for one day: ramps up over the project's life, dips on
    weekends, deterministic jitter."""
    ramp = min(1.0, 0.15 + 0.85 * (day_index / max(1, age_days * 0.6)))
    week = 0.35 if weekday >= 5 else 1.0
    jitter = random.uniform(0.75, 1.25)
    return max(1, int(base * ramp * week * jitter))


print("Clearing existing Undermind seed data...")

doomed_datasets = list(
    Dataset.objects.filter(project__slug__in=SEED_PROJECT_SLUGS).values_list("id", flat=True)
)
# Consumers PROTECT the cell they used; they go first so the project
# cascade can collect datasets cleanly.
FinetuningJob.objects.filter(project__slug__in=SEED_PROJECT_SLUGS).delete()
OptimizerExperiment.objects.filter(project__slug__in=SEED_PROJECT_SLUGS).delete()
EvalRun.objects.filter(project__slug__in=SEED_PROJECT_SLUGS).delete()
Project.objects.filter(slug__in=SEED_PROJECT_SLUGS).delete()
for dataset_id in doomed_datasets:
    # The DB cascade never touches MEDIA_ROOT: the Parquet files stay unless removed here.
    shutil.rmtree(dataset_paths.dataset_dir(dataset_id), ignore_errors=True)
User.objects.filter(email__endswith=f"@{SEED_USER_DOMAIN}").delete()
# Feedback.user is SET_NULL — deleting the seed users orphans their rows
# instead of removing them.
Feedback.objects.filter(user__isnull=True).delete()


print("Creating the Undermind team...")

upsert_managed_evaluators()  # global evaluator library; idempotent no-op if present


def _user(
    email, first, last, tz, sign_on, joined_days_ago, *, avatar="", password="undermind-demo"
):
    u = User.objects.create_user(
        email=email,
        password=password,
        first_name=first,
        last_name=last,
        timezone=tz,
        sign_on_method=sign_on,
        clerk_user_id=f"user_{hashlib.sha256(email.encode()).hexdigest()[:24]}",
        avatar_url=avatar,
    )
    User.objects.filter(pk=u.pk).update(date_joined=days_ago(joined_days_ago))
    # The auto-granted free-credits ledger row should carry the join date too.
    BillingTelemetry.objects.filter(user=u, service=BillingService.FREE_CREDITS).update(
        timestamp=days_ago(joined_days_ago)
    )
    return u


# The demo login: superuser, member of every project.
admin = _user(
    "admin@undermindlab.ai",
    "Undermind",
    "Admin",
    "Europe/London",
    "password",
    89,
    password="password",
)
admin.is_staff = True
admin.is_superuser = True
admin.projects_limit = None
admin.save(update_fields=["is_staff", "is_superuser", "projects_limit"])

priya = _user("priya@undermindlab.ai", "Priya", "Raghavan", "Europe/London", "google", 88)
jonas = _user("jonas@undermindlab.ai", "Jonas", "Weber", "Europe/Berlin", "google", 86)
amara = _user("amara@undermindlab.ai", "Amara", "Okafor", "Europe/London", "password", 80)
diego = _user("diego@undermindlab.ai", "Diego", "Martínez", "America/Mexico_City", "google", 71)
sofia = _user("sofia@undermindlab.ai", "Sofia", "Lindqvist", "Europe/Stockholm", "password", 62)
team = [priya, jonas, amara, diego, sofia]

# Pro plan for the two heaviest users; going Pro lifts the projects cap.
for u, sub_days, price in ((priya, 82, "price_1RkPro9MoSeat"), (jonas, 60, "price_1RkPro9MoSeat")):
    u.stripe_customer_id = f"cus_{hashlib.sha256(u.email.encode()).hexdigest()[:14]}"
    u.projects_limit = None
    u.save(update_fields=["stripe_customer_id", "projects_limit"])
    sub = Subscription.objects.create(
        user=u,
        stripe_subscription_id=f"sub_1R{hexid(6)}",
        stripe_price_id=price,
        status="active",
        start_date=days_ago(sub_days),
        end_date=NOW + timedelta(days=30 - (sub_days % 30)),
        cancel_at_period_end=False,
        last_stripe_invoice_id=f"in_1R{hexid(6)}",
        payload={
            "object": "subscription",
            "status": "active",
            "cancel_at_period_end": False,
            "collection_method": "charge_automatically",
            "currency": "usd",
        },
    )
    stamp(sub, days_ago(sub_days), days_ago(sub_days % 30))

ONBOARDING = {
    priya: (
        "done",
        "completed",
        ["Improve capability accuracy", "Cut inference cost"],
        "We run several LLM agents in production (support, payments analytics) "
        "and have no systematic eval or training loop.",
    ),
    jonas: (
        "done",
        "completed",
        ["Fine-tune on our own data", "Own our models"],
        "Looking to replace frontier-model calls with private fine-tunes where quality allows.",
    ),
    amara: (
        "done",
        "completed",
        ["Understand capability failures"],
        "Support lead — I need to see why the copilot mis-routes tickets.",
    ),
    diego: (
        "done",
        "completed",
        ["Evaluate model quality", "Reduce hallucinations"],
        "Building NL→SQL tooling for our analytics surface.",
    ),
    sofia: ("connect-repo", "in_progress", ["Improve capability accuracy"], ""),
}
for u, (step, status, priorities, desc) in ONBOARDING.items():
    ob = UserOnboarding.objects.create(
        user=u, step=step, status=status, priorities=priorities, description=desc
    )
    stamp(ob, u.date_joined + timedelta(minutes=12))


print("Creating projects...")


def _project(name, slug, integration, born_days, members):
    p = Project.objects.create(
        name=name,
        slug=slug,
        integration_type=integration,
        settings={"default_timezone": "UTC"},
    )
    stamp(p, days_ago(born_days), days_ago(random.uniform(0, 2)))
    for u in [admin, *members]:
        m = ProjectMembership.objects.create(user=u, project=p)
        stamp(m, max(days_ago(born_days), u.date_joined), days_ago(born_days))
    # Local dev convenience: every pre-existing account can browse the demo.
    for u in User.objects.exclude(email__endswith=f"@{SEED_USER_DOMAIN}"):
        ProjectMembership.objects.get_or_create(user=u, project=p)
    return p


support_proj = _project(
    "Support Copilot",
    "support-copilot",
    "sdk",
    D_SUPPORT,
    [priya, jonas, amara, sofia],
)
payments_proj = _project(
    "Payments Analyst",
    "payments-analyst",
    "sdk",
    D_PAYMENTS,
    [priya, diego, jonas],
)
expense_proj = _project(
    "Expense Audit",
    "expense-audit",
    "sdk",
    D_EXPENSE,
    [priya, jonas, sofia],
)
growth_proj = _project(
    "Growth Outreach",
    "growth-outreach",
    "sdk",
    D_GROWTH,
    [priya, amara],
)
onboard_proj = _project(
    "Merchant Onboarding",
    "merchant-onboarding",
    "sdk",
    D_ONBOARD,
    [priya, sofia],
)
seed_projects = [support_proj, payments_proj, expense_proj, growth_proj, onboard_proj]


raw_keys: list[tuple[str, str]] = []
for user_, proj, name, desc, made, used in (
    (
        priya,
        support_proj,
        "prod trace exporter",
        "OTLP ingest key used by the support-copilot deployment.",
        D_SUPPORT - 1,
        0.02,
    ),
    (
        priya,
        payments_proj,
        "prod trace exporter",
        "OTLP ingest key for the analytics workers.",
        D_PAYMENTS - 1,
        0.04,
    ),
    (jonas, expense_proj, "prod trace exporter", "", D_EXPENSE - 1, 0.03),
    (sofia, support_proj, "local CLI", "overmind optimise from sofia's workstation.", 40, 1.1),
    (diego, payments_proj, "local CLI", "", 45, 21),
    (priya, onboard_proj, "staging exporter", "", D_ONBOARD - 1, 2.5),
):
    raw, tok = APIToken.create_for_user(user_, name=name, project=proj)
    tok.description = desc
    tok.last_used_at = days_ago(used)
    tok.rate_limit = {"requests_per_minute": 600}
    tok.save(update_fields=["description", "last_used_at", "rate_limit"])
    stamp(tok, days_ago(made), days_ago(used))
    raw_keys.append((f"{user_.email} / {proj.slug} / {name}", raw))

#
# input_schema / output_fields / tool_config are the Capability Fit contract — the
# workshop fit checks and the eval normalizer read them, so they are
# load-bearing, not decoration.

print("Creating capabilities...")

# The expense compact fine-tune shipped weeks ago, so receipt-extractor
# already runs Undermind's own model in production. The job id is fixed here
# so the served model id exists before traces reference it.
FT_EXPENSE_COMPACT_ID = uuid.uuid5(uuid.NAMESPACE_URL, "seed/ft/expense-compact")
FT_EXPENSE_V2_ID = uuid.uuid5(uuid.NAMESPACE_URL, "seed/ft/expense-v2")
FT_DISPUTE_ID = uuid.uuid5(uuid.NAMESPACE_URL, "seed/ft/dispute-8b")
FT_ONBOARD_FAIL_ID = uuid.uuid5(uuid.NAMESPACE_URL, "seed/ft/onboard-fail")
EXPENSE_COMPACT_MODEL_ID = f"ft-{str(FT_EXPENSE_COMPACT_ID)[:8]}-llama-3-2-3b-instruct"
EXPENSE_V2_MODEL_ID = f"ft-{str(FT_EXPENSE_V2_ID)[:8]}-qwen3-8b"
DISPUTE_MODEL_ID = f"ft-{str(FT_DISPUTE_ID)[:8]}-llama-3-1-8b-instruct"


def _tool_decl(name, description, args):
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": {a: {"type": t} for a, t in args.items()},
                "required": list(args),
            },
        },
    }


def _capability(project, name, slug, born_days, **kw):
    a = Capability.objects.create(project=project, name=name, slug=slug, **kw)
    stamp(a, days_ago(born_days), days_ago(random.uniform(0, 1.5)))
    return a


triage_capability = _capability(
    support_proj,
    "Ticket Triage",
    "ticket-triage",
    D_SUPPORT - 1,
    description=(
        "Classifies inbound support tickets by urgency, category, and owning "
        "team, honouring plan-based SLAs."
    ),
    source_path="capabilities/triage/capability.py",
    entrypoint_fn="run_triage",
    model="openai/gpt-5.6-sol",
    analyzer_model="anthropic/claude-sonnet-5",
    cli_version="0.6.3",
    input_schema={
        "ticket_text": {"type": "string", "required": True},
        "merchant_plan": {"type": "enum", "values": ["starter", "growth", "enterprise"]},
        "previous_tickets": {"type": "integer", "default": 0},
    },
    output_fields={
        "urgency": {"type": "enum", "values": ["low", "medium", "high", "critical"], "weight": 35},
        "category": {"type": "string", "weight": 30},
        "team": {"type": "string", "weight": 20},
        "summary": {"type": "string", "weight": 15},
    },
    structure_weight=30.0,
    total_points=100.0,
    tool_config={
        "expected_tools": [
            {"name": "lookup_merchant", "weight": 6},
            {"name": "search_kb", "weight": 4},
        ]
    },
    tool_usage_weight=10.0,
    consistency_rules=[
        "urgency == 'critical' implies team startswith 'oncall'",
        "merchant_plan == 'enterprise' implies urgency != 'low'",
    ],
    optimizable_elements=["system_prompt", "urgency_criteria", "few_shot_examples"],
    fixed_elements=["output_schema", "team_registry"],
    policy_markdown=(
        "# Triage policy\n\n"
        "- Enterprise merchants are never routed below `medium` urgency.\n"
        "- Payment-disruption reports go to `oncall-payments` regardless of plan.\n"
        "- Suspected fraud always escalates to `risk-ops` with a summary that "
        "never quotes card numbers.\n"
    ),
    tools_summary=(
        "lookup_merchant fetches the plan, account age and open incident flags; "
        "search_kb retrieves matching help-centre articles for context."
    ),
    decision_logic=(
        "Classify from the ticket text alone, then adjust urgency using the "
        "merchant plan and open incident flags before selecting the team."
    ),
)

kb_capability = _capability(
    support_proj,
    "KB Answerer",
    "kb-answerer",
    D_SUPPORT - 1,
    description="Answers how-to questions from the help centre with citations.",
    source_path="capabilities/kb/capability.py",
    entrypoint_fn="answer_question",
    model="anthropic/claude-sonnet-5",
    analyzer_model="anthropic/claude-sonnet-5",
    cli_version="0.6.3",
    input_schema={
        "question": {"type": "string", "required": True},
        "merchant_plan": {"type": "enum", "values": ["starter", "growth", "enterprise"]},
    },
    output_fields={
        "answer": {"type": "string", "weight": 55},
        "citations": {"type": "array", "weight": 30},
        "confidence": {"type": "float", "range": [0, 1], "weight": 15},
    },
    structure_weight=20.0,
    tool_config={
        "expected_tools": [
            {"name": "search_kb", "weight": 8},
            {"name": "fetch_article", "weight": 4},
        ]
    },
    tool_usage_weight=12.0,
    consistency_rules=["every claim in answer is supported by a cited article"],
    optimizable_elements=["system_prompt", "retrieval_query_template"],
    fixed_elements=["output_schema", "citation_format"],
    tools_summary="search_kb (hybrid retrieval) and fetch_article (full text by slug).",
    decision_logic="Retrieve, answer strictly from retrieved articles, cite by slug.",
)

dispute_capability = _capability(
    support_proj,
    "Dispute Resolver",
    "dispute-resolver",
    D_SUPPORT - 1,
    description=(
        "Drafts chargeback / dispute resolutions from ledger evidence, "
        "following the representment policy."
    ),
    source_path="capabilities/disputes/capability.py",
    entrypoint_fn="resolve_dispute",
    model="anthropic/claude-sonnet-5",
    analyzer_model="anthropic/claude-sonnet-5",
    cli_version="0.6.3",
    input_schema={
        "dispute_id": {"type": "string", "required": True},
        "reason_code": {"type": "string", "required": True},
        "amount": {"type": "float"},
        "merchant_note": {"type": "string"},
    },
    output_fields={
        "resolution": {
            "type": "enum",
            "values": ["accept", "represent", "request_evidence"],
            "weight": 40,
        },
        "draft_reply": {"type": "string", "weight": 35},
        "evidence_used": {"type": "array", "weight": 25},
    },
    structure_weight=25.0,
    tool_config={
        "expected_tools": [
            {"name": "lookup_transaction", "weight": 6},
            {"name": "fetch_dispute_evidence", "weight": 6},
            {"name": "check_policy", "weight": 4},
        ]
    },
    tool_usage_weight=16.0,
    consistency_rules=[
        "resolution == 'represent' implies evidence_used is non-empty",
        "amount > 5000 implies resolution != 'accept' without policy check",
    ],
    optimizable_elements=["system_prompt", "evidence_selection_rules"],
    fixed_elements=["output_schema", "tool_list", "policy_text"],
    policy_markdown=(
        "# Representment policy\n\n"
        "- Fight only when at least two evidence classes support the charge.\n"
        "- Accept friendly-fraud disputes under $25 (processing cost exceeds "
        "recovery).\n- Never promise a refund timeline in the draft reply.\n"
    ),
    tools_summary=(
        "lookup_transaction (ledger row), fetch_dispute_evidence (delivery "
        "confirmations, AVS/CVV results, customer comms), check_policy "
        "(threshold rules by reason code)."
    ),
    decision_logic=(
        "Gather ledger + evidence, apply the reason-code policy table, then "
        "draft the reply in the merchant's voice."
    ),
)

sql_capability = _capability(
    payments_proj,
    "SQL Analyst",
    "sql-analyst",
    D_PAYMENTS - 2,
    description="Translates analytics questions into warehouse SQL and explains results.",
    source_path="analyst/capabilities/sql_analyst.py",
    entrypoint_fn="answer",
    model="openai/gpt-5.6-terra",
    analyzer_model="anthropic/claude-sonnet-5",
    cli_version="0.6.1",
    input_schema={
        "question": {"type": "string", "required": True},
        "dialect": {"type": "enum", "values": ["postgres", "bigquery"], "default": "postgres"},
    },
    output_fields={
        "sql": {"type": "string", "weight": 50},
        "explanation": {"type": "string", "weight": 30},
        "confidence": {"type": "float", "range": [0, 1], "weight": 20},
    },
    structure_weight=15.0,
    tool_config={
        "expected_tools": [
            {"name": "list_tables", "weight": 4},
            {"name": "execute_sql", "weight": 12},
        ]
    },
    tool_usage_weight=16.0,
    consistency_rules=["sql parses under the selected dialect", "confidence in [0, 1]"],
    optimizable_elements=["system_prompt", "schema_digest"],
    fixed_elements=["output_schema", "row_limit_guard"],
    tools_summary="list_tables (schema digest) and execute_sql (read-only, 10k row cap).",
    decision_logic="Ground on the schema digest, generate SQL, execute, then explain.",
)

chart_capability = _capability(
    payments_proj,
    "Chart Composer",
    "chart-composer",
    D_PAYMENTS - 2,
    description="Turns query results into Vega-Lite chart specs with captions.",
    source_path="analyst/capabilities/chart_composer.py",
    entrypoint_fn="compose",
    model="google/gemini-2.5-pro",
    analyzer_model="anthropic/claude-sonnet-5",
    cli_version="0.6.1",
    input_schema={
        "question": {"type": "string", "required": True},
        "columns": {"type": "array", "required": True},
        "rows_preview": {"type": "array"},
    },
    output_fields={
        "vega_lite_spec": {"type": "object", "weight": 60},
        "caption": {"type": "string", "weight": 40},
    },
    structure_weight=35.0,
    tool_config={},
    tool_usage_weight=0.0,
    consistency_rules=["vega_lite_spec.encoding references only provided columns"],
    optimizable_elements=["system_prompt"],
    fixed_elements=["output_schema"],
)

receipt_capability = _capability(
    expense_proj,
    "Receipt Extractor",
    "receipt-extractor",
    D_EXPENSE - 1,
    # Model-swap PR for the compact fine-tune merged five weeks ago — this
    # capability runs Undermind's own model in production.
    model=EXPENSE_COMPACT_MODEL_ID,
    description="Extracts structured expense records from OCR'd receipt text.",
    source_path="pipeline/capabilities/receipt_extractor.py",
    entrypoint_fn="extract",
    analyzer_model="anthropic/claude-sonnet-5",
    cli_version="0.6.2",
    input_schema={
        "ocr_text": {"type": "string", "required": True},
        "hint_currency": {"type": "string"},
    },
    output_fields={
        "merchant": {"type": "string", "weight": 15},
        "date": {"type": "string", "weight": 15},
        "total_amount": {"type": "float", "weight": 25},
        "currency": {"type": "string", "weight": 10},
        "tax_amount": {"type": "float", "weight": 10},
        "category": {
            "type": "enum",
            "values": ["travel", "meals", "software", "office", "other"],
            "weight": 25,
        },
    },
    structure_weight=40.0,
    tool_config={},
    tool_usage_weight=0.0,
    consistency_rules=[
        "total_amount >= tax_amount",
        "currency is ISO-4217",
        "date is ISO-8601",
    ],
    optimizable_elements=["system_prompt", "few_shot_examples"],
    fixed_elements=["output_schema"],
)

policy_capability = _capability(
    expense_proj,
    "Policy Checker",
    "policy-checker",
    D_EXPENSE - 1,
    description="Validates expense records against the T&E policy, citing rules.",
    source_path="pipeline/capabilities/policy_checker.py",
    entrypoint_fn="check",
    model="anthropic/claude-sonnet-5",
    analyzer_model="anthropic/claude-sonnet-5",
    cli_version="0.6.2",
    input_schema={
        "record": {"type": "object", "required": True},
        "employee_level": {"type": "enum", "values": ["ic", "manager", "exec"]},
    },
    output_fields={
        "verdict": {"type": "enum", "values": ["approve", "flag", "reject"], "weight": 45},
        "violated_rules": {"type": "array", "weight": 30},
        "note": {"type": "string", "weight": 25},
    },
    structure_weight=30.0,
    tool_config={"expected_tools": [{"name": "fetch_policy_rule", "weight": 8}]},
    tool_usage_weight=8.0,
    consistency_rules=["verdict != 'approve' implies violated_rules non-empty"],
    optimizable_elements=["system_prompt"],
    fixed_elements=["output_schema", "policy_text"],
)

outreach_capability = _capability(
    growth_proj,
    "Outreach Composer",
    "outreach-composer",
    D_GROWTH - 1,
    description="Drafts personalised merchant outreach from account signals.",
    source_path="growth/capabilities/outreach.py",
    entrypoint_fn="compose_outreach",
    model="openai/gpt-5.6-sol",
    cli_version="unknown",  # telemetry arrives via Langfuse sync, not the SDK
    input_schema={
        "merchant_name": {"type": "string", "required": True},
        "segment": {"type": "string"},
        "signal": {"type": "string"},
    },
    output_fields={
        "subject": {"type": "string", "weight": 30},
        "body": {"type": "string", "weight": 55},
        "cta": {"type": "string", "weight": 15},
    },
    structure_weight=20.0,
    tool_config={},
    tool_usage_weight=0.0,
    consistency_rules=["body under 160 words", "no pricing promises"],
    optimizable_elements=["system_prompt", "tone_guide"],
    fixed_elements=["output_schema"],
)

kyc_capability = _capability(
    onboard_proj,
    "KYC Doc Helper",
    "kyc-doc-helper",
    D_ONBOARD - 2,
    description="Answers applicant questions about required KYC documents.",
    source_path="onboarding/capabilities/kyc_helper.py",
    entrypoint_fn="answer_kyc",
    model="google/gemini-2.5-pro",
    analyzer_model="anthropic/claude-sonnet-5",
    cli_version="0.6.3",
    input_schema={
        "question": {"type": "string", "required": True},
        "entity_type": {"type": "enum", "values": ["sole_prop", "llc", "corp", "nonprofit"]},
        "country": {"type": "string"},
    },
    output_fields={
        "answer": {"type": "string", "weight": 60},
        "required_documents": {"type": "array", "weight": 40},
    },
    structure_weight=25.0,
    tool_config={"expected_tools": [{"name": "lookup_requirements", "weight": 8}]},
    tool_usage_weight=8.0,
    consistency_rules=["required_documents come from the requirements table"],
    optimizable_elements=["system_prompt"],
    fixed_elements=["output_schema", "requirements_table"],
)

# History: a purpose an earlier scan saw and the latest did not, and a duplicate
# partition that remounted into Ticket Triage — the history drawer and the merged
# redirect have something to show.
legacy_router = _capability(
    support_proj,
    "Legacy Ticket Router",
    "legacy-ticket-router",
    D_SUPPORT - 3,
    description="Routed tickets by keyword before the classifier existed.",
    source_path="support/legacy/router.py",
    entrypoint_fn="route",
    status=Capability.Status.LEFTOVER,
    improvement_metadata={"not_reproduced_at_sha": "b7e2a91c" * 5},
)
capabilities = [
    triage_capability,
    kb_capability,
    dispute_capability,
    sql_capability,
    chart_capability,
    receipt_capability,
    policy_capability,
    outreach_capability,
    kyc_capability,
]


print("Creating prompt versions...")

PROMPTS = {
    triage_capability: [
        (
            "baseline",
            "You are Undermind's support triage capability. Read the ticket and "
            "return strict JSON with urgency, category, team and a one-line summary.",
        ),
        (
            "sla-aware",
            "You are Undermind's support triage capability.\n\nClassify the ticket, "
            "then adjust urgency using the merchant plan (enterprise ≥ medium) and any "
            "open incident flags from lookup_merchant. Return strict JSON only.",
        ),
        (
            "optimised-v3",
            "You are Undermind's support triage capability.\n\nProcess:\n"
            "1. Identify the failure surface (API, dashboard, payouts, billing, fraud).\n"
            "2. Call lookup_merchant; enterprise merchants are never `low` urgency.\n"
            "3. Payment-disruption reports route to oncall-payments; suspected fraud to "
            "risk-ops.\n4. Summaries never quote card numbers.\n\nReturn strict JSON "
            "matching the schema. Urgency reflects merchant impact, not sentiment.",
        ),
    ],
    kb_capability: [
        (
            "baseline",
            "Answer the merchant's question using only the retrieved help-centre "
            "articles. Cite article slugs for every claim. Return JSON.",
        ),
        (
            "citation-strict",
            "Answer strictly from retrieved articles. Every sentence "
            "that states a fact must carry a citation. If the articles do not answer the "
            "question, say so and set confidence below 0.3. Return JSON.",
        ),
    ],
    dispute_capability: [
        (
            "baseline",
            "You resolve card disputes for Undermind merchants. Use the ledger "
            "and evidence tools, apply the representment policy, and draft the reply.",
        ),
        (
            "policy-v2",
            "You resolve card disputes for Undermind merchants.\n\nAlways: "
            "lookup_transaction → fetch_dispute_evidence → check_policy, then decide. "
            "Fight only with two independent evidence classes. Accept friendly-fraud "
            "under $25. Never promise refund timelines. Return strict JSON.",
        ),
    ],
    sql_capability: [
        (
            "baseline",
            "Translate the question into a single read-only SQL query for the "
            "payments warehouse. Explain the result in one paragraph.",
        ),
        (
            "schema-grounded",
            "Translate the question into one read-only SQL query.\n\n"
            "Ground every table and column on the schema digest from list_tables — never "
            "invent columns. Amounts are minor units; settlement times are UTC. Use "
            "net_amount for revenue questions unless gross is requested. Execute, then "
            "explain. Return JSON.",
        ),
    ],
    receipt_capability: [
        (
            "baseline",
            "Extract merchant, date, total_amount, currency, tax_amount and "
            "category from the OCR text. Return strict JSON, null for unreadable fields.",
        ),
        (
            "ft-serving",
            "Extract the expense record fields from the receipt text. "
            "Return strict JSON matching the schema; ISO-4217 currency, ISO-8601 date.",
        ),
    ],
}
prompt_rows: dict = {}
for capability_, versions in PROMPTS.items():
    for i, (label, text) in enumerate(versions, start=1):
        p = Prompt.objects.create(
            capability=capability_,
            version=i,
            label=label,
            system_prompt=text,
            tools_json=capability_tool_names(capability_),
            model=capability_.model,
        )
        stamp(p, days_ago(D_SUPPORT - 3 - (i - 1) * 18))
        prompt_rows.setdefault(capability_, []).append(p)


print("Creating evaluators and eval sets...")


def managed(name):
    ev = (
        Evaluator.objects.filter(is_managed=True, project=None, capability=None, name=name)
        .order_by("-version")
        .first()
    )
    if ev is None:
        raise RuntimeError(f"managed evaluator {name!r} missing")
    return ev


def _checklist(rubric_md):
    """A generative run grades an LLM judge from its checklist, so every seeded
    judge carries one: one item per rubric bullet, else per sentence."""
    lines = [ln.strip() for ln in (rubric_md or "").splitlines()]
    items = [re.sub(r"^[-*]\s*|\*\*", "", ln).strip() for ln in lines if ln.startswith(("-", "*"))]
    if not items:
        items = [t.strip() for t in re.split(r"(?<=[.!?])\s+", rubric_md or "") if t.strip()]
    items = [t.rstrip(".") for t in items if t][:12] or ["Does the output satisfy the rubric?"]
    return [{"id": f"q{i + 1}", "q": q, "weight": 1.0} for i, q in enumerate(items)]


def _evaluator(project, name, kind, born_days, **kw):
    if kind == "llm_judge" and not kw.get("checklist"):
        kw["checklist"] = _checklist(kw.get("rubric_md", ""))
    ev = Evaluator.objects.create(project=project, name=name, kind=kind, **kw)
    stamp(ev, days_ago(born_days))
    return ev


JUDGE = "anthropic/claude-sonnet-5"

triage_accuracy = _evaluator(
    support_proj,
    "Triage Accuracy",
    "llm_judge",
    D_SUPPORT - 20,
    description="Urgency, category and team match the golden label.",
    rubric_md=(
        "Compare the capability's triage against the reference.\n\n"
        "- **Urgency** exact match (SLA-critical).\n"
        "- **Category** may differ in wording but not in surface.\n"
        "- **Team** must be exactly the reference team.\n"
    ),
    checklist=[
        {
            "id": "urgency",
            "q": "Does urgency match the reference?",
            "weight": 0.4,
            "gate": False,
            "field": "urgency",
        },
        {
            "id": "category",
            "q": "Is the category the same failure surface?",
            "weight": 0.3,
            "gate": False,
            "field": "category",
        },
        {
            "id": "team",
            "q": "Is the routed team exactly correct?",
            "weight": 0.3,
            "gate": False,
            "field": "team",
        },
    ],
    judge_model=JUDGE,
    score_type="numeric",
    score_min=0.0,
    score_max=1.0,
    requires_reference=True,
    created_by=amara,
)
routing_valid = _evaluator(
    support_proj,
    "Valid Routing Team",
    "deterministic",
    D_SUPPORT - 20,
    description="The team field is one of the registered routing teams.",
    score_type="boolean",
    pass_threshold=1.0,
    config={
        "check": "enum_member",
        "field": "team",
        "allowed": [
            "oncall-payments",
            "oncall-platform",
            "billing-support",
            "support-general",
            "risk-ops",
            "integrations",
            "product",
        ],
    },
    created_by=amara,
)
citation_support = _evaluator(
    support_proj,
    "Citation Support",
    "llm_judge",
    D_SUPPORT - 18,
    description="Every factual claim in the answer is supported by a cited article.",
    rubric_md="Fail if any stated fact lacks a citation or cites an article that "
    "does not contain it.",
    judge_model=JUDGE,
    score_type="boolean",
    pass_threshold=1.0,
    checklist=[
        {
            "id": "grounded",
            "q": "Is every claim supported by a cited article?",
            "weight": 1.0,
            "gate": True,
        },
    ],
    created_by=amara,
)
tone_empathy = _evaluator(
    support_proj,
    "Tone & Empathy",
    "llm_judge",
    D_SUPPORT - 18,
    description="Reply is professional, empathetic, and free of blame-shifting.",
    judge_model=JUDGE,
    score_type="numeric",
    rubric_md=(
        "The reply should acknowledge the customer's situation before the fix, stay "
        "professional throughout, and never shift blame onto the customer or another team."
    ),
    checklist=[
        {
            "id": "acknowledges_impact",
            "q": "Does the reply acknowledge the customer's situation before moving to the fix?",
            "weight": 0.35,
            "gate": False,
        },
        {
            "id": "professional_tone",
            "q": "Is the tone professional and free of curtness or sarcasm?",
            "weight": 0.3,
            "gate": False,
        },
        {
            "id": "no_blame_shifting",
            "q": "Does the reply avoid blaming the customer or another team?",
            "weight": 0.25,
            "gate": False,
        },
        {
            "id": "no_hollow_sympathy",
            "q": "Is the empathy specific to this issue rather than boilerplate apology?",
            "weight": 0.1,
            "gate": False,
        },
    ],
    created_by=amara,
)
policy_compliance = _evaluator(
    support_proj,
    "Resolution Policy Compliance",
    "llm_judge",
    D_SUPPORT - 15,
    description="The dispute resolution follows the representment policy.",
    rubric_md=(
        "- Representment requires two independent evidence classes.\n"
        "- Friendly-fraud under $25 is accepted.\n"
        "- The draft never promises a refund timeline.\n"
    ),
    judge_model=JUDGE,
    score_type="boolean",
    pass_threshold=1.0,
    checklist=[
        {
            "id": "evidence",
            "q": "Two evidence classes when representing?",
            "weight": 0.4,
            "gate": True,
        },
        {
            "id": "small-claims",
            "q": "Sub-$25 friendly fraud accepted?",
            "weight": 0.3,
            "gate": False,
        },
        {"id": "no-promises", "q": "No refund-timeline promises?", "weight": 0.3, "gate": True},
    ],
    created_by=priya,
)
tool_sequence = _evaluator(
    support_proj,
    "Tool Sequence Accuracy",
    "trajectory",
    D_SUPPORT - 15,
    description="Ledger lookup and evidence fetch happen before the policy check.",
    score_type="boolean",
    pass_threshold=1.0,
    config={"mode": "match", "reference_source": "expected"},
    created_by=priya,
)
sql_correctness = _evaluator(
    payments_proj,
    "SQL Correctness",
    "llm_judge",
    D_PAYMENTS - 12,
    description="Generated SQL answers the question and matches the reference result.",
    rubric_md="Judge semantic equivalence to the reference SQL: same grain, same "
    "filters, same aggregation. Cosmetic differences do not fail.",
    judge_model=JUDGE,
    score_type="numeric",
    requires_reference=True,
    created_by=diego,
)
sql_syntax = _evaluator(
    payments_proj,
    "SQL Syntax Valid",
    "deterministic",
    D_PAYMENTS - 12,
    description="The sql field parses under the selected dialect.",
    score_type="boolean",
    pass_threshold=1.0,
    config={"check": "sql_parse", "field": "sql"},
    created_by=diego,
)
intent_match = _evaluator(
    payments_proj,
    "Query Intent Accuracy",
    "statistical",
    D_PAYMENTS - 10,
    description="Predicted question category vs analyst-labelled category.",
    score_type="categorical",
    choices=[
        {"label": "revenue", "value": 1.0},
        {"label": "churn", "value": 1.0},
        {"label": "settlement", "value": 1.0},
        {"label": "fraud", "value": 1.0},
        {"label": "operations", "value": 1.0},
    ],
    config={"metric": "accuracy", "label_source": "expected.category"},
    created_by=diego,
)
chart_valid = _evaluator(
    payments_proj,
    "Chart Spec Valid",
    "deterministic",
    D_PAYMENTS - 10,
    description="vega_lite_spec is valid JSON and references only provided columns.",
    score_type="boolean",
    pass_threshold=1.0,
    config={"check": "json_schema_valid", "field": "vega_lite_spec"},
    created_by=diego,
)
field_accuracy = _evaluator(
    expense_proj,
    "Field Extraction Accuracy",
    "llm_judge",
    D_EXPENSE - 8,
    description="Per-field comparison of the extracted record with the reference.",
    judge_model=JUDGE,
    score_type="numeric",
    requires_reference=True,
    checklist=[
        {
            "id": "merchant",
            "q": "Merchant correct?",
            "weight": 0.15,
            "gate": False,
            "field": "merchant",
        },
        {"id": "date", "q": "Date correct?", "weight": 0.15, "gate": False, "field": "date"},
        {
            "id": "total",
            "q": "Total amount exact?",
            "weight": 0.3,
            "gate": False,
            "field": "total_amount",
        },
        {
            "id": "currency",
            "q": "Currency correct?",
            "weight": 0.1,
            "gate": False,
            "field": "currency",
        },
        {
            "id": "tax",
            "q": "Tax amount correct?",
            "weight": 0.1,
            "gate": False,
            "field": "tax_amount",
        },
        {
            "id": "category",
            "q": "Category correct?",
            "weight": 0.2,
            "gate": False,
            "field": "category",
        },
    ],
    created_by=jonas,
)
amount_exact = _evaluator(
    expense_proj,
    "Total Amount Exact",
    "deterministic",
    D_EXPENSE - 8,
    description="total_amount equals the reference to the cent.",
    score_type="boolean",
    pass_threshold=1.0,
    requires_reference=True,
    config={"check": "field_exact", "field": "total_amount"},
    created_by=jonas,
)
policy_verdict = _evaluator(
    expense_proj,
    "Policy Verdict Correct",
    "llm_judge",
    D_EXPENSE - 6,
    description="Approve/flag/reject matches the reference and cites real rules.",
    judge_model=JUDGE,
    score_type="boolean",
    pass_threshold=1.0,
    requires_reference=True,
    rubric_md=(
        "The verdict must match the reference verdict, and every rule cited must be a "
        "real rule that actually applies to this expense."
    ),
    checklist=[
        {
            "id": "verdict_matches",
            "q": "Does the verdict match the reference verdict?",
            "weight": 0.5,
            "gate": True,
        },
        {
            "id": "rules_are_real",
            "q": "Is every cited rule one that appears in the reference rather than invented?",
            "weight": 0.3,
            "gate": True,
        },
        {
            "id": "rules_apply",
            "q": "Does each cited rule actually apply to this expense?",
            "weight": 0.2,
            "gate": False,
        },
    ],
    created_by=jonas,
)
brand_voice = _evaluator(
    growth_proj,
    "Brand Voice",
    "llm_judge",
    D_GROWTH - 4,
    description="Outreach matches the Undermind voice guide: direct, concrete, no hype.",
    judge_model=JUDGE,
    score_type="numeric",
    rubric_md=(
        "The voice guide is signal-first: open with the specific account observation, "
        "stay concrete, and avoid flattery, hype and filler."
    ),
    checklist=[
        {
            "id": "opens_with_signal",
            "q": "Does the message open with a specific observation about this account rather than a generic greeting or flattery?",
            "weight": 0.35,
            "gate": False,
        },
        {
            "id": "concrete_not_vague",
            "q": "Are the claims concrete and specific rather than generic marketing language?",
            "weight": 0.3,
            "gate": False,
        },
        {
            "id": "no_hype",
            "q": "Is the message free of hype words and superlatives?",
            "weight": 0.25,
            "gate": False,
        },
        {
            "id": "direct_ask",
            "q": "Does the message make one clear, direct ask?",
            "weight": 0.1,
            "gate": False,
        },
    ],
    created_by=amara,
)
no_claims = _evaluator(
    growth_proj,
    "No Unsubstantiated Claims",
    "llm_judge",
    D_GROWTH - 4,
    description="No pricing promises or unverifiable performance claims.",
    judge_model=JUDGE,
    score_type="boolean",
    pass_threshold=1.0,
    checklist=[{"id": "claims", "q": "Free of unverifiable claims?", "weight": 1.0, "gate": True}],
    created_by=amara,
)
kyc_correct = _evaluator(
    onboard_proj,
    "Doc Answer Correctness",
    "llm_judge",
    D_ONBOARD - 3,
    description="Required documents match the requirements table for the entity/country.",
    judge_model=JUDGE,
    score_type="numeric",
    requires_reference=True,
    rubric_md=(
        "The listed documents must match the reference requirements for this entity type "
        "and country: none missing, none invented, and the entity/country read correctly."
    ),
    checklist=[
        {
            "id": "no_missing_documents",
            "q": "Does the answer list every document the reference requires?",
            "weight": 0.4,
            "gate": False,
        },
        {
            "id": "no_invented_documents",
            "q": "Is the answer free of documents the reference does not require?",
            "weight": 0.3,
            "gate": False,
        },
        {
            "id": "entity_country_correct",
            "q": "Does the answer address the entity type and country the input specifies?",
            "weight": 0.3,
            "gate": False,
        },
    ],
    created_by=sofia,
)


def _eval_set(capability_, name, born_days, members, *, trace=(), created_by=None):
    """members: list of Evaluator; trace: subset that also live-scores traces."""
    es = EvalSet.objects.create(
        project=capability_.project,
        capability=capability_,
        name=name,
        description=f"Grading rubric for {capability_.name}.",
        created_by=created_by,
    )
    stamp(es, days_ago(born_days), days_ago(born_days))
    order = 0
    member_rows = {}
    for ev in members:
        m = EvalSetMember.objects.create(
            eval_set=es, evaluator=ev, role="generative", enabled=True, order=order
        )
        stamp(m, days_ago(born_days))
        member_rows[ev.name] = m
        order += 1
    trace_rows = {}
    for ev in trace:
        m = EvalSetMember.objects.create(
            eval_set=es, evaluator=ev, role="trace_scoring", enabled=True, order=order
        )
        stamp(m, days_ago(born_days))
        trace_rows[ev.name] = m
        order += 1
    capability_.active_eval_set = es
    capability_.save(update_fields=["active_eval_set"])
    return es, member_rows, trace_rows


triage_set, triage_members, triage_trace = _eval_set(
    triage_capability,
    "Triage rubric",
    D_SUPPORT - 20,
    [triage_accuracy, routing_valid, managed("Correctness"), managed("Conciseness")],
    trace=(triage_accuracy, routing_valid),
    created_by=amara,
)
kb_set, kb_members, kb_trace = _eval_set(
    kb_capability,
    "KB answer quality",
    D_SUPPORT - 18,
    [citation_support, managed("Faithfulness"), tone_empathy],
    trace=(citation_support, managed("Faithfulness")),
    created_by=amara,
)
dispute_set, dispute_members, dispute_trace = _eval_set(
    dispute_capability,
    "Dispute resolution",
    D_SUPPORT - 15,
    [policy_compliance, tool_sequence, managed("Correctness")],
    trace=(policy_compliance,),
    created_by=priya,
)
sql_set, sql_members, sql_trace = _eval_set(
    sql_capability,
    "SQL quality",
    D_PAYMENTS - 12,
    [sql_correctness, sql_syntax, intent_match],
    trace=(sql_syntax,),
    created_by=diego,
)
chart_set, chart_members, _ = _eval_set(
    chart_capability,
    "Chart quality",
    D_PAYMENTS - 10,
    [chart_valid, managed("Faithfulness")],
    created_by=diego,
)
receipt_set, receipt_members, receipt_trace = _eval_set(
    receipt_capability,
    "Extraction quality",
    D_EXPENSE - 8,
    [field_accuracy, amount_exact, managed("JSON Validity")],
    trace=(managed("JSON Validity"), amount_exact),
    created_by=jonas,
)
policy_set, policy_members, _ = _eval_set(
    policy_capability,
    "Policy check quality",
    D_EXPENSE - 6,
    [policy_verdict, managed("Correctness")],
    created_by=jonas,
)
outreach_set, outreach_members, _ = _eval_set(
    outreach_capability,
    "Outreach quality",
    D_GROWTH - 4,
    [brand_voice, no_claims],
    created_by=amara,
)
kyc_set, kyc_members, _ = _eval_set(
    kyc_capability,
    "KYC answers",
    D_ONBOARD - 3,
    [kyc_correct, managed("Faithfulness")],
    created_by=sofia,
)


print("Creating connectors...")

# LIVE but auto_sync off: the demo creds are fake, so the poller must never
# dial out. Manual "Sync now" is the story for how the backfill landed.
langfuse_cred = ConnectorCredential.objects.create(
    project=growth_proj,
    name="Growth prod",
    connector_type="langfuse",
    base_url="https://cloud.langfuse.com",
    api_key="pk-lf-" + hexid(12),
    api_secret="sk-lf-" + hexid(12),
    is_active=True,
    auto_sync_enabled=False,
    last_synced_at=days_ago(0, h=6),
    sync_status="live",
    sync_cursor={"mode": "live", "page": 1, "watermark": days_ago(0, h=6).isoformat()},
    backfill_imported=1187,
    backfill_total=1187,
)
stamp(langfuse_cred, days_ago(D_GROWTH - 1), days_ago(0, h=6))

helicone_cred = ConnectorCredential.objects.create(
    project=payments_proj,
    name="Legacy gateway logs",
    connector_type="helicone",
    api_key="sk-helicone-" + hexid(10),
    is_active=True,
    auto_sync_enabled=False,
    sync_status="idle",
)
stamp(helicone_cred, days_ago(26), days_ago(26))


print("Generating three months of traces (this is the big one)...")

MODEL_RATES = {  # $ per 1M tokens (prompt, completion) — plausible list prices
    "openai/gpt-5.6-sol": (3.0, 12.0),
    "openai/gpt-5.6-terra": (1.1, 4.4),
    "anthropic/claude-sonnet-5": (3.0, 15.0),
    "google/gemini-2.5-pro": (1.25, 10.0),
    EXPENSE_COMPACT_MODEL_ID: (0.05, 0.12),
    DISPUTE_MODEL_ID: (0.08, 0.25),
}


def llm_cost(model, pt, ct):
    rin, rout = MODEL_RATES.get(model, (1.0, 4.0))
    return round((pt * rin + ct * rout) / 1_000_000, 6)


MERCHANTS = [
    "Bloom & Wilder",
    "Kite Coffee Roasters",
    "Northgate Outfitters",
    "Paloma Skincare",
    "Ferrostreet Tools",
    "Casa Verde Foods",
    "Atlas Print Co",
    "Juniper & Sage",
    "Volt Cycle Works",
    "Marrow & Rye Bakery",
    "Hachi Ramen Group",
    "Clearline Optics",
    "Tidepool Surf Supply",
    "Redbrick Furniture",
    "Lumen Candle Studio",
    "Orchard Lane Toys",
    "Baltic Board Games",
    "Sable & Co Leather",
    "Prism Art Supplies",
    "Golden Hour Wines",
]
PLANS = ["starter", "growth", "enterprise"]
PLAN_W = [0.45, 0.38, 0.17]


def wplan():
    r = random.random()
    return "starter" if r < 0.45 else ("growth" if r < 0.83 else "enterprise")


# (template, category, team, base_urgency, enterprise_urgency)
TICKETS = [
    (
        "Payouts to our {bank} account have been stuck in 'in transit' since {day}. "
        "Nothing arrived and our vendors are waiting.",
        "Payouts / Delays",
        "oncall-payments",
        "high",
        "critical",
    ),
    (
        "All API requests started returning 503 about twenty minutes ago. Checkout is "
        "down on our site.",
        "API / Availability",
        "oncall-platform",
        "critical",
        "critical",
    ),
    (
        "Webhook deliveries for payment_intent.succeeded are arriving 10–15 minutes "
        "late since this morning.",
        "API / Webhooks",
        "oncall-platform",
        "high",
        "high",
    ),
    (
        "We were charged twice for the {month} platform invoice — can you check and "
        "refund the duplicate?",
        "Billing",
        "billing-support",
        "medium",
        "medium",
    ),
    (
        "How do I add a second owner to our workspace? The role dropdown only shows Member.",
        "Account / Roles",
        "support-general",
        "low",
        "medium",
    ),
    (
        "The reconciliation CSV export has been stuck at 99% for four hours.",
        "Data / Exports",
        "support-general",
        "high",
        "high",
    ),
    (
        "We're seeing a spike of declined cards from {country} in the last hour — "
        "possible fraud ring?",
        "Risk / Fraud",
        "risk-ops",
        "high",
        "critical",
    ),
    (
        "Your fee report shows 2.9% + 30¢ but our contract says 2.7% + 25¢. Which is right?",
        "Billing / Fees",
        "billing-support",
        "medium",
        "medium",
    ),
    (
        "SSO login loops back to the sign-in page for everyone on our team since the weekend.",
        "Account / SSO",
        "oncall-platform",
        "high",
        "critical",
    ),
    (
        "The terminal SDK throws INVALID_LOCATION on our new {city} store's readers.",
        "Integrations / Terminal",
        "integrations",
        "medium",
        "high",
    ),
    (
        "Can we get sandbox test cards that trigger 3DS challenges? Docs only list "
        "frictionless ones.",
        "Integrations / Sandbox",
        "integrations",
        "low",
        "low",
    ),
    (
        "Feature request: let us schedule payout reports to email every Monday.",
        "Feature Request",
        "product",
        "low",
        "low",
    ),
    (
        "A customer says they were charged but our dashboard shows the payment as "
        "failed. Payment id {pid}.",
        "Payments / Mismatch",
        "oncall-payments",
        "high",
        "high",
    ),
    (
        "Refund {pid} has shown 'processing' for six days — the customer is filing a "
        "chargeback threat.",
        "Payments / Refunds",
        "billing-support",
        "medium",
        "high",
    ),
    (
        "The new dashboard rollout removed our saved report filters. Can they be restored?",
        "Dashboard",
        "support-general",
        "low",
        "medium",
    ),
    (
        "Getting rate-limited at 40 req/s although our plan says 100 req/s burst.",
        "API / Rate limits",
        "oncall-platform",
        "medium",
        "high",
    ),
    (
        "Dispute evidence upload returns 'file type not supported' for PDFs exported from Preview.",
        "Disputes / Evidence",
        "support-general",
        "medium",
        "medium",
    ),
    (
        "Our accountant needs the {month} settlement report broken down by store location.",
        "Reporting",
        "support-general",
        "low",
        "low",
    ),
    (
        "Apple Pay stopped working on Safari 19 after your JS SDK update yesterday.",
        "Integrations / Wallets",
        "integrations",
        "high",
        "critical",
    ),
    (
        "We suspect an employee ran unauthorised refunds — need an audit trail of "
        "refund actions by user.",
        "Risk / Audit",
        "risk-ops",
        "high",
        "high",
    ),
]

KB_QA = [
    (
        "How long do payouts take to reach a UK bank account?",
        "Standard payouts settle in 2 business days for UK accounts; instant payouts "
        "arrive within 30 minutes for a 1% fee.",
        ["payouts-schedule", "instant-payouts"],
    ),
    (
        "Can I issue a partial refund on a disputed payment?",
        "No — once a payment is disputed, refunds are blocked. Respond through the "
        "dispute flow instead; accepting the dispute effectively refunds it.",
        ["disputes-overview", "refunds-limits"],
    ),
    (
        "What's the difference between gross and net settlement in reports?",
        "Gross shows the full charge amount; net deducts Undermind fees and refunds. "
        "The settlement CSV reports net per payout line.",
        ["settlement-reports"],
    ),
    (
        "How do I rotate our API keys without downtime?",
        "Create a second restricted key, deploy it, then revoke the old one. Both "
        "stay valid during the overlap window.",
        ["api-keys-rotation"],
    ),
    (
        "Do you support Strong Customer Authentication exemptions?",
        "Yes — low-value and TRA exemptions are requested automatically; you can "
        "force a challenge with request_three_d_secure=any.",
        ["sca-exemptions", "3ds-guide"],
    ),
    (
        "Why was my instant payout declined?",
        "Instant payouts require a debit-card linked account and a 30-day clean "
        "history; new accounts fall back to standard payouts.",
        ["instant-payouts"],
    ),
    (
        "Can I export disputes with their evidence files?",
        "The dashboard exports dispute metadata as CSV; evidence files must be "
        "downloaded per dispute for compliance reasons.",
        ["disputes-export"],
    ),
    (
        "How are currency conversions priced?",
        "Conversions use the mid-market rate plus 1% at capture time; the applied "
        "rate is stored on the balance transaction.",
        ["fx-pricing"],
    ),
    (
        "Where do I find our merchant category code?",
        "Settings → Business profile shows the MCC we file with networks; contact "
        "support to change it.",
        ["business-profile"],
    ),
    (
        "Does the terminal SDK work offline?",
        "Yes, with offline mode enabled payments queue on-device up to a configured "
        "amount and sync when connectivity returns.",
        ["terminal-offline"],
    ),
]

DISPUTE_REASONS = [
    ("fraudulent", "10.4"),
    ("product_not_received", "13.1"),
    ("duplicate", "12.6.1"),
    ("credit_not_processed", "13.6"),
    ("subscription_canceled", "13.2"),
    ("unrecognized", "10.5"),
]

SQL_QUESTIONS = [
    (
        "What was gross processing volume by week for the last 8 weeks?",
        "SELECT date_trunc('week', captured_at) AS week, SUM(amount) / 100.0 AS gross_gbp "
        "FROM payments WHERE captured_at >= now() - interval '8 weeks' AND status = "
        "'succeeded' GROUP BY 1 ORDER BY 1",
        "revenue",
    ),
    (
        "Which merchants had the highest refund rate last month?",
        "SELECT m.name, COUNT(r.id)::float / NULLIF(COUNT(p.id), 0) AS refund_rate "
        "FROM merchants m JOIN payments p ON p.merchant_id = m.id LEFT JOIN refunds r "
        "ON r.payment_id = p.id WHERE p.captured_at >= date_trunc('month', now()) - "
        "interval '1 month' AND p.captured_at < date_trunc('month', now()) "
        "GROUP BY m.name HAVING COUNT(p.id) > 100 ORDER BY refund_rate DESC LIMIT 20",
        "operations",
    ),
    (
        "How many merchants churned in Q2 (no successful payment in 30 days)?",
        "WITH last_pay AS (SELECT merchant_id, MAX(captured_at) AS last_at FROM payments "
        "WHERE status = 'succeeded' GROUP BY merchant_id) SELECT COUNT(*) FROM last_pay "
        "WHERE last_at < now() - interval '30 days'",
        "churn",
    ),
    (
        "What's the median settlement delay by payout rail?",
        "SELECT rail, percentile_cont(0.5) WITHIN GROUP (ORDER BY settled_at - "
        "initiated_at) AS median_delay FROM payouts WHERE settled_at IS NOT NULL "
        "GROUP BY rail",
        "settlement",
    ),
    (
        "Show the dispute rate by card network this quarter.",
        "SELECT network, COUNT(d.id)::float / NULLIF(COUNT(p.id), 0) AS dispute_rate "
        "FROM payments p LEFT JOIN disputes d ON d.payment_id = p.id WHERE "
        "p.captured_at >= date_trunc('quarter', now()) GROUP BY network",
        "fraud",
    ),
    (
        "Top 10 merchants by net revenue contribution this month.",
        "SELECT m.name, SUM(p.fee_amount) / 100.0 AS fees_gbp FROM merchants m JOIN "
        "payments p ON p.merchant_id = m.id WHERE p.captured_at >= date_trunc('month', "
        "now()) GROUP BY m.name ORDER BY fees_gbp DESC LIMIT 10",
        "revenue",
    ),
    (
        "Average authorisation rate by issuer country, last 30 days.",
        "SELECT issuer_country, AVG(CASE WHEN status IN ('succeeded','captured') THEN 1 "
        "ELSE 0 END) AS auth_rate FROM payment_attempts WHERE created_at >= now() - "
        "interval '30 days' GROUP BY issuer_country HAVING COUNT(*) > 500 ORDER BY "
        "auth_rate ASC",
        "operations",
    ),
    (
        "How much volume ran through instant payouts vs standard last week?",
        "SELECT payout_type, SUM(amount) / 100.0 AS volume_gbp FROM payouts WHERE "
        "initiated_at >= date_trunc('week', now()) - interval '1 week' AND initiated_at "
        "< date_trunc('week', now()) GROUP BY payout_type",
        "settlement",
    ),
    (
        "What share of payments used a wallet (Apple/Google Pay) each month this year?",
        "SELECT date_trunc('month', captured_at) AS month, AVG(CASE WHEN "
        "payment_method IN ('apple_pay','google_pay') THEN 1 ELSE 0 END) AS "
        "wallet_share FROM payments WHERE captured_at >= date_trunc('year', now()) "
        "GROUP BY 1 ORDER BY 1",
        "operations",
    ),
    (
        "Which reason codes drive the most dispute losses by amount?",
        "SELECT reason_code, SUM(amount) / 100.0 AS lost_gbp FROM disputes WHERE "
        "outcome = 'lost' GROUP BY reason_code ORDER BY lost_gbp DESC",
        "fraud",
    ),
    (
        "How long does a merchant take from signup to first successful payment, "
        "on average, by signup month?",
        "SELECT date_trunc('month', m.created_at) AS cohort, AVG(fp.first_at - "
        "m.created_at) AS avg_time_to_first FROM merchants m JOIN (SELECT "
        "merchant_id, MIN(captured_at) AS first_at FROM payments WHERE status = "
        "'succeeded' GROUP BY merchant_id) fp ON fp.merchant_id = m.id GROUP BY 1 "
        "ORDER BY 1",
        "churn",
    ),
    (
        "Monthly recurring platform fee revenue for growth-plan merchants?",
        "SELECT date_trunc('month', charged_at) AS month, SUM(amount) / 100.0 AS "
        "fees_gbp FROM platform_fees pf JOIN merchants m ON m.id = pf.merchant_id "
        "WHERE m.plan = 'growth' GROUP BY 1 ORDER BY 1",
        "revenue",
    ),
]
SQL_PHRASINGS = ["{q}", "Quick one: {q}", "For the board deck — {q}"]

# The prompt the model actually sees, inside the harness — shared by the
# seeded traces and the training corpus so both surfaces stay consistent.
_RECEIPT_SYSTEM = (
    "Extract the expense record fields from the receipt text. Return strict "
    "JSON: merchant, date, total_amount, currency, tax_amount, category."
)

RECEIPTS = [
    (
        "THE LANGHAM HOTEL\n1c Portland Place London\n2 nights deluxe room\nRoom total "
        "£604.00\nVAT (20%) £100.67\nTOTAL £604.00\nVISA ****4242",
        {
            "merchant": "The Langham Hotel",
            "total_amount": 604.0,
            "currency": "GBP",
            "tax_amount": 100.67,
            "category": "travel",
        },
    ),
    (
        "LUFTHANSA AG\nE-ticket 220-48819\nFRA-LHR Economy flex\nFare EUR 342.00\nTaxes "
        "EUR 96.40\nTotal EUR 438.40",
        {
            "merchant": "Lufthansa",
            "total_amount": 438.4,
            "currency": "EUR",
            "tax_amount": 96.4,
            "category": "travel",
        },
    ),
    (
        "DISHOOM KING'S CROSS\nTable 14 — 4 covers\nFood 96.50\nDrinks 38.00\nService "
        "12.5% 16.81\nTOTAL GBP 151.31",
        {
            "merchant": "Dishoom King's Cross",
            "total_amount": 151.31,
            "currency": "GBP",
            "tax_amount": 0.0,
            "category": "meals",
        },
    ),
    (
        "GITHUB INC\nTeam plan — 18 seats\nApr 2026\nSubtotal $72.00\nTax $0.00\nTotal "
        "$72.00\nCard ending 1187",
        {
            "merchant": "GitHub",
            "total_amount": 72.0,
            "currency": "USD",
            "tax_amount": 0.0,
            "category": "software",
        },
    ),
    (
        "BOLT.EU\nRide Tallinn Airport → Old Town\n12 Jun 2026 22:41\nFare €14.80 incl. VAT €2.47",
        {
            "merchant": "Bolt",
            "total_amount": 14.8,
            "currency": "EUR",
            "tax_amount": 2.47,
            "category": "travel",
        },
    ),
    (
        "OFFICEMART LTD\nStanding desk x1 £389.99\nMonitor arm x2 £45.98\nSubtotal "
        "£435.97\nVAT £87.19\nTotal £523.16",
        {
            "merchant": "OfficeMart",
            "total_amount": 523.16,
            "currency": "GBP",
            "tax_amount": 87.19,
            "category": "office",
        },
    ),
    (
        "ANTHROPIC PBC\nAPI usage — May 2026\nAmount due $1,240.55\nTax $0.00",
        {
            "merchant": "Anthropic",
            "total_amount": 1240.55,
            "currency": "USD",
            "tax_amount": 0.0,
            "category": "software",
        },
    ),
    (
        "PRET A MANGER 041\nLatte 3.85\nChicken avocado 5.25\nTOTAL £9.10\nVAT incl £1.52",
        {
            "merchant": "Pret a Manger",
            "total_amount": 9.1,
            "currency": "GBP",
            "tax_amount": 1.52,
            "category": "meals",
        },
    ),
    (
        "HILTON AMSTERDAM\n1 night executive\nRoom EUR 289.00\nCity tax EUR 21.68\n"
        "TOTAL EUR 310.68\nMC ****8817",
        {
            "merchant": "Hilton Amsterdam",
            "total_amount": 310.68,
            "currency": "EUR",
            "tax_amount": 21.68,
            "category": "travel",
        },
    ),
    (
        "TRAINLINE\nLDN Kings Cross → Leeds rtn\nOff-peak £86.30\nBooking fee £1.75\nTotal £88.05",
        {
            "merchant": "Trainline",
            "total_amount": 88.05,
            "currency": "GBP",
            "tax_amount": 0.0,
            "category": "travel",
        },
    ),
    (
        "FIGMA INC\nOrganization plan — 12 editors\nMay 2026\nAmount $540.00\nTax $0.00",
        {
            "merchant": "Figma",
            "total_amount": 540.0,
            "currency": "USD",
            "tax_amount": 0.0,
            "category": "software",
        },
    ),
    (
        "WEWORK MOORGATE\nDay pass x3\nSubtotal £105.00\nVAT (20%) £21.00\nTOTAL £126.00",
        {
            "merchant": "WeWork Moorgate",
            "total_amount": 126.0,
            "currency": "GBP",
            "tax_amount": 21.0,
            "category": "office",
        },
    ),
    (
        "UBER *TRIP\nSF downtown → SFO\nJul 3 2026\nTotal $52.18 incl fees",
        {
            "merchant": "Uber",
            "total_amount": 52.18,
            "currency": "USD",
            "tax_amount": 0.0,
            "category": "travel",
        },
    ),
    (
        "BLAU BAR & KITCHEN BERLIN\nTeam dinner — 6 covers\nSpeisen 214,00\n"
        "Getränke 96,50\nGesamt EUR 310,50\ninkl. MwSt EUR 49,58",
        {
            "merchant": "Blau Bar & Kitchen",
            "total_amount": 310.5,
            "currency": "EUR",
            "tax_amount": 49.58,
            "category": "meals",
        },
    ),
    (
        "AWS EMEA SARL\nCloud services — June 2026\nTotal due USD 1,872.44\nVAT reverse charged",
        {
            "merchant": "Amazon Web Services",
            "total_amount": 1872.44,
            "currency": "USD",
            "tax_amount": 0.0,
            "category": "software",
        },
    ),
    (
        "RYMAN STATIONERY\nWhiteboard markers x12 £18.00\nNotebooks x8 £32.00\n"
        "TOTAL £50.00\nVAT incl £8.33",
        {
            "merchant": "Ryman",
            "total_amount": 50.0,
            "currency": "GBP",
            "tax_amount": 8.33,
            "category": "office",
        },
    ),
]

KYC_QA = [
    (
        "What documents does a UK LLC need to open a merchant account?",
        ["certificate_of_incorporation", "proof_of_address", "director_id", "shareholder_register"],
        "llc",
        "GB",
    ),
    (
        "I'm a sole trader in Ireland — do I need a business registration?",
        ["personal_id", "proof_of_address", "tax_registration"],
        "sole_prop",
        "IE",
    ),
    (
        "Which shareholders need to verify identity for a German GmbH?",
        ["director_id", "ubo_ids_over_25pct", "commercial_register_extract"],
        "corp",
        "DE",
    ),
    (
        "What proof of address documents do you accept for nonprofits?",
        ["utility_bill", "bank_statement", "charity_register_entry"],
        "nonprofit",
        "GB",
    ),
    (
        "Our director's passport is expired — can we still verify?",
        ["director_id", "secondary_id"],
        "llc",
        "GB",
    ),
    (
        "What do you need from a Delaware C-corp with UK operations?",
        [
            "certificate_of_incorporation",
            "ein_letter",
            "director_id",
            "ubo_ids_over_25pct",
            "uk_establishment_proof",
        ],
        "corp",
        "US",
    ),
    (
        "Do French SAS companies need a K-bis extract?",
        ["kbis_extract", "director_id", "ubo_ids_over_25pct"],
        "corp",
        "FR",
    ),
    (
        "What counts as proof of address for a new sole trader?",
        ["utility_bill", "bank_statement", "council_tax_bill"],
        "sole_prop",
        "GB",
    ),
]

OUTREACH_BRIEFS = [
    ("Kite Coffee Roasters", "cafes", "instant payouts eligibility"),
    ("Volt Cycle Works", "retail", "terminal offline mode launch"),
    ("Paloma Skincare", "d2c", "checkout conversion benchmark"),
    ("Baltic Board Games", "d2c", "multi-currency pricing beta"),
    ("Hachi Ramen Group", "restaurants", "QR pay-at-table rollout"),
    ("Clearline Optics", "retail", "3DS exemption uplift"),
]

_TRIAGE_RATIONALES = [
    "Urgency and team match the golden routing for this failure surface.",
    "Category wording differs but the surface (payouts) is the same; team exact.",
    "Correct team; urgency one level above the reference for a growth-plan merchant.",
    "Routed to support-general but the reference routes payout delays to oncall-payments.",
    "Enterprise merchant floored at medium as policy requires; matches reference.",
]
_ROUTING_RATIONALES = [
    "team is a registered routing team.",
    "team value not in the routing registry.",
]

span_buf: list[Span] = []
conv_buf: dict[str, Conversation] = {}
usage_acc: dict = {
    a.pk: {
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
        "llm_calls": 0,
        "tool_calls": 0,
        "cost_usd": 0.0,
        "models": {},
        "_spans": [],
        "last": None,
    }
    for a in capabilities
}
trace_index: dict = {a.pk: [] for a in capabilities}  # (trace_id, t_end, input, output, ok)


execution_buf: list[TaskExecution] = []


def _flush_executions():
    """Live scoring materialises one TaskExecution per scored unit; the seeded
    root block alone leaves the task-execution views and dataset scores empty.
    The session score is the mean over the conversation's executions."""
    if not execution_buf:
        return
    TaskExecution.objects.bulk_create(execution_buf, batch_size=1000)
    backdate(TaskExecution, [(te.pk, te.started_at) for te in execution_buf])
    backdate(TaskExecution, [(te.pk, te.started_at) for te in execution_buf], "updated_at")
    by_conv: dict[str, list[float]] = {}
    for te in execution_buf:
        if te.conversation_id and te.success_score is not None:
            by_conv.setdefault(te.conversation_id, []).append(te.success_score)
    for conv_id, scores in by_conv.items():
        TaskExecution.objects.filter(conversation_id=conv_id).update(
            session_score=round(sum(scores) / len(scores), 4)
        )
    execution_buf.clear()


def _flush_spans():
    if span_buf:
        # bulk_create bypasses Span.save(), so project usage explicitly or the
        # seeded traces show no tokens/cost in every rollup.
        for sp in span_buf:
            sp.usage = usage_slice(sp.attributes)
        Span.objects.bulk_create(span_buf, batch_size=1000)
        span_buf.clear()


def _acc(capability_, span_id, model=None, pt=0, ct=0, cost=0.0, tool=False, end=None):
    u = usage_acc[capability_.pk]
    if model:
        u["llm_calls"] += 1
        u["prompt_tokens"] += pt
        u["completion_tokens"] += ct
        u["total_tokens"] += pt + ct
        u["cost_usd"] = round(u["cost_usd"] + cost, 6)
        u["models"][model] = u["models"].get(model, 0) + 1
    if tool:
        u["tool_calls"] += 1
    u["_spans"].append(span_id)
    if end is not None and (u["last"] is None or end > u["last"]):
        u["last"] = end


def _feedback_block(entries, scored_at):
    block = {}
    for name, member, value, passed, rationale, stype in entries:
        block[name] = {
            "score": value,
            "passed": passed,
            "outcome": "scored",
            "rationale": rationale,
            "scope": "trace",
            "sub_scores": [],
            "eval_set_member_id": str(member.pk),
            "evaluator_id": str(member.evaluator_id),
            "scored_at": scored_at.isoformat(),
        }
        _ = stype
    # The composer's ``_execution`` payload exactly as live scoring persists it.
    composed = composition.compose(block)
    if composed:
        block["_execution"] = composed
    block["_scored_at"] = scored_at.isoformat()
    return {"trace_scoring": block}


def emit_trace(
    capability_,
    t0: datetime,
    inp: dict,
    out,
    *,
    steps,
    error: str | None = None,
    conversation: Conversation | None = None,
    conv_ext: str = "",
    feedback: dict | None = None,
    model: str | None = None,
    model_io: tuple[list[dict], object] | None = None,
):
    """One trace: root span + LLM/tool children per ``steps``.

    steps: list of ("llm", model, pt, ct, ms) | ("tool", name, args, ms, err?).

    ``model_io`` gives the first LLM step its own (messages, completion) and marks
    the root an ``entry_point``, which is what makes a trace two-layer: the model
    call and the answer the capability assembled around it become separate surfaces.
    Returns (trace_id, end_dt).
    """
    model = model or capability_.model
    model_step_pending = model_io is not None
    if t0 > NOW:  # today's partial day: keep every trace in the past
        t0 = NOW - timedelta(minutes=random.randint(5, 110))
    trace_id = hexid(16)
    root_id = hexid(8)
    start_ns = int(t0.timestamp() * 1_000_000_000)
    offset_ns = 0
    for step in steps:
        if step[0] == "llm":
            _, smodel, pt, ct, ms = step
            s_start = start_ns + offset_ns
            s_end = s_start + int(ms * 1e6)
            cost = llm_cost(smodel, pt, ct)
            sid = hexid(8)
            span_buf.append(
                Span(
                    span_id=sid,
                    trace_id=trace_id,
                    parent_span_id=root_id,
                    project=capability_.project,
                    capability=capability_,
                    conversation=conversation,
                    span_type="llm_call",
                    operation="llm.chat",
                    name=f"chat {smodel}",
                    kind=3,
                    start_time_ns=s_start,
                    end_time_ns=s_end,
                    duration_ns=s_end - s_start,
                    status_code=1,
                    service_name=capability_.slug,
                    resource_attrs=_res_attrs(capability_, conv_ext),
                    scope_name="overmind.sdk",
                    scope_version=capability_.cli_version
                    if capability_.cli_version != "unknown"
                    else "0.6.0",
                    feedback_score={},
                    attributes={
                        "genai.model": smodel,
                        "genai.provider": smodel.split("/")[0] if "/" in smodel else "overmind",
                        "genai.prompt_tokens": pt,
                        "genai.completion_tokens": ct,
                        "genai.total_tokens": pt + ct,
                        "genai.cost": cost,
                        "genai.elapsed_seconds": round(ms / 1000, 3),
                        "genai.request.temperature": 0.2,
                        "genai.response.finish_reason": "stop",
                        "genai.streaming": False,
                        **({"conversation.id": conv_ext} if conv_ext else {}),
                        **(
                            {
                                "overmind.input.data": model_io[0],
                                "overmind.output.data": [
                                    {"role": "assistant", "content": json.dumps(model_io[1])}
                                ],
                            }
                            if model_step_pending
                            else {}
                        ),
                    },
                )
            )
            model_step_pending = False
            _acc(capability_, sid, model=smodel, pt=pt, ct=ct, cost=cost)
            offset_ns += int(ms * 1e6)
        else:
            _, tname, targs, ms, *terr = step
            terr = terr[0] if terr else ""
            s_start = start_ns + offset_ns
            s_end = s_start + int(ms * 1e6)
            sid = hexid(8)
            span_buf.append(
                Span(
                    span_id=sid,
                    trace_id=trace_id,
                    parent_span_id=root_id,
                    project=capability_.project,
                    capability=capability_,
                    conversation=conversation,
                    span_type="tool_call",
                    operation=tname,
                    name=tname,
                    kind=1,
                    start_time_ns=s_start,
                    end_time_ns=s_end,
                    duration_ns=s_end - s_start,
                    status_code=2 if terr else 1,
                    status_message=terr,
                    service_name=capability_.slug,
                    resource_attrs=_res_attrs(capability_, conv_ext),
                    scope_name="overmind.sdk",
                    scope_version="0.6.0",
                    feedback_score={},
                    attributes={
                        "tool.name": tname,
                        "tool.arg_keys": sorted(targs),
                        **({"tool.error": terr} if terr else {}),
                        **({"conversation.id": conv_ext} if conv_ext else {}),
                    },
                )
            )
            _acc(capability_, sid, tool=True)
            offset_ns += int(ms * 1e6)
    end_ns = start_ns + offset_ns + int(rnd(20, 90) * 1e6)
    root_attrs = {
        "overmind.input.data": inp,
        "overmind.span.type": "entry_point" if model_io is not None else "llm_call",
        **({"conversation.id": conv_ext} if conv_ext else {}),
    }
    if error:
        root_attrs["overmind.error.type"] = "AgentExecutionError"
        root_attrs["overmind.error.message"] = error
        root_attrs["overmind.status"] = "failed"
    else:
        root_attrs["overmind.output.data"] = out
        root_attrs["overmind.status"] = "success"
    span_buf.append(
        Span(
            span_id=root_id,
            trace_id=trace_id,
            parent_span_id=None,
            project=capability_.project,
            capability=capability_,
            conversation=conversation,
            span_type="entry_point" if model_io is not None else "llm_call",
            operation=f"{capability_.slug}.run",
            name=f"{capability_.slug}.run",
            kind=2,
            start_time_ns=start_ns,
            end_time_ns=end_ns,
            duration_ns=end_ns - start_ns,
            status_code=2 if error else 1,
            status_message=error or "",
            service_name=capability_.slug,
            resource_attrs=_res_attrs(capability_, conv_ext),
            scope_name="overmind.sdk",
            scope_version="0.6.0",
            feedback_score=feedback or {},
            attributes=root_attrs,
        )
    )
    end_dt = datetime.fromtimestamp(end_ns / 1e9, tz=UTC)
    composed = ((feedback or {}).get("trace_scoring") or {}).get("_execution") or {}
    if composed.get("score") is not None:
        execution_buf.append(
            TaskExecution(
                project=capability_.project,
                capability=capability_,
                trace_id=trace_id,
                unit_span_id=root_id,
                conversation_id=conv_ext or "",
                binding_source="unbound",
                status="error" if error else "completed",
                terminal_kind="delivery",
                success_score=composed["score"],
                user_intent={
                    "text": str(next(iter(inp.values()), ""))[:200],
                    "source": "first_message",
                }
                if isinstance(inp, dict) and inp
                else {},
                started_at=datetime.fromtimestamp(start_ns / 1e9, tz=UTC),
                duration_ms=(end_ns - start_ns) // 1_000_000,
            )
        )
    _acc(capability_, root_id, end=end_dt)
    trace_index[capability_.pk].append((trace_id, end_dt, inp, out, error is None))
    if len(span_buf) >= 2000:
        _flush_spans()
    return trace_id, end_dt


def _res_attrs(capability_, conv_ext=""):
    attrs = {
        "service.name": capability_.slug,
        "deployment.environment": "production",
        "telemetry.sdk.name": "overmind",
        "telemetry.sdk.language": "python",
        "overmind.capability.id": str(capability_.pk),
        "overmind.project.id": str(capability_.project_id),
    }
    return attrs


def _session(project, capability_, t: datetime) -> tuple[Conversation, str]:
    ext = f"sess_{hexid(8)}"
    conv = Conversation.objects.create(project=project, external_id=ext, capability=capability_)
    stamp(conv, t)
    conv_buf[ext] = conv
    return conv, ext


def gen_triage_day(day: datetime, n: int):
    for _ in range(n):
        t0 = business_hour(day)
        merchant = pick(MERCHANTS)
        plan = wplan()
        tmpl, category, team, base_u, ent_u = pick(TICKETS)
        text = tmpl.format(
            bank=pick(["Barclays", "Monzo", "HSBC", "Revolut Business"]),
            day=pick(["Monday", "Tuesday", "yesterday morning"]),
            month=pick(["April", "May", "June", "July"]),
            country=pick(["Brazil", "Vietnam", "Nigeria", "Romania"]),
            city=pick(["Leeds", "Austin", "Rotterdam", "Lyon"]),
            pid=f"pay_{hexid(6)}",
        )
        urgency = ent_u if plan == "enterprise" else base_u
        ok = random.random() > 0.03
        good = random.random() > 0.13
        out_team = team if good else pick(["support-general", "billing-support", "product"])
        out_urg = (
            urgency
            if good or random.random() < 0.5
            else ("medium" if urgency in ("high", "critical") else "high")
        )
        inp = {"ticket_text": text, "merchant_plan": plan, "previous_tickets": random.randint(0, 9)}
        out = {
            "urgency": out_urg,
            "category": category,
            "team": out_team,
            "summary": text.split(".")[0][:110] + ".",
        }
        conv = conv_ext = None
        if random.random() < 0.22:
            conv, conv_ext = _session(support_proj, triage_capability, t0)
        steps = [
            ("tool", "lookup_merchant", {"merchant_name": merchant}, rnd(60, 220, 1)),
            (
                "llm",
                triage_capability.model,
                random.randint(700, 1400),
                random.randint(90, 220),
                rnd(700, 2400, 1),
            ),
        ]
        if random.random() < 0.4:
            steps.insert(1, ("tool", "search_kb", {"query": category.lower()}, rnd(90, 400, 1)))
        fb = None
        if ok and random.random() < 0.86:
            acc = rnd(0.72, 1.0) if good else rnd(0.15, 0.55)
            team_ok = out_team in {
                "oncall-payments",
                "oncall-platform",
                "billing-support",
                "support-general",
                "risk-ops",
                "integrations",
                "product",
            }
            fb = _feedback_block(
                [
                    (
                        "Triage Accuracy",
                        triage_trace["Triage Accuracy"],
                        round(acc, 2),
                        None,
                        pick(_TRIAGE_RATIONALES),
                        "numeric",
                    ),
                    (
                        "Valid Routing Team",
                        triage_trace["Valid Routing Team"],
                        1.0 if team_ok else 0.0,
                        team_ok,
                        _ROUTING_RATIONALES[0 if team_ok else 1],
                        "boolean",
                    ),
                ],
                t0 + timedelta(minutes=random.randint(2, 7)),
            )
        emit_trace(
            triage_capability,
            t0,
            inp,
            out,
            steps=steps,
            error=None if ok else "LLM provider timeout after 3 retries",
            conversation=conv,
            conv_ext=conv_ext or "",
            feedback=fb,
        )
        # Session follow-up: the merchant asks a KB question in the same thread.
        if conv is not None and random.random() < 0.7:
            q, a, cites = pick(KB_QA)
            t1 = t0 + timedelta(minutes=random.randint(3, 25))
            fb2 = None
            if random.random() < 0.8:
                grounded = random.random() > 0.1
                fb2 = _feedback_block(
                    [
                        (
                            "Citation Support",
                            kb_trace["Citation Support"],
                            1.0 if grounded else 0.0,
                            grounded,
                            "All claims trace to the cited articles."
                            if grounded
                            else "Second paragraph states a fee not present in any cited article.",
                            "boolean",
                        ),
                        (
                            "Faithfulness",
                            kb_trace["Faithfulness"],
                            rnd(0.7, 1.0),
                            None,
                            "Answer stays within the retrieved articles.",
                            "numeric",
                        ),
                    ],
                    t1 + timedelta(minutes=3),
                )
            emit_trace(
                kb_capability,
                t1,
                {"question": q, "merchant_plan": plan},
                {"answer": a, "citations": cites, "confidence": rnd(0.62, 0.97)},
                steps=[
                    ("tool", "search_kb", {"query": q[:40]}, rnd(120, 500, 1)),
                    ("tool", "fetch_article", {"slug": cites[0]}, rnd(40, 160, 1)),
                    (
                        "llm",
                        kb_capability.model,
                        random.randint(1600, 3200),
                        random.randint(180, 420),
                        rnd(1500, 4800, 1),
                    ),
                ],
                conversation=conv,
                conv_ext=conv_ext,
                feedback=fb2,
            )


def gen_kb_day(day: datetime, n: int):
    for _ in range(n):
        t0 = business_hour(day)
        q, a, cites = pick(KB_QA)
        ok = random.random() > 0.02
        fb = None
        if ok and random.random() < 0.8:
            grounded = random.random() > 0.09
            fb = _feedback_block(
                [
                    (
                        "Citation Support",
                        kb_trace["Citation Support"],
                        1.0 if grounded else 0.0,
                        grounded,
                        "Every factual claim carries a supporting citation."
                        if grounded
                        else "The SLA claim cites payouts-schedule, which covers timing "
                        "but not SLAs.",
                        "boolean",
                    ),
                    (
                        "Faithfulness",
                        kb_trace["Faithfulness"],
                        rnd(0.66, 1.0),
                        None,
                        "No content beyond the retrieved articles.",
                        "numeric",
                    ),
                ],
                t0 + timedelta(minutes=4),
            )
        emit_trace(
            kb_capability,
            t0,
            {"question": q, "merchant_plan": wplan()},
            {"answer": a, "citations": cites, "confidence": rnd(0.6, 0.97)},
            steps=[
                ("tool", "search_kb", {"query": q[:40]}, rnd(120, 520, 1)),
                ("tool", "fetch_article", {"slug": cites[0]}, rnd(40, 170, 1)),
                (
                    "llm",
                    kb_capability.model,
                    random.randint(1500, 3300),
                    random.randint(170, 430),
                    rnd(1400, 5200, 1),
                ),
            ],
            error=None if ok else "search_kb: upstream retrieval 502",
            feedback=fb,
        )


def gen_dispute_day(day: datetime, n: int):
    for _ in range(n):
        t0 = business_hour(day)
        reason, code = pick(DISPUTE_REASONS)
        amount = round(random.choice([rnd(8, 24), rnd(30, 240), rnd(250, 4200)]), 2)
        dispute_id = f"dp_{hexid(6)}"
        small_fraud = reason == "fraudulent" and amount < 25
        resolution = (
            "accept"
            if small_fraud
            else pick(["represent", "represent", "request_evidence", "accept"])
        )
        evidence = (
            []
            if resolution == "accept"
            else pick(
                [
                    ["delivery_confirmation", "avs_match"],
                    ["customer_comms", "cvv_match"],
                    ["delivery_confirmation", "customer_comms", "prior_undisputed_charges"],
                ]
            )
        )
        ok = random.random() > 0.04
        compliant = random.random() > 0.12
        inp = {
            "dispute_id": dispute_id,
            "reason_code": code,
            "amount": amount,
            "merchant_note": f"{pick(MERCHANTS)} — {reason.replace('_', ' ')}",
        }
        out = {
            "resolution": resolution,
            "draft_reply": (
                f"We reviewed dispute {dispute_id} (reason {code}). "
                + (
                    "Given the amount involved we recommend accepting."
                    if resolution == "accept"
                    else "The evidence on file supports the original charge and we "
                    "recommend representment."
                )
            ),
            "evidence_used": evidence,
        }
        fb = None
        if ok and random.random() < 0.82:
            fb = _feedback_block(
                [
                    (
                        "Resolution Policy Compliance",
                        dispute_trace["Resolution Policy Compliance"],
                        1.0 if compliant else 0.0,
                        compliant,
                        "Two evidence classes support representment; no timeline "
                        "promises in the draft."
                        if compliant
                        else "Represented with a single evidence class — policy requires two.",
                        "boolean",
                    )
                ],
                t0 + timedelta(minutes=5),
            )
        emit_trace(
            dispute_capability,
            t0,
            inp,
            out,
            steps=[
                ("tool", "lookup_transaction", {"dispute_id": dispute_id}, rnd(80, 260, 1)),
                ("tool", "fetch_dispute_evidence", {"dispute_id": dispute_id}, rnd(200, 900, 1)),
                ("tool", "check_policy", {"reason_code": code, "amount": amount}, rnd(30, 120, 1)),
                (
                    "llm",
                    dispute_capability.model,
                    random.randint(2400, 4800),
                    random.randint(260, 620),
                    rnd(2600, 7800, 1),
                ),
            ],
            error=None if ok else "fetch_dispute_evidence: evidence store timeout",
            feedback=fb,
        )


def gen_sql_day(day: datetime, n: int):
    for _ in range(n):
        t0 = business_hour(day)
        q, sql, cat = pick(SQL_QUESTIONS)
        ok = random.random() > 0.05
        valid = random.random() > 0.07
        fb = None
        if ok and random.random() < 0.75:
            fb = _feedback_block(
                [
                    (
                        "SQL Syntax Valid",
                        sql_trace["SQL Syntax Valid"],
                        1.0 if valid else 0.0,
                        valid,
                        "Parses cleanly under postgres."
                        if valid
                        else "Window frame clause rejected by the postgres parser.",
                        "boolean",
                    )
                ],
                t0 + timedelta(minutes=3),
            )
        emit_trace(
            sql_capability,
            t0,
            {"question": q, "dialect": "postgres"},
            {
                "sql": sql,
                "explanation": f"Computes: {q[:70].rstrip('?')}.",
                "confidence": rnd(0.62, 0.97),
            },
            steps=[
                ("tool", "list_tables", {"schema": "analytics"}, rnd(40, 140, 1)),
                (
                    "llm",
                    sql_capability.model,
                    random.randint(1800, 3600),
                    random.randint(160, 420),
                    rnd(1300, 4200, 1),
                ),
                (
                    "tool",
                    "execute_sql",
                    {"sql": sql[:60]},
                    rnd(300, 2600, 1),
                    "" if ok else "canceling statement due to statement timeout",
                ),
            ]
            + (
                [
                    (
                        "llm",
                        sql_capability.model,
                        random.randint(900, 1600),
                        random.randint(120, 260),
                        rnd(800, 2200, 1),
                    )
                ]
                if ok
                else []
            ),
            error=None if ok else "execute_sql failed: statement timeout (10s)",
            feedback=fb,
        )
        if ok and random.random() < 0.4:
            t1 = t0 + timedelta(seconds=random.randint(20, 90))
            emit_trace(
                chart_capability,
                t1,
                {
                    "question": q,
                    "columns": ["week", "value"],
                    "rows_preview": [["2026-06-01", 128400.5]],
                },
                {
                    "vega_lite_spec": {
                        "mark": "line",
                        "encoding": {"x": {"field": "week"}, "y": {"field": "value"}},
                    },
                    "caption": f"Trend for: {q[:60].rstrip('?')}.",
                },
                steps=[
                    (
                        "llm",
                        chart_capability.model,
                        random.randint(900, 1700),
                        random.randint(140, 320),
                        rnd(700, 2100, 1),
                    )
                ],
            )


def gen_receipt_day(day: datetime, n: int, swap_day: datetime):
    for _ in range(n):
        t0 = business_hour(day)
        ocr, record = pick(RECEIPTS)
        # Each real submission is a distinct scan: receipt no. + till noise.
        ocr = f"{ocr}\nReceipt {hexid(3).upper()}-{random.randint(100, 999)}"
        model = receipt_capability.model if t0 >= swap_day else "openai/gpt-5.6-terra"
        ok = random.random() > 0.02
        json_ok = random.random() > 0.05
        exact = random.random() > 0.1
        out = dict(record)
        out["date"] = (t0 - timedelta(days=random.randint(1, 20))).date().isoformat()
        if not exact:
            out["total_amount"] = round(out["total_amount"] + pick([-1, 1]) * rnd(0.5, 9), 2)
        # The model extracts the fields; the harness files them as an expense and
        # returns that record. Two surfaces, and only the inner one is trainable.
        extracted = dict(out)
        out = {
            **extracted,
            "expense_id": f"EXP-{hexid(3).upper()}",
            "status": "submitted",
        }
        fb = None
        if ok and random.random() < 0.8:
            fb = _feedback_block(
                [
                    (
                        "JSON Validity",
                        receipt_trace["JSON Validity"],
                        1.0 if json_ok else 0.0,
                        json_ok,
                        "Output parses and matches the schema."
                        if json_ok
                        else "tax_amount emitted as a string with a currency symbol.",
                        "boolean",
                    ),
                    (
                        "Total Amount Exact",
                        receipt_trace["Total Amount Exact"],
                        1.0 if exact else 0.0,
                        exact,
                        "Matches the reference total to the cent."
                        if exact
                        else "Picked the pre-VAT subtotal instead of the total.",
                        "boolean",
                    ),
                ],
                t0 + timedelta(minutes=2),
            )
        emit_trace(
            receipt_capability,
            t0,
            {"ocr_text": ocr, "hint_currency": record["currency"]},
            out,
            steps=[
                (
                    "llm",
                    model,
                    random.randint(500, 1100),
                    random.randint(90, 200),
                    rnd(300, 900, 1) if model == EXPENSE_COMPACT_MODEL_ID else rnd(800, 2200, 1),
                )
            ],
            error=None if ok else "output failed JSON schema validation after 2 retries",
            feedback=fb,
            model=model,
            model_io=(
                [
                    {"role": "system", "content": _RECEIPT_SYSTEM},
                    {"role": "user", "content": ocr},
                ],
                extracted,
            ),
        )
        if ok and random.random() < 0.5:
            t1 = t0 + timedelta(seconds=random.randint(5, 40))
            level = pick(["ic", "ic", "manager", "exec"])
            over = record["category"] == "meals" and record["total_amount"] > 120
            emit_trace(
                policy_capability,
                t1,
                {"record": out, "employee_level": level},
                {
                    "verdict": "flag" if over else "approve",
                    "violated_rules": (["meals-per-diem-40"] if over else []),
                    "note": "Meal exceeds the per-diem cap; needs manager approval."
                    if over
                    else "Within policy.",
                },
                steps=[
                    (
                        "tool",
                        "fetch_policy_rule",
                        {"category": record["category"]},
                        rnd(30, 110, 1),
                    ),
                    (
                        "llm",
                        policy_capability.model,
                        random.randint(900, 1900),
                        random.randint(110, 260),
                        rnd(900, 2600, 1),
                    ),
                ],
            )


def gen_kyc_day(day: datetime, n: int):
    for _ in range(n):
        t0 = business_hour(day)
        q, docs, entity, country = pick(KYC_QA)
        emit_trace(
            kyc_capability,
            t0,
            {"question": q, "entity_type": entity, "country": country},
            {
                "answer": f"For a {entity.replace('_', ' ')} in {country} you'll need: "
                + ", ".join(d.replace("_", " ") for d in docs)
                + ".",
                "required_documents": docs,
            },
            steps=[
                (
                    "tool",
                    "lookup_requirements",
                    {"entity_type": entity, "country": country},
                    rnd(30, 120, 1),
                ),
                (
                    "llm",
                    kyc_capability.model,
                    random.randint(1100, 2200),
                    random.randint(130, 300),
                    rnd(1100, 3400, 1),
                ),
            ],
        )


def gen_outreach_day(day: datetime, n: int):
    """Langfuse-imported spans: connector-shaped, hash ids, never live-scored."""
    for _ in range(n):
        t0 = business_hour(day)
        merchant, segment, signal = pick(OUTREACH_BRIEFS)
        ext_id = f"lf-{hexid(12)}"
        trace_id = hashlib.sha256(f"langfuse:{ext_id}".encode()).hexdigest()[:32]
        root_id = hashlib.sha256(f"langfuse:{ext_id}:root".encode()).hexdigest()[:16]
        start_ns = int(t0.timestamp() * 1e9)
        ms = rnd(1800, 5200, 1)
        end_ns = start_ns + int(ms * 1e6)
        body = (
            f"Hi {merchant.split(' ')[0]} team — saw you're growing in {segment}. "
            f"Merchants like you are using our {signal.split(' ')[0]} features to "
            "settle faster. Worth a 15-minute look this week?"
        )
        span_buf.append(
            Span(
                span_id=root_id,
                trace_id=trace_id,
                parent_span_id=None,
                project=growth_proj,
                capability=outreach_capability,
                span_type="llm_call",
                operation="outreach.compose",
                name="outreach.compose",
                kind=2,
                start_time_ns=start_ns,
                end_time_ns=end_ns,
                duration_ns=end_ns - start_ns,
                status_code=1,
                service_name="outreach-composer",
                resource_attrs={
                    "service.name": "outreach-composer",
                    "connector.source": "langfuse",
                },
                scope_name="langfuse-import",
                scope_version="",
                feedback_score={},
                attributes={
                    "connector.source": "langfuse",
                    "connector.external_id": ext_id,
                    "overmind.input.data": {
                        "merchant_name": merchant,
                        "segment": segment,
                        "signal": signal,
                    },
                    "overmind.output.data": {
                        "subject": f"{signal.capitalize()} for {merchant}",
                        "body": body,
                        "cta": "Book 15 minutes",
                    },
                    "genai.model": outreach_capability.model,
                    "genai.prompt_tokens": random.randint(600, 1200),
                    "genai.completion_tokens": random.randint(120, 260),
                },
            )
        )
        _acc(
            outreach_capability,
            root_id,
            model=outreach_capability.model,
            pt=800,
            ct=180,
            cost=llm_cost(outreach_capability.model, 800, 180),
            end=datetime.fromtimestamp(end_ns / 1e9, tz=UTC),
        )
        trace_index[outreach_capability.pk].append(
            (
                trace_id,
                datetime.fromtimestamp(end_ns / 1e9, tz=UTC),
                {"merchant_name": merchant, "segment": segment, "signal": signal},
                {
                    "subject": f"{signal.capitalize()} for {merchant}",
                    "body": body,
                    "cta": "Book 15 minutes",
                },
                True,
            )
        )


GENERATORS = [
    (gen_triage_day, D_SUPPORT - 3, 11, None),
    (gen_kb_day, D_SUPPORT - 3, 5, None),
    (gen_dispute_day, D_SUPPORT - 2, 4, None),
    (gen_sql_day, D_PAYMENTS - 2, 6, None),
    (gen_receipt_day, D_EXPENSE - 2, 7, days_ago(39)),
    (gen_kyc_day, D_ONBOARD - 2, 4, None),
    (gen_outreach_day, D_GROWTH - 1, 4, None),
]

for gen, age, base, extra in GENERATORS:
    for i in range(age + 1):
        day = days_ago(age - i)
        n = daily_volume(base, age, i, day.weekday())
        if extra is not None:
            gen(day, n, extra)
        else:
            gen(day, n)


# Both traces sit >2h in the past, outside the trace-scoring sweep's lookback,
# so nothing re-drives them.

print("Seeding conflict + skip showcase traces...")

# (a) Members land 0.92 apart, so ``compose`` stamps ``_execution.conflict``.
_cq, _ca, _ccites = KB_QA[0]
_conflict_t0 = NOW - timedelta(hours=2, minutes=20)
_conflict_feedback = _feedback_block(
    [
        (
            "Citation Support",
            kb_trace["Citation Support"],
            0.0,
            False,
            "The 2-business-day settlement claim cites payouts-schedule, which "
            "gives timing bands but no settlement guarantee.",
            "boolean",
        ),
        (
            "Faithfulness",
            kb_trace["Faithfulness"],
            0.92,
            None,
            "Aside from the settlement sentence, every claim stays within the retrieved articles.",
            "numeric",
        ),
    ],
    _conflict_t0 + timedelta(minutes=4),
)
assert "conflict" in _conflict_feedback["trace_scoring"]["_execution"]
emit_trace(
    kb_capability,
    _conflict_t0,
    {"question": _cq, "merchant_plan": "growth"},
    {"answer": _ca, "citations": _ccites, "confidence": 0.91},
    steps=[
        ("tool", "search_kb", {"query": _cq}, 320.0),
        ("tool", "fetch_article", {"slug": _ccites[0]}, 95.0),
        ("llm", kb_capability.model, 2100, 240, 2800.0),
    ],
    feedback=_conflict_feedback,
)

# (b) First turn only clarifies: terminal-grain members skip it
# (``_skipped_members`` + ``skip:grain`` Verdict rows) and score the terminal turn.
_sq, _sa, _scites = KB_QA[1]
_skip_t0 = NOW - timedelta(hours=2, minutes=40)
_skip_trace_id = hexid(16)
_skip_root_id = hexid(8)
_skip_scored_at = (_skip_t0 + timedelta(minutes=9)).isoformat()


def _showcase_span(span_id, parent_id, name, start_offset_s, dur_s, attrs, feedback):
    s_start = int((_skip_t0 + timedelta(seconds=start_offset_s)).timestamp() * 1e9)
    s_end = s_start + int(dur_s * 1e9)
    span_buf.append(
        Span(
            span_id=span_id,
            trace_id=_skip_trace_id,
            parent_span_id=parent_id,
            project=kb_capability.project,
            capability=kb_capability,
            span_type="task" if parent_id else "entry_point",
            operation=name,
            name=name,
            kind=2,
            start_time_ns=s_start,
            end_time_ns=s_end,
            duration_ns=s_end - s_start,
            status_code=1,
            service_name=kb_capability.slug,
            resource_attrs=_res_attrs(kb_capability),
            scope_name="overmind.sdk",
            scope_version="0.6.0",
            feedback_score=feedback,
            attributes=attrs,
        )
    )


_turn1_id = hexid(8)
_showcase_span(
    _turn1_id,
    _skip_root_id,
    "turn",
    2,
    14,
    {
        "overmind.unit_kind": "turn",
        "overmind.input.data": {"message": "Can I refund a disputed payment?"},
        "overmind.output.data": {
            "message": "Do you mean a full refund, or a partial refund on the disputed amount?"
        },
    },
    {
        "trace_scoring": {
            "_skipped_members": ["Citation Support", "Faithfulness"],
            "_scored_at": _skip_scored_at,
        }
    },
)
_turn2_id = hexid(8)
_showcase_span(
    _turn2_id,
    _skip_root_id,
    "turn",
    30,
    22,
    {
        "overmind.unit_kind": "turn",
        "overmind.input.data": {"message": "A partial refund."},
        "overmind.output.data": {"message": _sa},
    },
    _feedback_block(
        [
            (
                "Citation Support",
                kb_trace["Citation Support"],
                1.0,
                True,
                "Both claims trace to the cited dispute and refund articles.",
                "boolean",
            ),
            (
                "Faithfulness",
                kb_trace["Faithfulness"],
                0.94,
                None,
                "The answer stays within the retrieved articles.",
                "numeric",
            ),
        ],
        _skip_t0 + timedelta(minutes=9),
    ),
)
_showcase_span(
    _skip_root_id,
    None,
    f"{kb_capability.slug}.run",
    0,
    55,
    {
        "overmind.span.type": "entry_point",
        "overmind.input.data": {"question": _sq, "merchant_plan": "scale"},
        "overmind.output.data": {"answer": _sa, "citations": _scites, "confidence": 0.88},
        "overmind.status": "success",
    },
    {"trace_scoring": {"_scored_at": _skip_scored_at}},
)

# The UI reads the skip reason from these Verdict rows.
_grain_skip_reason = (
    "Skipped: terminal-grain claim binds at the trace's terminal unit; this "
    "unit is not it. Retryable — the row is overwritten when the member "
    "dispatches here."
)
_skip_rows = [
    eval_dispatch.skip_kwargs(
        _skip_member,
        eval_dispatch.SKIP_GRAIN,
        _grain_skip_reason,
        project_id=str(kb_capability.project_id),
        target_id=_turn1_id,
        identifier=eval_dispatch.member_identifier(_skip_member, _skip_member.evaluator.spec),
    )
    for _skip_member in (kb_trace["Citation Support"], kb_trace["Faithfulness"])
]
eval_dispatch.persist_verdicts(_skip_rows)

_flush_spans()
_flush_executions()

# received_at must mirror the trace timeline: the trace-scoring sweep only
# looks at the last two hours of arrivals, and "recent traces" UIs read it.
with connection.cursor() as cur:
    cur.execute(
        f'UPDATE "{Span._meta.db_table}" '  # noqa: S608
        "SET received_at = to_timestamp(end_time_ns / 1e9) "
        "WHERE project_id::text = ANY(%s)",
        [[str(p.pk) for p in seed_projects]],
    )

# Conversations were stamped at creation; capability usage rollups mirror what OTLP
# ingest accumulates per span.
for a in capabilities:
    u = usage_acc[a.pk]
    a.usage_stats = {
        "prompt_tokens": u["prompt_tokens"],
        "completion_tokens": u["completion_tokens"],
        "total_tokens": u["total_tokens"],
        "llm_calls": u["llm_calls"],
        "tool_calls": u["tool_calls"],
        "cost_usd": round(u["cost_usd"], 6),
        "models": u["models"],
        "_spans": u["_spans"][-2000:],
        "updated_at": (u["last"] or NOW).isoformat(),
    }
    a.last_activity_at = u["last"]
    a.save(update_fields=["usage_stats", "last_activity_at"])

n_spans = Span.objects.filter(project__in=seed_projects).count()
print(f"   {n_spans} spans across {sum(len(v) for v in trace_index.values())} traces")


print("Building datasets...")


def _stamp_dataset(ds, born):
    stamp(ds, born, born + timedelta(hours=1))
    for i, c in enumerate(ds.cells.order_by("position")):
        Cell.objects.filter(pk=c.pk).update(created_at=born + timedelta(hours=1, minutes=i))


_INTENT_OF = {"eval": "eval", "ft": "train", "unstructured": "pending"}


def _run_and_check(ds, want, born):
    notebook_run.execute(ds)
    ds.refresh_from_db()
    active = ds.active_cell
    expected = _INTENT_OF[want]
    if active is None:
        ok, reason = False, "no version ran"
    elif expected == "pending":
        ok, reason = True, ""
    else:
        ok, reason = active.fits(expected)
    if ds.state != "idle" or not ok:
        raise RuntimeError(f"seed dataset {ds.name!r}: wanted {expected} — {ds.error or reason}")
    _stamp_dataset(ds, born)
    return ds


def _shape_rows(rows, intent, input_paths=None):
    """Eval rows land as `input` (an object of the remaining fields, or the
    named paths) plus `expected_output`; persona and tags stay as columns."""
    if intent != "eval":
        return [dict(r) for r in rows]
    shaped = []
    for row in rows:
        row = dict(row)
        expected = row.pop("expected_output", None)
        meta = {k: row.pop(k) for k in ("persona", "tags") if k in row}
        inp = {k: row[k] for k in input_paths if k in row} if input_paths else row
        shaped.append({"input": inp, "expected_output": expected, **meta})
    return shaped


def _ingest(name, rows, intent, *, project, capability=None, born=None, input_paths=None):
    ds = Dataset.objects.create(
        capability=capability,
        project=project,
        name=name,
        intent=_INTENT_OF[intent],
        source_spec={"pasted": True},
    )
    dataset_land.land_rows(ds, _shape_rows(rows, intent, input_paths), spec={"pasted": True})
    return _run_and_check(ds, intent, born)


_TRACE_EVAL_SCRIPT = (
    "df = df[df['input'].notna() & df['output'].notna()]\n"
    "df = df.rename(columns={'output': 'expected_output'})\n"
    "df = df.drop(columns=[c for c in ('messages', 'tools', 'tool_calls') if c in df.columns])\n"
)


def _from_traces(capability_, name, *, want, intent, born, only_ok=True):
    entries = [e for e in trace_index[capability_.pk] if e[4] or not only_ok]
    step = max(1, len(entries) // want)
    chosen = entries[::step][:want]
    ds = Dataset.objects.create(
        capability=capability_,
        project=capability_.project,
        name=name,
        intent=_INTENT_OF[intent],
        source_kind="traces",
    )
    dataset_land.land_traces(ds, {"trace_ids": [tid for tid, *_ in chosen]})
    if intent == "eval":
        dataset_lifecycle.add_cell(ds, title="Eval pairs", script=_TRACE_EVAL_SCRIPT)
    return _run_and_check(ds, intent, born)


# — Support: triage golden, curated from real traces mid-May —
triage_golden = _from_traces(
    triage_capability,
    "Triage Golden Set",
    want=60,
    intent="eval",
    born=days_ago(D_SUPPORT - 22),
)

# — Support: KB answer eval, hand-written references —
kb_rows = []
for i, (q, a, cites) in enumerate(KB_QA * 4):
    kb_rows.append(
        {
            "question": q,
            "merchant_plan": PLANS[i % 3],
            "expected_output": {"answer": a, "citations": cites},
            "persona": pick(["merchant-admin", "developer", "finance-lead"]),
            "tags": ["kb", cites[0].split("-")[0]],
        }
    )
kb_eval = _ingest(
    "KB Answers — Golden",
    kb_rows[:40],
    "eval",
    project=support_proj,
    capability=kb_capability,
    born=days_ago(D_SUPPORT - 26),
)

# — Support: dispute resolutions training corpus (the flagship Train set) —
_DISPUTE_SYSTEM = (
    "You are Undermind's dispute resolver. Use the ledger and evidence tools, "
    "apply the representment policy, and return strict JSON with resolution, "
    "draft_reply and evidence_used."
)
_DISPUTE_TOOLS = [
    _tool_decl(
        "lookup_transaction", "Fetch the ledger row for a dispute.", {"dispute_id": "string"}
    ),
    _tool_decl(
        "fetch_dispute_evidence", "List evidence on file for a dispute.", {"dispute_id": "string"}
    ),
    _tool_decl(
        "check_policy",
        "Policy thresholds for a reason code.",
        {"reason_code": "string", "amount": "number"},
    ),
]
_OFF_CONTRACT_TOOL = _tool_decl(
    "issue_refund", "Issue a refund for a payment.", {"payment_id": "string"}
)


def _dispute_ft_row(
    dispute_id, code, amount, reason, resolution, evidence, *, merchant_note="", tools=None
):
    call_id = f"call_{hexid(4)}"
    ev_text = json.dumps({"evidence": evidence or ["none_on_file"]})
    answer = json.dumps(
        {
            "resolution": resolution,
            "draft_reply": (
                f"We reviewed dispute {dispute_id} (reason {code}). "
                + (
                    "We recommend accepting this dispute."
                    if resolution == "accept"
                    else "The evidence on file supports the original charge; we recommend "
                    "representment."
                    if resolution == "represent"
                    else "We need additional evidence from the merchant before responding."
                )
            ),
            "evidence_used": evidence,
        }
    )
    return {
        "messages": [
            {"role": "system", "content": _DISPUTE_SYSTEM},
            {
                "role": "user",
                "content": f"Dispute {dispute_id}: reason {code} ({reason.replace('_', ' ')}), "
                f"amount ${amount:.2f}."
                + (f" Merchant note: {merchant_note}" if merchant_note else ""),
            },
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": call_id,
                        "type": "function",
                        "function": {
                            "name": "fetch_dispute_evidence",
                            "arguments": json.dumps({"dispute_id": dispute_id}),
                        },
                    }
                ],
            },
            {"role": "tool", "tool_call_id": call_id, "content": ev_text},
            {"role": "assistant", "content": answer},
        ],
        "tools": tools or _DISPUTE_TOOLS,
    }


dispute_ft_rows = []
for i in range(400):
    reason, code = DISPUTE_REASONS[i % len(DISPUTE_REASONS)]
    amount = round(random.choice([rnd(8, 24), rnd(30, 240), rnd(250, 4200)]), 2)
    small_fraud = reason == "fraudulent" and amount < 25
    resolution = (
        "accept" if small_fraud else ["represent", "represent", "request_evidence", "accept"][i % 4]
    )
    evidence = (
        []
        if resolution in ("accept", "request_evidence")
        else [
            ["delivery_confirmation", "avs_match"],
            ["customer_comms", "cvv_match"],
            ["delivery_confirmation", "prior_undisputed_charges"],
        ][i % 3]
    )
    dispute_ft_rows.append(
        _dispute_ft_row(
            f"dp_{hexid(6)}",
            code,
            amount,
            reason,
            resolution,
            evidence,
            merchant_note=pick(MERCHANTS) if i % 3 == 0 else "",
        )
    )
# Planted defects the workshop kernel should surface:
dispute_ft_rows.append(dict(dispute_ft_rows[7]))  # exact duplicate
dispute_ft_rows.append(dict(dispute_ft_rows[19]))  # exact duplicate
dispute_ft_rows.append(dict(dispute_ft_rows[31]))  # exact duplicate
dispute_ft_rows.append(
    _dispute_ft_row(  # PII in the merchant note
        f"dp_{hexid(6)}",
        "13.1",
        148.0,
        "product_not_received",
        "request_evidence",
        [],
        merchant_note="Customer reachable at lena.hoff@gmail.com or +49 171 555 0192.",
    )
)
dispute_ft_rows.append(
    _dispute_ft_row(  # PII: full PAN in the note
        f"dp_{hexid(6)}",
        "10.4",
        96.4,
        "fraudulent",
        "represent",
        ["cvv_match", "avs_match"],
        merchant_note="Cardholder gave the number 4929 1187 3341 0052 over the phone.",
    )
)
dispute_ft_rows.append(
    _dispute_ft_row(  # tool off the capability contract
        f"dp_{hexid(6)}",
        "13.6",
        41.2,
        "credit_not_processed",
        "accept",
        [],
        tools=[*_DISPUTE_TOOLS, _OFF_CONTRACT_TOOL],
    )
)

dispute_train = _ingest(
    "Dispute Resolutions — Train",
    dispute_ft_rows,
    "ft",
    project=support_proj,
    capability=dispute_capability,
    born=days_ago(31),
)

# — Payments: NL2SQL golden (custom upload) —
sql_rows = []
for i in range(len(SQL_QUESTIONS) * 3):
    q, sql, cat = SQL_QUESTIONS[i % len(SQL_QUESTIONS)]
    sql_rows.append(
        {
            "question": SQL_PHRASINGS[i // len(SQL_QUESTIONS)].format(q=q),
            "dialect": "postgres",
            "expected_output": {"sql": sql, "category": cat},
            "persona": pick(["analyst", "finance-lead", "founder"]),
            "tags": ["nl2sql", cat],
        }
    )
sql_golden = _ingest(
    "NL2SQL Golden v2",
    sql_rows,
    "eval",
    project=payments_proj,
    capability=sql_capability,
    born=days_ago(D_PAYMENTS - 14),
)

sql_from_traces = _from_traces(
    sql_capability,
    "SQL Analyst — from traces",
    want=48,
    intent="eval",
    born=days_ago(20),
)

# Goldens the fine-tuning benchmarks grade against, lifted from production.
dispute_golden = _from_traces(
    dispute_capability,
    "Dispute Golden — from traces",
    want=36,
    intent="eval",
    born=days_ago(30),
)
receipt_golden = _from_traces(
    receipt_capability,
    "Receipt Golden — from traces",
    want=30,
    intent="eval",
    born=days_ago(44),
)

# — Expense: receipt extraction training corpus —


def _receipt_ft_row(ocr, record, date):
    answer = dict(record)
    answer["date"] = date
    return {
        "messages": [
            {"role": "system", "content": _RECEIPT_SYSTEM},
            {"role": "user", "content": ocr},
            {"role": "assistant", "content": json.dumps(answer)},
        ],
    }


receipt_ft_rows = []
for i in range(340):
    ocr, record = RECEIPTS[i % len(RECEIPTS)]
    date = (days_ago(random.randint(20, 140))).date().isoformat()
    # Vary the OCR text so rows aren't trivially identical.
    noise = f"\nRef {hexid(4).upper()}\n" if i % 2 else f"\nTill {random.randint(1, 9)}\n"
    receipt_ft_rows.append(_receipt_ft_row(ocr + noise, record, date))
receipt_ft_rows.append(dict(receipt_ft_rows[3]))  # exact duplicate
receipt_ft_rows.append(dict(receipt_ft_rows[11]))  # exact duplicate
receipt_ft_rows.append(
    _receipt_ft_row(  # PII: unmasked card + email
        "CITYCABS LEEDS\nAirport transfer\nTOTAL £48.00\nCard 4762 8811 0490 3324\n"
        "receipt to j.weber@undermindlab.ai",
        {
            "merchant": "CityCabs Leeds",
            "total_amount": 48.0,
            "currency": "GBP",
            "tax_amount": 0.0,
            "category": "travel",
        },
        days_ago(60).date().isoformat(),
    )
)
receipt_ft_rows.append(
    _receipt_ft_row(  # language outlier
        "RISTORANTE DA MARIO\nCena di lavoro — 3 persone\nTotale EUR 186,00\nIVA inclusa EUR 33,55",
        {
            "merchant": "Ristorante Da Mario",
            "total_amount": 186.0,
            "currency": "EUR",
            "tax_amount": 33.55,
            "category": "meals",
        },
        days_ago(72).date().isoformat(),
    )
)

receipt_train = _ingest(
    "Receipt Extraction — Train",
    receipt_ft_rows,
    "ft",
    project=expense_proj,
    capability=receipt_capability,
    born=days_ago(46),
)

# — Expense: policy eval —
policy_rows = []
for i in range(30):
    ocr, record = RECEIPTS[i % len(RECEIPTS)]
    # Each row is a distinct expense claim: jitter the amount and date.
    amount = round(record["total_amount"] * rnd(0.82, 1.31), 2)
    over = record["category"] == "meals" and amount > 120
    policy_rows.append(
        {
            "record": {
                **record,
                "total_amount": amount,
                "date": days_ago(30 + i).date().isoformat(),
            },
            "employee_level": ["ic", "manager", "exec"][i % 3],
            "expected_output": {
                "verdict": "flag" if over else "approve",
                "violated_rules": ["meals-per-diem-40"] if over else [],
            },
            "persona": "expense-admin",
            "tags": ["policy", record["category"]],
        }
    )
policy_eval = _ingest(
    "Policy Check — Golden",
    policy_rows,
    "eval",
    project=expense_proj,
    capability=policy_capability,
    born=days_ago(40),
    input_paths=["record", "employee_level"],
)

# — Onboarding: KYC doc QA —
kyc_rows = []
_kyc_phrasings = ["{q}", "Hi — {q}", "Before we apply: {q}"]
for i, (q, docs, entity, country) in enumerate(KYC_QA * 3):
    kyc_rows.append(
        {
            "question": _kyc_phrasings[i // len(KYC_QA)].format(q=q),
            "entity_type": entity,
            "country": country,
            "expected_output": {"required_documents": docs},
            "persona": "applicant",
            "tags": ["kyc", country.lower()],
        }
    )
kyc_eval = _ingest(
    "KYC Doc QA",
    kyc_rows[:24],
    "eval",
    project=onboard_proj,
    capability=kyc_capability,
    born=days_ago(10),
)

# — Growth: Langfuse-synced eval corpus + raw voice-of-customer notes —
outreach_rows = []
_signals = [
    "instant payouts eligibility",
    "terminal offline mode launch",
    "checkout conversion benchmark",
    "multi-currency pricing beta",
    "QR pay-at-table rollout",
    "3DS exemption uplift",
    "settlement report exports",
    "payout notifications in Slack",
]
for i in range(32):
    merchant = MERCHANTS[i % len(MERCHANTS)]
    segment = ["retail", "d2c", "cafes", "restaurants"][i % 4]
    signal = _signals[(i * 3) % len(_signals)]
    outreach_rows.append(
        {
            "merchant_name": merchant,
            "segment": segment,
            "signal": signal,
            "expected_output": {
                "subject": f"{signal.capitalize()} — worth a look?",
                "notes": "Concrete signal in first line; one CTA; no pricing promises.",
            },
            "persona": "sdr",
            "tags": ["outreach", segment],
        }
    )
outreach_synced = _ingest(
    "Outreach Briefs (Langfuse sync)",
    outreach_rows,
    "eval",
    project=growth_proj,
    capability=outreach_capability,
    born=days_ago(D_GROWTH - 2),
)

voice_rows = [
    {
        "note": "Call with Kite Coffee: instant payouts are the wedge; fees fine.",
        "topic": "pricing",
    },
    {
        "note": "Volt Cycle asked twice about offline terminal mode — launch blocker.",
        "topic": "product",
    },
    {
        "note": "Paloma churn risk: their Shopify rev-share beats our take rate.",
        "topic": "competitive",
    },
    {"note": "Three merchants confused net vs gross settlement in reports.", "topic": "product"},
    {"note": "Hachi Ramen wants QR pay-at-table before the summer rush.", "topic": "product"},
    {"note": "Baltic Board Games: multi-currency pricing beta went 'flawlessly'.", "topic": "wins"},
    {"note": "Clearline saw +2.1pp auth rate after 3DS exemption rollout.", "topic": "wins"},
    {
        "note": "Two SDRs report outreach replies double when we cite a concrete "
        "signal in the first line.",
        "topic": "outreach",
    },
    {"note": "Golden Hour Wines blocked on alcohol MCC review for 9 days.", "topic": "ops"},
    {"note": "Redbrick asked for annual invoicing — finance says Q3.", "topic": "pricing"},
    {"note": "Sable & Co want payout notifications in Slack.", "topic": "product"},
    {"note": "Prism Art moved from starter to growth after the exports fix.", "topic": "wins"},
    # planted: exact duplicate + near-duplicate for the workshop to find
    {
        "note": "Volt Cycle asked twice about offline terminal mode — launch blocker.",
        "topic": "product",
    },
    {
        "note": "Volt Cycle asked twice about offline terminal mode — launch blocker",
        "topic": "product",
    },
]
voice_notes = _ingest(
    "Voice-of-customer notes (raw)",
    voice_rows,
    "unstructured",
    project=growth_proj,
    born=days_ago(12),
)
voice_eval = _ingest(
    "Voice-of-customer (as eval)",
    [{"note": r["note"], "expected_output": {"topic": r["topic"]}} for r in voice_rows],
    "eval",
    project=growth_proj,
    born=days_ago(11),
)

seed_datasets = [
    triage_golden,
    kb_eval,
    dispute_train,
    sql_golden,
    sql_from_traces,
    dispute_golden,
    receipt_golden,
    receipt_train,
    policy_eval,
    kyc_eval,
    outreach_synced,
    voice_notes,
    voice_eval,
]

DATASET_BORN = {
    triage_golden.pk: days_ago(D_SUPPORT - 22),
    kb_eval.pk: days_ago(D_SUPPORT - 26),
    dispute_train.pk: days_ago(31),
    sql_golden.pk: days_ago(D_PAYMENTS - 14),
    sql_from_traces.pk: days_ago(20),
    dispute_golden.pk: days_ago(30),
    receipt_golden.pk: days_ago(44),
    receipt_train.pk: days_ago(46),
    policy_eval.pk: days_ago(40),
    kyc_eval.pk: days_ago(10),
    outreach_synced.pk: days_ago(D_GROWTH - 2),
    voice_notes.pk: days_ago(12),
    voice_eval.pk: days_ago(11),
}


print("Writing dataset contexts...")

CONTEXTS = {
    triage_golden.pk: (
        "support ticket triage",
        "Classify inbound support tickets by urgency, category and owning team, "
        "with plan-aware SLA floors.",
        [
            {
                "name": "SLA floor respected",
                "rubric": "Enterprise merchants must never be classified below medium urgency.",
                "reason": "The corpus shows plan-dependent urgency labels.",
            }
        ],
    ),
    kb_eval.pk: (
        "grounded support QA",
        "Answer help-centre questions strictly from retrieved articles with citations.",
        [
            {
                "name": "Citation coverage",
                "rubric": "Every factual sentence must cite an article slug present in "
                "the retrieval set.",
                "reason": "References include per-answer citation lists.",
            }
        ],
    ),
    dispute_train.pk: (
        "chargeback resolution",
        "Draft dispute resolutions (accept / represent / request_evidence) from "
        "ledger evidence under the representment policy.",
        [
            {
                "name": "Evidence gating",
                "rubric": "Representment requires at least two independent evidence classes.",
                "reason": "Assistant turns consistently pair representment with "
                "multi-class evidence.",
            }
        ],
    ),
    sql_golden.pk: (
        "payments analytics NL→SQL",
        "Translate analytics questions into read-only warehouse SQL over the payments schema.",
        [
            {
                "name": "Net vs gross discipline",
                "rubric": "Revenue questions use net_amount unless gross is explicitly requested.",
                "reason": "Reference queries encode the net-by-default convention.",
            }
        ],
    ),
    sql_from_traces.pk: (
        "payments analytics NL→SQL",
        "Production questions and answers lifted from sql-analyst traces.",
        [],
    ),
    dispute_golden.pk: (
        "chargeback resolution",
        "Production dispute resolutions lifted from dispute-resolver traces.",
        [],
    ),
    receipt_golden.pk: (
        "receipt field extraction",
        "Production receipt extractions lifted from receipt-extractor traces.",
        [],
    ),
    receipt_train.pk: (
        "receipt field extraction",
        "Extract merchant, date, amounts, currency and category from OCR'd "
        "receipts into strict JSON.",
        [
            {
                "name": "Total vs subtotal",
                "rubric": "total_amount is the charged total, never the pre-tax subtotal.",
                "reason": "EU receipts in the corpus show VAT-inclusive totals.",
            }
        ],
    ),
    policy_eval.pk: (
        "expense policy checking",
        "Validate expense records against the T&E policy with cited rules.",
        [],
    ),
    kyc_eval.pk: (
        "KYC document guidance",
        "Answer applicant questions about required KYC documents by entity type and country.",
        [],
    ),
    outreach_synced.pk: (
        "sales outreach drafting",
        "Draft personalised merchant outreach from account signals.",
        [
            {
                "name": "Signal-first opening",
                "rubric": "The first sentence must reference the concrete account signal.",
                "reason": "Reference notes require signal-led openings.",
            }
        ],
    ),
    voice_notes.pk: ("voice of customer", "Raw GTM call notes.", []),
    voice_eval.pk: ("voice of customer", "Call notes with the topic as the reference.", []),
}

for ds in seed_datasets:
    domain, task, rubrics = CONTEXTS[ds.pk]
    stats = row_store.dataset_stats(ds)
    # eval-capabilities and the task-type classifier both read the profiler's shape;
    # a hand-written stand-in leaves every dataset classifying the same.
    profile = profiler.profile_dataset(ds)
    DatasetContext.objects.create(
        dataset=ds,
        project=ds.project,
        profile=profile,
        domain=domain,
        task_description=task,
        task_type=classify_task_type(profile, stats),
        task_type_source="heuristic",
        suggested_rubrics=rubrics,
        evaluator_scores={},
        auto_rubric_md="",
        auto_rubric_checklist=[],
        sample_count=min(ds.active_cell.rows, 25),
        extracted_at=DATASET_BORN[ds.pk] + timedelta(hours=2),
    )


print("Creating evaluation runs...")

mr_sol = ModelRef.objects.create(
    project=support_proj,
    label="GPT-5.6 Sol (incumbent)",
    provider="openai",
    model_id="gpt-5.6-sol",
    params={"temperature": 0.2, "max_tokens": 1024},
)
mr_sonnet = ModelRef.objects.create(
    project=support_proj,
    label="Claude Sonnet 5",
    provider="anthropic",
    model_id="claude-sonnet-5",
    params={"temperature": 0.2},
)
mr_terra = ModelRef.objects.create(
    project=expense_proj,
    label="GPT-5.6 Terra (incumbent)",
    provider="openai",
    model_id="gpt-5.6-terra",
    params={"temperature": 0.0},
)
model_refs = [mr_sol, mr_sonnet, mr_terra]  # ft refs are added with their jobs


def clampq(quality, spread=0.12):
    return round(min(1.0, max(0.0, random.gauss(quality, spread))), 2)


def _traj(
    user_text,
    final_text,
    *,
    tools=(),
    model,
    quality_ms=(900, 3800),
    mode="existing",
    trace_id="",
    pt=None,
    ct=None,
):
    pt = pt or random.randint(700, 2600)
    ct = ct or random.randint(120, 500)
    ms = rnd(*quality_ms, 1)
    messages = [{"role": "user", "content": user_text}]
    nodes, edges, tool_defs = [], [], []
    for i, (tname, args, result) in enumerate(tools):
        call_id = f"call_{hexid(3)}"
        messages.append(
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [{"id": call_id, "name": tname, "arguments": args}],
            }
        )
        messages.append({"role": "tool", "tool_call_id": call_id, "content": json.dumps(result)})
        nodes.append(
            {
                "id": f"step_{i}",
                "tool": tname,
                "arguments": args,
                "result": result,
                "error": "",
                "caused_state_change": False,
                "depends_on": [f"step_{i - 1}"] if i else [],
            }
        )
        if i:
            edges.append({"from": f"step_{i - 1}", "to": f"step_{i}"})
        if tname not in {t["name"] for t in tool_defs}:
            tool_defs.append(
                {
                    "name": tname,
                    "description": "",
                    "parameters": {"type": "object", "properties": {}},
                }
            )
    messages.append({"role": "assistant", "content": final_text})
    if mode == "generate":
        metadata = {
            "model": model,
            "cost": llm_cost(model, pt, ct),
            "latency_ms": ms,
            "prompt_tokens": pt,
            "completion_tokens": ct,
            "total_tokens": pt + ct,
            "steps": len(tools) + 1,
            "max_steps": 16,
            "replay_fuzzy_hits": 0,
            "replay_misses": 0,
            "truncated": False,
        }
    else:
        metadata = {
            "source": "otel_span",
            "trace_id": trace_id or hexid(16),
            "cost": llm_cost(model, pt, ct),
            "latency_ms": ms,
            "total_tokens": pt + ct,
            "truncated": False,
        }
    trajectory = {
        "modality": "text",
        "messages": messages,
        "tool_definitions": tool_defs,
        "final_output": final_text,
        "metadata": metadata,
    }
    salient = [{"type": "final_answer", "ref": "final", "preview": final_text[:80]}]
    structured = {
        "turns": [
            {
                "index": 0,
                "start": 0,
                "messages": list(range(len(messages))),
                "tool_calls": len(nodes),
            }
        ],
        "tool_graph": {"nodes": nodes, "edges": edges},
        "salient_steps": salient,
        "approx_tokens": pt + ct,
        "num_messages": len(messages),
        "num_tool_calls": len(nodes),
        "num_turns": 1,
    }
    return trajectory, structured


_JUDGE_REASONS = {
    "Triage Accuracy": [
        "Urgency and team match the reference; category wording differs but "
        "names the same surface.",
        "Team matches; urgency one level below the reference for an enterprise "
        "merchant, which the SLA floor forbids.",
        "All three fields match the golden triage.",
        "Routed to support-general where the reference routes to "
        "oncall-payments — payout disruptions are on-call territory.",
    ],
    "Correctness": [
        "The answer matches the reference on every material point.",
        "Substantively correct; one secondary detail differs from the reference.",
        "Misses the reference's key qualifier, changing the recommendation.",
    ],
    "Conciseness": [
        "Tight summary with no filler.",
        "Slightly padded but within reason.",
        "Repeats the ticket text nearly verbatim before answering.",
    ],
    "Citation Support": [
        "Every claim is backed by one of the cited articles.",
        "The fee claim cites an article that does not mention fees.",
    ],
    "Faithfulness": [
        "The answer stays entirely within the retrieved articles.",
        "One sentence extrapolates beyond the cited material.",
    ],
    "Tone & Empathy": [
        "Professional and warm; acknowledges the disruption before the fix.",
        "Correct but abrupt; no acknowledgement of impact.",
    ],
    "Resolution Policy Compliance": [
        "Two evidence classes support representment; no timeline promises.",
        "Represented on a single evidence class — the policy requires two.",
    ],
    "SQL Correctness": [
        "Semantically equivalent to the reference: same grain, filters and aggregation.",
        "Uses gross amount where the reference uses net_amount.",
        "Correct aggregation but missing the minimum-volume HAVING filter.",
    ],
    "Field Extraction Accuracy": [
        "All six fields match the reference exactly.",
        "Total matches but tax_amount picked up the service charge.",
        "Date parsed as DD/MM where the receipt uses MM/DD.",
    ],
    "Policy Verdict Correct": [
        "Verdict and cited rules match the reference.",
        "Approved a meal that exceeds the per-diem cap.",
    ],
    "Doc Answer Correctness": [
        "Document list matches the requirements table for this entity/country.",
        "Missing the shareholder register required for UK LLCs.",
    ],
    "Brand Voice": [
        "Direct, concrete, signal-first — on voice.",
        "Opens with generic flattery instead of the account signal.",
    ],
    "No Unsubstantiated Claims": [
        "No pricing promises or unverifiable claims.",
        "Promises a settlement-time improvement we cannot guarantee.",
    ],
}


def _resolution_sub():
    return {
        "_resolution": {
            "output": {"strategy": "final_output", "shape": "str"},
            "reference": {"strategy": "expected", "shape": "dict"},
        }
    }


def _judge_score(run, variant, sample, run_ev, quality):
    snap = run_ev.snapshot
    name = snap["name"]
    reasons = _JUDGE_REASONS.get(name, ["Meets the rubric.", "Partially meets the rubric."])
    if snap["score_type"] == "boolean":
        ok = random.random() < quality
        subs = []
        for item in snap.get("checklist") or [{"id": "check", "q": ""}]:
            subs.append(
                {
                    "id": item["id"],
                    "verdict": ok,
                    "score": 1.0 if ok else 0.0,
                    "reasoning": reasons[0 if ok else -1][:160],
                }
            )
        subs.append(
            {"_threshold": {"pass_threshold": snap.get("pass_threshold"), "gated_fail": not ok}}
        )
        subs.append(_resolution_sub())
        value, passed = (1.0, True) if ok else (0.0, False)
        reasoning = reasons[0] if ok else reasons[-1]
    else:
        value = clampq(quality)
        passed = None
        subs = []
        checklist = snap.get("checklist") or []
        for item in checklist:
            v = random.random() < quality
            subs.append(
                {
                    "id": item["id"],
                    "verdict": v,
                    "score": 1.0 if v else 0.0,
                    "reasoning": reasons[0 if v else -1][:160],
                    **({"field": item["field"]} if item.get("field") else {}),
                }
            )
        subs.append({"_threshold": {"pass_threshold": None, "gated_fail": False}})
        subs.append(_resolution_sub())
        reasoning = reasons[0] if value >= 0.7 else reasons[-1]
    return Score(
        project=run.project,
        run=run,
        variant=variant,
        sample=sample,
        evaluator=run_ev.evaluator,
        run_evaluator=run_ev,
        scope=snap.get("scope", "final_output"),
        name=name,
        data_type=snap["score_type"],
        value=value,
        passed=passed,
        outcome="scored",
        reasoning=reasoning,
        sub_scores=subs,
        judge_trace_id=uuid.uuid4().hex,
        cost=rnd(0.0002, 0.0011, 6),
        latency_ms=rnd(500, 1900, 1),
    )


def _det_score(run, variant, sample, run_ev, quality):
    snap = run_ev.snapshot
    ok = random.random() < min(1.0, quality + 0.12)
    return Score(
        project=run.project,
        run=run,
        variant=variant,
        sample=sample,
        evaluator=run_ev.evaluator,
        run_evaluator=run_ev,
        scope=snap.get("scope", "final_output"),
        name=snap["name"],
        data_type="boolean",
        value=1.0 if ok else 0.0,
        passed=ok,
        outcome="scored",
        reasoning="" if ok else "Check failed on the output payload.",
        sub_scores=[{"_resolution": {"output": {"strategy": "final_output", "shape": "str"}}}],
        cost=0.0,
        latency_ms=rnd(1, 8, 1),
    )


def _trajectory_score(run, variant, sample, run_ev, quality, ref_tools):
    ok = random.random() < quality
    actual = list(ref_tools) if ok else list(ref_tools)[:-1]
    return Score(
        project=run.project,
        run=run,
        variant=variant,
        sample=sample,
        evaluator=run_ev.evaluator,
        run_evaluator=run_ev,
        scope="trajectory",
        name=run_ev.snapshot["name"],
        data_type="boolean",
        value=1.0 if ok else 0.0,
        passed=ok,
        outcome="scored",
        reasoning=f"trajectory superset match: {'pass' if ok else 'fail'} "
        f"({len(actual)} actual vs {len(ref_tools)} reference calls)",
        sub_scores=[
            {
                "actual_tools": actual,
                "reference_tools": list(ref_tools),
                "first_mismatch": None if ok else len(actual),
            }
        ],
        cost=0.0,
        latency_ms=rnd(1, 6, 1),
    )


def _prediction_score(run, variant, sample, run_ev, quality, reference, labels):
    pred = reference if random.random() < quality else pick(labels)
    return Score(
        project=run.project,
        run=run,
        variant=variant,
        sample=sample,
        evaluator=run_ev.evaluator,
        run_evaluator=run_ev,
        scope="sample",
        name=f"{run_ev.snapshot['name']}__prediction",
        data_type="categorical",
        value=None,
        string_value=str(pred)[:256],
        passed=None,
        outcome="scored",
        reasoning="",
        sub_scores=[{"prediction": pred, "reference": reference}, _resolution_sub()],
        cost=rnd(0.0001, 0.0004, 6),
        latency_ms=rnd(300, 900, 1),
    )


def make_eval_run(
    *,
    name,
    description,
    capability_,
    dataset,
    eval_set,
    members,
    variants,
    born,
    triggered_by,
    n_samples,
    sample_fn,
    labels=None,
    failed=None,
):
    """Build a terminal EvalRun through the shapes aggregate_run expects.

    variants: [(label, model_name, model_ref, mode, is_baseline, quality)]
    sample_fn(datapoint, quality, model, mode) ->
        (trajectory, structured, expected, ref_tools, reference_label)
    """
    version = dataset.active_cell
    run = EvalRun.objects.create(
        project=capability_.project,
        name=name,
        description=description,
        data_source="dataset",
        dataset=dataset,
        cell=version,
        max_items=n_samples,
        eval_set=eval_set,
        sampling=1.0,
        status="pending",
        triggered_by=triggered_by,
        celery_task_id=str(uuid.uuid4()),
    )
    if failed:
        run.status = "failed"
        run.error = failed
        run.save(update_fields=["status", "error"])
        stamp(run, born, born + timedelta(minutes=3), completed_at=born + timedelta(minutes=3))
        return run, []
    run_evs = []
    for order, member in enumerate(members):
        run_evs.append(
            RunEvaluator.objects.create(
                run=run,
                evaluator=member.evaluator,
                snapshot=eval_snapshots.build_snapshot(member.evaluator),
                sampling=1.0,
                enabled=True,
                order=order,
            )
        )
    variant_rows = []
    for order, (label, model_name, model_ref, mode, is_baseline, quality) in enumerate(variants):
        variant_rows.append(
            (
                EvalVariant.objects.create(
                    run=run,
                    label=label,
                    model_ref=model_ref,
                    model_name=model_name,
                    mode=mode,
                    is_baseline=is_baseline,
                    order=order,
                ),
                quality,
                model_name or (model_ref.model_id if model_ref else ""),
            )
        )
    datapoints = row_store.sample_rows(dataset, n_samples)
    score_buf, sample_pairs = [], []
    for variant, quality, vmodel in variant_rows:
        for dp in datapoints:
            trajectory, structured, expected, ref_tools, ref_label = sample_fn(
                dp,
                quality,
                vmodel,
                variant.mode,
            )
            sample = EvalSample.objects.create(
                run=run,
                variant=variant,
                row_index=dp.index,
                source_trace_id=trajectory["metadata"].get("trace_id", ""),
                trajectory=trajectory,
                structured=structured,
                expected=expected,
            )
            sample_pairs.append((sample.pk, born + timedelta(minutes=rnd(1, 8))))
            for run_ev in run_evs:
                kind = run_ev.snapshot["kind"]
                if kind == "llm_judge":
                    score_buf.append(_judge_score(run, variant, sample, run_ev, quality))
                elif kind == "deterministic":
                    score_buf.append(_det_score(run, variant, sample, run_ev, quality))
                elif kind == "trajectory":
                    score_buf.append(
                        _trajectory_score(run, variant, sample, run_ev, quality, ref_tools)
                    )
                elif kind == "statistical":
                    score_buf.append(
                        _prediction_score(
                            run, variant, sample, run_ev, quality, ref_label, labels or [ref_label]
                        )
                    )
    Score.objects.bulk_create(score_buf, batch_size=500)
    backdate(EvalSample, sample_pairs)
    backdate(Score, [(s.pk, born + timedelta(minutes=rnd(4, 14))) for s in score_buf])
    eval_tasks.aggregate_run.apply(kwargs={"eval_run_id": str(run.pk)})
    run.refresh_from_db()
    stamp(run, born, born + timedelta(minutes=15), completed_at=born + timedelta(minutes=15))
    return run, [v for v, _, _ in variant_rows]


# — sample builders per capability —


def triage_sample(dp, quality, model, mode):
    inp = dp.input if isinstance(dp.input, dict) else {}
    text = inp.get("ticket_text", "")
    expected = dp.expected_output or {}
    good = random.random() < quality
    out = dict(expected) if isinstance(expected, dict) else {}
    if not good and out:
        out = {**out, "team": "support-general"}
    final = json.dumps(
        out
        or {
            "urgency": "medium",
            "category": "General",
            "team": "support-general",
            "summary": text[:80],
        }
    )
    traj, structured = _traj(
        f"Triage this ticket ({inp.get('merchant_plan', 'growth')} plan):\n{text}",
        final,
        tools=[
            (
                "lookup_merchant",
                {"merchant_name": "…"},
                {"plan": inp.get("merchant_plan", "growth"), "open_incidents": 0},
            )
        ],
        model=model,
        mode=mode,
    )
    return traj, structured, expected, ["lookup_merchant"], None


def kb_sample(dp, quality, model, mode):
    inp = dp.input if isinstance(dp.input, dict) else {}
    expected = dp.expected_output or {}
    answer = expected.get("answer", "") if isinstance(expected, dict) else ""
    cites = expected.get("citations", []) if isinstance(expected, dict) else []
    final = json.dumps({"answer": answer, "citations": cites, "confidence": clampq(quality, 0.08)})
    traj, structured = _traj(
        inp.get("question", ""),
        final,
        tools=[
            ("search_kb", {"query": inp.get("question", "")[:40]}, {"hits": cites}),
            ("fetch_article", {"slug": cites[0] if cites else "payouts"}, {"found": True}),
        ],
        model=model,
        mode=mode,
    )
    return traj, structured, expected, ["search_kb", "fetch_article"], None


def dispute_sample(dp, quality, model, mode):
    inp = dp.input if isinstance(dp.input, dict) else {}
    expected = dp.expected_output or {}
    final = json.dumps(expected if isinstance(expected, dict) else {})
    ref_tools = ["lookup_transaction", "fetch_dispute_evidence", "check_policy"]
    traj, structured = _traj(
        f"Resolve dispute {inp.get('dispute_id', 'dp_x')} (reason "
        f"{inp.get('reason_code', '10.4')}, ${inp.get('amount', 0)}).",
        final,
        tools=[(t, {"dispute_id": inp.get("dispute_id", "dp_x")}, {"ok": True}) for t in ref_tools],
        model=model,
        mode=mode,
    )
    return traj, structured, expected, ref_tools, None


SQL_LABELS = ["revenue", "churn", "settlement", "fraud", "operations"]


def sql_sample(dp, quality, model, mode):
    inp = dp.input if isinstance(dp.input, dict) else {}
    expected = dp.expected_output or {}
    sql = expected.get("sql", "SELECT 1") if isinstance(expected, dict) else "SELECT 1"
    label = expected.get("category", "operations") if isinstance(expected, dict) else "operations"
    final = json.dumps({"sql": sql, "explanation": "…", "confidence": clampq(quality, 0.08)})
    traj, structured = _traj(
        inp.get("question", ""),
        final,
        tools=[
            ("list_tables", {"schema": "analytics"}, {"tables": 42}),
            ("execute_sql", {"sql": sql[:50]}, {"rows": random.randint(1, 900)}),
        ],
        model=model,
        mode=mode,
    )
    return traj, structured, expected, ["list_tables", "execute_sql"], label


def receipt_sample(dp, quality, model, mode):
    inp = dp.input if isinstance(dp.input, dict) else {}
    expected = dp.expected_output or {}
    out = dict(expected) if isinstance(expected, dict) else {}
    if out and random.random() > quality:
        out = {**out, "tax_amount": round((out.get("tax_amount") or 1) + 3.1, 2)}
    traj, structured = _traj(
        str(inp.get("ocr_text", ""))[:400],
        json.dumps(out),
        model=model,
        mode=mode,
    )
    return traj, structured, expected, [], None


def kyc_sample(dp, quality, model, mode):
    inp = dp.input if isinstance(dp.input, dict) else {}
    expected = dp.expected_output or {}
    traj, structured = _traj(
        inp.get("question", ""),
        json.dumps(expected),
        tools=[
            (
                "lookup_requirements",
                {"entity_type": inp.get("entity_type", "llc")},
                {"documents": (expected or {}).get("required_documents", [])},
            )
        ],
        model=model,
        mode=mode,
    )
    return traj, structured, expected, ["lookup_requirements"], None


def outreach_sample(dp, quality, model, mode):
    inp = dp.input if isinstance(dp.input, dict) else {}
    expected = dp.expected_output or {}
    final = json.dumps(
        {
            "subject": (expected or {}).get("subject", "Quick look?"),
            "body": f"Hi {inp.get('merchant_name', 'there')} — about "
            f"{inp.get('signal', 'your account')}…",
            "cta": "Book 15 minutes",
        }
    )
    traj, structured = _traj(
        f"Draft outreach for {inp.get('merchant_name')} ({inp.get('segment')}); "
        f"signal: {inp.get('signal')}.",
        final,
        model=model,
        mode=mode,
    )
    return traj, structured, expected, [], None


# — the runs —

triage_may_run, _ = make_eval_run(
    name="Triage rubric — first baseline",
    description="First scored baseline of the production triage prompt.",
    capability_=triage_capability,
    dataset=triage_golden,
    eval_set=triage_set,
    members=list(triage_members.values()),
    variants=[("production (gpt-5.6-sol)", "openai/gpt-5.6-sol", mr_sol, "existing", True, 0.71)],
    born=days_ago(D_SUPPORT - 24),
    triggered_by=amara,
    n_samples=20,
    sample_fn=triage_sample,
)

triage_opt_run, triage_opt_variants = make_eval_run(
    name="Triage — prompt v3 verification",
    description="Optimiser winner (prompt v3) vs the previous production prompt on the golden set.",
    capability_=triage_capability,
    dataset=triage_golden,
    eval_set=triage_set,
    members=list(triage_members.values()),
    variants=[
        ("prompt v2 (baseline)", "openai/gpt-5.6-sol", mr_sol, "existing", True, 0.714),
        ("prompt v3 (optimised)", "openai/gpt-5.6-sol", mr_sol, "existing", False, 0.842),
    ],
    born=days_ago(31),
    triggered_by=sofia,
    n_samples=20,
    sample_fn=triage_sample,
)

kb_run, _ = make_eval_run(
    name="KB answers — citation audit",
    description="Citation and faithfulness audit ahead of the KB refresh.",
    capability_=kb_capability,
    dataset=kb_eval,
    eval_set=kb_set,
    members=list(kb_members.values()),
    variants=[
        (
            "production (claude-sonnet-5)",
            "anthropic/claude-sonnet-5",
            mr_sonnet,
            "existing",
            True,
            0.87,
        )
    ],
    born=days_ago(48),
    triggered_by=amara,
    n_samples=24,
    sample_fn=kb_sample,
)

sql_run_early, _ = make_eval_run(
    name="SQL quality — June checkpoint",
    description="Monthly quality gate on the NL2SQL golden set.",
    capability_=sql_capability,
    dataset=sql_golden,
    eval_set=sql_set,
    members=list(sql_members.values()),
    variants=[("production (gpt-5.6-terra)", "openai/gpt-5.6-terra", None, "existing", True, 0.74)],
    born=days_ago(40),
    triggered_by=diego,
    n_samples=30,
    sample_fn=sql_sample,
    labels=SQL_LABELS,
)

sql_run_late, _ = make_eval_run(
    name="SQL quality — July checkpoint",
    description="Monthly quality gate on the NL2SQL golden set.",
    capability_=sql_capability,
    dataset=sql_golden,
    eval_set=sql_set,
    members=list(sql_members.values()),
    variants=[("production (gpt-5.6-terra)", "openai/gpt-5.6-terra", None, "existing", True, 0.83)],
    born=days_ago(12),
    triggered_by=diego,
    n_samples=30,
    sample_fn=sql_sample,
    labels=SQL_LABELS,
)

kyc_failed_run, _ = make_eval_run(
    name="KYC answers — first run",
    description="",
    capability_=kyc_capability,
    dataset=kyc_eval,
    eval_set=kyc_set,
    members=list(kyc_members.values()),
    variants=[
        ("production (gemini-2.5-pro)", "google/gemini-2.5-pro", None, "existing", True, 0.85)
    ],
    born=days_ago(9),
    triggered_by=sofia,
    n_samples=24,
    sample_fn=kyc_sample,
    failed="judge provider rate limited (429) on 21/24 samples; run aborted",
)

kyc_run, _ = make_eval_run(
    name="KYC answers — first run (retry)",
    description="Retry after the judge-provider rate limit cleared.",
    capability_=kyc_capability,
    dataset=kyc_eval,
    eval_set=kyc_set,
    members=list(kyc_members.values()),
    variants=[
        ("production (gemini-2.5-pro)", "google/gemini-2.5-pro", None, "existing", True, 0.86)
    ],
    born=days_ago(8),
    triggered_by=sofia,
    n_samples=24,
    sample_fn=kyc_sample,
)

outreach_run, _ = make_eval_run(
    name="Outreach voice audit",
    description="Brand-voice and claims audit on the synced Langfuse corpus.",
    capability_=outreach_capability,
    dataset=outreach_synced,
    eval_set=outreach_set,
    members=list(outreach_members.values()),
    variants=[("production (gpt-5.6-sol)", "openai/gpt-5.6-sol", None, "existing", True, 0.79)],
    born=days_ago(24),
    triggered_by=amara,
    n_samples=24,
    sample_fn=outreach_sample,
)

# FT benchmarks: incumbent vs the fine-tune, generate-mode variants. The
# FinetuningJobEval rows in §15 read their aggregate scores from these.
dispute_bench_run, dispute_bench_variants = make_eval_run(
    name="Dispute FT benchmark — ft-llama-3.1-8b vs incumbent",
    description="Final benchmark for the dispute-resolver fine-tune against the "
    "incumbent production model on the from-traces golden.",
    capability_=dispute_capability,
    dataset=dispute_golden,
    eval_set=dispute_set,
    members=list(dispute_members.values()),
    variants=[
        (
            "incumbent (claude-sonnet-5)",
            "anthropic/claude-sonnet-5",
            mr_sonnet,
            "generate",
            True,
            0.78,
        ),
        (DISPUTE_MODEL_ID, DISPUTE_MODEL_ID, None, "generate", False, 0.84),
    ],
    born=days_ago(21, h=-3),
    triggered_by=jonas,
    n_samples=24,
    sample_fn=dispute_sample,
)

receipt_bench_run, receipt_bench_variants = make_eval_run(
    name="Extraction FT benchmark — compact vs incumbent",
    description="Final benchmark for the receipt-extractor compact fine-tune.",
    capability_=receipt_capability,
    dataset=receipt_golden,
    eval_set=receipt_set,
    members=list(receipt_members.values()),
    variants=[
        ("incumbent (gpt-5.6-terra)", "openai/gpt-5.6-terra", mr_terra, "generate", True, 0.81),
        (EXPENSE_COMPACT_MODEL_ID, EXPENSE_COMPACT_MODEL_ID, None, "generate", False, 0.86),
    ],
    born=days_ago(40, h=-4),
    triggered_by=jonas,
    n_samples=20,
    sample_fn=receipt_sample,
)

eval_runs_all = [
    triage_may_run,
    triage_opt_run,
    kb_run,
    sql_run_early,
    sql_run_late,
    kyc_failed_run,
    kyc_run,
    outreach_run,
    dispute_bench_run,
    receipt_bench_run,
]

# Annotations: Amara spot-checks the optimiser verification run.
ann_pairs = []
for sample in EvalSample.objects.filter(run=triage_opt_run)[:8]:
    agree = random.random() < 0.8
    ann = Annotation.objects.create(
        project=support_proj,
        sample=sample,
        evaluator=triage_accuracy,
        user=amara,
        value=1.0 if agree else 0.0,
        label="agree" if agree else "disagree",
        note=""
        if agree
        else "Judge accepted a category that our routing table treats as a different surface.",
    )
    ann_pairs.append((ann.pk, days_ago(30, h=-rnd(1, 20))))
backdate(Annotation, ann_pairs)

for i in range(6):
    JudgeCache.objects.update_or_create(
        key=hashlib.sha256(f"seed-judge-{i}".encode()).hexdigest(),
        defaults=dict(
            raw=json.dumps(
                {
                    "items": [
                        {
                            "id": "urgency",
                            "verdict": True,
                            "score": 1.0,
                            "reasoning": "Matches the reference urgency.",
                        }
                    ],
                    "score": round(0.7 + i * 0.05, 2),
                    "label": "",
                    "reasoning": "Fields match the golden triage.",
                    "evidence": ["urgency", "team"],
                }
            ),
            prompt_tokens=random.randint(900, 1600),
            completion_tokens=random.randint(120, 240),
            hits=random.randint(1, 9),
        ),
    )


print("Creating optimiser experiments...")

_COMMAND_TEMPLATE = """uv run python - <<'EOF'

def main():
    datapoint = __DATAPOINT_INPUT__
    from {module} import {entry}

    return {entry}(**datapoint)


main()
EOF
"""

_TRIAGE_DIFFS = [
    """diff --git a/capabilities/triage/prompts.py b/capabilities/triage/prompts.py
index 4c1f2ab..8e9d310 100644
--- a/capabilities/triage/prompts.py
+++ b/capabilities/triage/prompts.py
@@ -1,12 +1,18 @@
 SYSTEM_PROMPT = \"\"\"You are Undermind's support triage capability.
-Classify the ticket and return strict JSON with urgency, category,
-team and a one-line summary.
+Process:
+1. Identify the failure surface (API, dashboard, payouts, billing, fraud).
+2. Call lookup_merchant; enterprise merchants are never `low` urgency.
+3. Payment-disruption reports route to oncall-payments; suspected fraud
+   to risk-ops.
+4. Summaries never quote card numbers.
+
+Return strict JSON matching the schema. Urgency reflects merchant
+impact, not sentiment.
 \"\"\"
""",
    """diff --git a/capabilities/triage/prompts.py b/capabilities/triage/prompts.py
index 4c1f2ab..2b7a914 100644
--- a/capabilities/triage/prompts.py
+++ b/capabilities/triage/prompts.py
@@ -1,8 +1,13 @@
 SYSTEM_PROMPT = \"\"\"You are Undermind's support triage capability.
-Classify the ticket and return strict JSON with urgency, category,
-team and a one-line summary.
+Think through the routing before answering:
+- Which team owns this failure surface?
+- Does the merchant plan floor the urgency?
+Return strict JSON with urgency, category, team and a one-line summary.
 \"\"\"
+
+FEW_SHOT = [
+    {"ticket": "Payouts stuck since Monday", "team": "oncall-payments"},
+]
""",
    """diff --git a/capabilities/triage/capability.py b/capabilities/triage/capability.py
index 91c0d44..d02f871 100644
--- a/capabilities/triage/capability.py
+++ b/capabilities/triage/capability.py
@@ -41,7 +41,10 @@ def run_triage(ticket_text, merchant_plan="growth", previous_tickets=0):
     merchant = lookup_merchant(merchant_name=extract_merchant(ticket_text))
-    result = llm.chat(SYSTEM_PROMPT, ticket_text)
+    context = f"plan={merchant_plan} incidents={merchant.open_incidents}"
+    result = llm.chat(SYSTEM_PROMPT, f"{context}\\n\\n{ticket_text}")
+    if merchant_plan == "enterprise" and result["urgency"] == "low":
+        result["urgency"] = "medium"
     return validate(result)
""",
]


def _variant_score(run, variant) -> float:
    metrics = (run.summary or {}).get("variants", {}).get(str(variant.pk), {}).get("metrics", {})
    vals = []
    for m in metrics.values():
        v = m.get("mean")
        if v is None:
            v = m.get("pass_rate")
        if v is not None:
            vals.append(v)
    return round(sum(vals) / len(vals) * 100, 1) if vals else 0.0


def build_experiment(
    *,
    capability_,
    dataset,
    eval_set,
    triggered_by,
    born,
    done,
    entrypoint,
    module,
    baseline_q,
    iteration_qs,
    diffs,
    status="completed",
    num_iterations=None,
    with_eval_runs=False,
):
    """iteration_qs: list per iteration of per-candidate quality targets."""
    exp = OptimizerExperiment.objects.create(
        project=capability_.project,
        capability=capability_,
        dataset=dataset,
        cell=dataset.active_cell,
        eval_set=eval_set,
        triggered_by=triggered_by,
        entrypoint=entrypoint,
        code_trigger=f"{module}.{entrypoint}(**datapoint)",
        status=status,
        num_iterations=num_iterations or len(iteration_qs),
        num_candidates_per_iteration=len(iteration_qs[0]) if iteration_qs else 3,
        command_template=_COMMAND_TEMPLATE.format(module=module, entry=entrypoint),
        cursor_usage={},
    )
    datapoints = list(row_store.iter_rows(dataset.active_cell))
    span_hours = max(4.0, (done - born).total_seconds() / 3600.0)
    total_iters = 1 + len(iteration_qs)
    cmd_pairs, cand_pairs, iter_pairs = [], [], []

    def _mk_eval_run(candidate, label, quality, t):
        run, variants = make_eval_run(
            name=f"Optimizer · {capability_.name} · {label}",
            description=f"Optimizer experiment {exp.pk}, {label}",
            capability_=capability_,
            dataset=dataset,
            eval_set=eval_set,
            members=list(eval_set.members.filter(role="generative")),
            variants=[(label, capability_.model, None, "existing", candidate.is_baseline, quality)],
            born=t,
            triggered_by=triggered_by,
            n_samples=len(datapoints),
            sample_fn=triage_sample,
        )
        variants[0].params = {"optimizer_candidate_id": str(candidate.pk)}
        variants[0].save(update_fields=["params"])
        return run

    def _commands_for(candidate, iteration, quality, t, trace_type, originals):
        per_dp_scores = []
        rows = []
        for idx, dp in enumerate(datapoints):
            score = round(min(100.0, max(5.0, random.gauss(quality * 100, 7.0))), 1)
            per_dp_scores.append(score)
            out = dp.expected_output if isinstance(dp.expected_output, dict) else {}
            trace_id = hexid(16)
            rows.append(
                OptimizerCommand(
                    experiment=exp,
                    candidate=candidate,
                    iteration=iteration,
                    datapoint_index=idx,
                    input=dp.input,
                    command=exp.command_template.replace("__DATAPOINT_INPUT__", repr(dp.input)),
                    output=json.dumps(out)[:400],
                    result={
                        "output": json.dumps(out)[:400],
                        "trace_id": trace_id,
                        "exit_code": 0,
                        "duration_ms": random.randint(900, 4200),
                        "stdout": "",
                        "stderr": "",
                    },
                    score=score,
                    trace_type=trace_type,
                    original_trace_id=originals[idx] if originals else "",
                    status="evaluated",
                    timeout=600,
                )
            )
        OptimizerCommand.objects.bulk_create(rows)
        for j, r in enumerate(rows):
            cmd_pairs.append((r.pk, t + timedelta(minutes=j * 0.5)))
        return rows, round(sum(per_dp_scores) / len(per_dp_scores), 1)

    # Baseline
    t = born + timedelta(minutes=20)
    base_iter = OptimizerIteration.objects.create(
        experiment=exp, order=0, name="Baseline", status="evaluated"
    )
    base_cand = OptimizerCandidate.objects.create(
        experiment=exp,
        iteration=base_iter,
        candidate_index=0,
        code_path="",
        is_baseline=True,
        status="evaluated",
    )
    base_cmds, base_score = _commands_for(base_cand, base_iter, baseline_q, t, "original", None)
    base_originals = [c.result["trace_id"] for c in base_cmds]

    def _align_commands(candidate, target, current_mean):
        # candidate.score must equal the mean of its command scores exactly.
        from django.db.models import F

        OptimizerCommand.objects.filter(candidate=candidate).update(
            score=F("score") + (target - current_mean)
        )

    if with_eval_runs:
        base_run = _mk_eval_run(base_cand, "baseline", baseline_q, t)
        new_score = _variant_score(base_run, base_run.variants.first())
        _align_commands(base_cand, new_score, base_score)
        base_score = new_score
        base_cand.eval_run = base_run
    base_cand.score = base_score
    base_cand.save(update_fields=["score", "eval_run"] if with_eval_runs else ["score"])
    base_iter.scores = {"best": base_score}
    base_iter.save(update_fields=["scores"])
    iter_pairs.append((base_iter.pk, t))
    cand_pairs.append((base_cand.pk, t))

    scores = {"baseline": base_score, "best": base_score}
    stalled = 0
    winner = None
    for i, cand_qs in enumerate(iteration_qs, start=1):
        t = born + timedelta(hours=span_hours * i / total_iters)
        iteration = OptimizerIteration.objects.create(
            experiment=exp, order=i, name=f"Iteration{i}", status="completed"
        )
        iter_pairs.append((iteration.pk, t))
        iter_scores = {}
        iter_best_cand, iter_best = None, -1.0
        for ci, q in enumerate(cand_qs):
            cand = OptimizerCandidate.objects.create(
                experiment=exp,
                iteration=iteration,
                candidate_index=ci,
                code_path=diffs[(i + ci) % len(diffs)],
                is_baseline=False,
                status="evaluated",
            )
            _, cscore = _commands_for(
                cand, iteration, q, t + timedelta(minutes=5 + ci * 10), "replay", base_originals
            )
            if with_eval_runs:
                crun = _mk_eval_run(cand, f"candidate {ci}", q, t + timedelta(minutes=5 + ci * 10))
                new_score = _variant_score(crun, crun.variants.first())
                _align_commands(cand, new_score, cscore)
                cscore = new_score
                cand.eval_run = crun
            cand.score = cscore
            cand.save(update_fields=["score", "eval_run"] if with_eval_runs else ["score"])
            cand_pairs.append((cand.pk, t + timedelta(minutes=5 + ci * 10)))
            iter_scores[str(cand.pk)] = cscore
            if cscore > iter_best:
                iter_best, iter_best_cand = cscore, cand
        scores[str(i)] = iter_scores
        iteration.scores = {"best": iter_best}
        iteration.save(update_fields=["scores"])
        if iter_best > scores["best"] + 1.0:
            scores["best"] = iter_best
            winner = iter_best_cand
            stalled = 0
        else:
            stalled += 1

    exp.scores = scores
    exp.current_iteration = len(iteration_qs)
    exp.stalled_iterations = stalled
    exp.cursor_usage = {
        "input_tokens": random.randint(280_000, 520_000),
        "output_tokens": random.randint(24_000, 48_000),
        "cache_read_tokens": random.randint(800_000, 1_500_000),
        "cache_write_tokens": random.randint(60_000, 130_000),
        "reasoning_tokens": random.randint(9_000, 26_000),
    }
    exp.cursor_usage["total_tokens"] = sum(
        v for k, v in exp.cursor_usage.items() if k != "reasoning_tokens"
    )
    state = {"eval_pending": {}}
    if status == "completed" and winner is not None:
        state["winner_score"] = scores["best"]
        state["winner_candidate_id"] = str(winner.pk)
    exp.state = state
    exp.save()
    backdate(OptimizerCommand, cmd_pairs)
    backdate(OptimizerCandidate, cand_pairs)
    backdate(OptimizerIteration, iter_pairs)
    OptimizerExperiment.objects.filter(pk=exp.pk).update(created_at=born, updated_at=done)
    return exp, winner


# A small pinned subset keeps optimiser command volume realistic per-run.
triage_opt_subset = _from_traces(
    triage_capability,
    "Triage Optimiser Subset",
    want=12,
    intent="eval",
    born=days_ago(39),
)
Dataset.objects.filter(pk=triage_opt_subset.pk).update(
    created_at=days_ago(39), updated_at=days_ago(39)
)

# Flagship: triage prompt optimisation, +12-ish points.
triage_exp, triage_winner = build_experiment(
    capability_=triage_capability,
    dataset=triage_opt_subset,
    eval_set=triage_set,
    triggered_by=sofia,
    born=days_ago(38),
    done=days_ago(31),
    entrypoint="run_triage",
    module="capabilities.triage.capability",
    baseline_q=0.714,
    iteration_qs=[
        [0.75, 0.69, 0.73],
        [0.80, 0.74, 0.76],
        [0.86, 0.78, 0.80],
        [0.83, 0.79, 0.82],
        [0.82, 0.80, 0.84],
    ],
    diffs=_TRIAGE_DIFFS,
    with_eval_runs=True,
)

# Earlier, smaller: KB citation prompt tightening (no eval-run linkage — ran
# before the eval integration shipped).
kb_exp, _ = build_experiment(
    capability_=kb_capability,
    dataset=kb_eval,
    eval_set=kb_set,
    triggered_by=priya,
    born=days_ago(52),
    done=days_ago(51),
    entrypoint="answer_question",
    module="capabilities.kb.capability",
    baseline_q=0.83,
    iteration_qs=[[0.85, 0.81], [0.87, 0.84], [0.86, 0.87]],
    diffs=_TRIAGE_DIFFS[:1],
    num_iterations=3,
)

# In-flight this week, deliberately paused before iteration 3.
sql_exp, _ = build_experiment(
    capability_=sql_capability,
    dataset=sql_from_traces,
    eval_set=sql_set,
    triggered_by=diego,
    born=days_ago(3),
    done=days_ago(1),
    entrypoint="answer",
    module="analyst.capabilities.sql_analyst",
    baseline_q=0.79,
    iteration_qs=[[0.80, 0.77, 0.81], [0.83, 0.79, 0.82]],
    diffs=_TRIAGE_DIFFS[2:],
    status="paused",
    num_iterations=5,
)


bt_run = BacktestRun.objects.create(
    capability=sql_capability,
    prompt_id="sql-analyst-v2",
    models_config=["openai/gpt-5.6-terra", "anthropic/claude-sonnet-5", "google/gemini-2.5-pro"],
    status="completed",
    celery_task_id=str(uuid.uuid4()),
    completed_at=days_ago(45, h=-1),
)
BacktestRun.objects.filter(pk=bt_run.pk).update(created_at=days_ago(45))


print("Creating fine-tuning jobs...")


def gen_training_curves(total_steps, epochs, *, start_loss, end_loss, start_acc, end_acc, lr0):
    metrics_history, eval_history = [], []
    loss_series, lr_series, gn_series, acc_series = [], [], [], []
    checkpoints = []
    eval_every = max(1, total_steps // (epochs * 2))
    warmup = max(1, int(total_steps * 0.05))
    for step in range(10, total_steps + 1, 10):
        frac = step / total_steps
        train_loss = round(
            end_loss + (start_loss - end_loss) * (1 - frac) ** 1.6 + random.uniform(-0.03, 0.03), 4
        )
        acc = round(
            start_acc + (end_acc - start_acc) * frac**0.7 + random.uniform(-0.008, 0.008), 4
        )
        lr = round(
            lr0 * (step / warmup)
            if step < warmup
            else lr0 * max(0.02, 1 - (step - warmup) / (total_steps - warmup)),
            8,
        )
        gn = round(random.uniform(0.4, 1.8) * (1.6 - frac), 3)
        epoch = round(frac * epochs, 2)
        metrics_history.append(
            {
                "step": step,
                "epoch": epoch,
                "train_loss": train_loss,
                "token_accuracy": acc,
                "lr": lr,
                "grad_norm": gn,
            }
        )
        point = {"step": step, "train_loss": train_loss}
        lr_series.append({"step": step, "value": lr})
        gn_series.append({"step": step, "value": gn})
        acc_point = {"step": step, "train": acc}
        if step % eval_every < 10:
            eval_loss = round(train_loss + random.uniform(0.04, 0.14), 4)
            eval_acc = round(acc - random.uniform(0.01, 0.03), 4)
            eval_history.append(
                {
                    "step": step,
                    "epoch": epoch,
                    "eval_loss": eval_loss,
                    "eval_token_accuracy": eval_acc,
                }
            )
            point["eval_loss"] = eval_loss
            acc_point["eval"] = eval_acc
        loss_series.append(point)
        acc_series.append(acc_point)
    per_epoch = total_steps // epochs
    for e in range(1, epochs + 1):
        step = per_epoch * e
        near = min(metrics_history, key=lambda r: abs(r["step"] - step))
        neare = min(eval_history, key=lambda r: abs(r["step"] - step))
        checkpoints.append(
            {
                "id": f"checkpoint-{step}",
                "step": step,
                "type": "checkpoint",
                "path": f"checkpoint-{step}",
                "train_loss": near["train_loss"],
                "valid_loss": neare["eval_loss"],
                "valid_mean_token_accuracy": neare["eval_token_accuracy"],
                "result_files": [],
                "file_count": 0,
                "created_at": 0,  # filled by caller with epoch-ns
                "resumable": True,
                "has_eval": True,
                "upload_status": "uploaded",
            }
        )
    epoch_losses = [
        {
            "epoch": e,
            "train_loss": checkpoints[e - 1]["train_loss"],
            "valid_loss": checkpoints[e - 1]["valid_loss"],
        }
        for e in range(1, epochs + 1)
    ]
    metrics = {
        "loss": loss_series,
        "learning_rate": lr_series,
        "grad_norm": gn_series,
        "token_accuracy": acc_series,
    }
    return metrics_history, eval_history, metrics, checkpoints, epoch_losses


def _hyper(epochs, lr, batch, ctx, lora_r):
    return {
        "n_epochs": epochs,
        "learning_rate": lr,
        "batch_size": batch,
        "warmup_ratio": 0.05,
        "context_length": ctx,
        "packing": False,
        "training_type": {
            "type": "Lora",
            "lora_r": lora_r,
            "lora_alpha": lora_r * 2,
            "lora_dropout": 0.05,
            "lora_trainable_modules": "all-linear",
        },
        "eval_max_items": 25,
    }


def make_ft_job(
    *,
    job_id,
    capability_,
    dataset,
    eval_dataset,
    eval_set,
    name,
    use_case,
    base_model,
    tier,
    born,
    done,
    epochs,
    total_steps,
    curves,
    hyper,
    remote,
    output_name,
    baseline_model,
    train_examples,
    val_examples,
    cost=None,
    minutes=None,
    status="succeeded",
    error="",
    group=None,
):
    metrics_history, eval_history, metrics, checkpoints, epoch_losses = curves
    started = born + timedelta(minutes=4)
    for i, cp in enumerate(checkpoints):
        cp["created_at"] = int(
            (started + (done - started) * ((i + 1) / len(checkpoints))).timestamp() * 1e9
        )
    latest = metrics_history[-1]
    latest_eval = eval_history[-1]
    elapsed = int((done - started).total_seconds())
    progress = {
        "epochs_completed": epochs,
        "tokens_processed": train_examples * random.randint(380, 520) * epochs,
        "trained_steps": total_steps,
        "total_steps": total_steps,
        "percent": 100.0,
        "estimated_finish": None,
        "eta_seconds": None,
        "elapsed_seconds": elapsed,
        "phase": "finalizing",
        "stage": "model_loaded",
        "download": None,
        "provider_status": "succeeded",
        "latest_train_loss": latest["train_loss"],
        "latest_eval_loss": latest_eval["eval_loss"],
        "train_loss": latest["train_loss"],
        "eval_loss": latest_eval["eval_loss"],
        "learning_rate": latest["lr"],
        "token_accuracy": latest["token_accuracy"],
        "eval_token_accuracy": latest_eval["eval_token_accuracy"],
        "current_epoch": float(epochs),
        "eta_s": 0.0,
        "metrics_history": metrics_history,
        "eval_history": eval_history,
        "activity": [
            {"ts": int((started + timedelta(seconds=s)).timestamp() * 1000), "message": msg}
            for s, msg in [
                (0, "Preparing dataset"),
                (40, f"Materialised {train_examples} training, {val_examples} validation examples"),
                (95, "Uploading training file"),
                (170, "Loading base model…"),
                (400, "Training started"),
                (elapsed - 60, "Training complete — merging adapter"),
            ]
        ],
        "metrics": metrics,
        "checkpoints": checkpoints,
    }
    result = {}
    if status == "succeeded":
        result = {
            "epoch_losses": epoch_losses,
            "model": output_name,
            "metrics": metrics,
            "checkpoints": checkpoints,
        }
    job = FinetuningJob.objects.create(
        id=job_id,
        project=capability_.project,
        capability=capability_,
        dataset=dataset,
        cell=dataset.active_cell,
        validation_enabled=True,
        validation_split_ratio=0.2,
        split_method="random",
        triggered_by=jonas,
        eval_dataset=eval_dataset,
        eval_set=eval_set,
        name=name,
        use_case=use_case,
        group_id=group,
        model_tier=tier,
        provider="baseten",
        base_model=base_model,
        hyperparameters=hyper,
        baseline_model=baseline_model,
        status=status,
        remote_job_id=remote,
        output_model_name=output_name if status == "succeeded" else "",
        progress=progress
        if status == "succeeded"
        else {
            "phase": "training",
            "percent": 34.0,
            "trained_steps": int(total_steps * 0.34),
            "total_steps": total_steps,
            "provider_status": "failed",
            "metrics_history": metrics_history[: len(metrics_history) // 3],
            "eval_history": eval_history[:2],
            "metrics": metrics,
            "checkpoints": [],
            "activity": progress["activity"][:4],
        },
        result=result,
        error_message=error,
        celery_task_id=str(uuid.uuid4()),
        cost_usd=Decimal(str(cost)) if cost is not None else None,
        billed_minutes=minutes,
        cost_synced_at=done if cost is not None else None,
        started_at=started,
        completed_at=done,
    )
    FinetuningJob.objects.filter(pk=job.pk).update(created_at=born, updated_at=done)
    events = [
        ("status_change", "Preparing dataset", {"status": "preparing"}, 1),
        (
            "log",
            f"Materialised {train_examples} training, {val_examples} validation examples",
            {
                "train_examples": train_examples,
                "val_examples": val_examples,
                "validation_mode": "split",
                "split_method": "random",
                "split_warnings": [],
            },
            2,
        ),
        (
            "log",
            f"Uploaded {train_examples} training examples",
            {"num_examples": train_examples},
            3,
        ),
        ("status_change", f"Submitted (remote_id={remote})", {"status": "running"}, 4),
    ]
    step_marks = [total_steps // 4, total_steps // 2, (3 * total_steps) // 4, total_steps]
    for i, s in enumerate(step_marks):
        near = min(metrics_history, key=lambda r: abs(r["step"] - s))
        events.append(
            (
                "progress",
                f"step {near['step']}",
                {
                    "step": near["step"],
                    "train_loss": near["train_loss"],
                    "eval_loss": next(
                        (
                            e["eval_loss"]
                            for e in eval_history
                            if abs(e["step"] - near["step"]) < 20
                        ),
                        None,
                    ),
                    "percent": round(100 * near["step"] / total_steps, 2),
                    "eta_seconds": int(elapsed * (1 - near["step"] / total_steps)),
                    "n_loss_points": near["step"] // 10,
                    "n_checkpoints": i,
                },
                6 + i * 8,
            )
        )
    if status == "succeeded":
        events.append(
            (
                "status_change",
                "Fine-tuning completed — deploying model",
                {"status": "deploying"},
                60,
            )
        )
    else:
        events.append(("error", f"poll failed: {error}", {"error": error}, 30))
        events.append(
            ("status_change", "Training failed", {"status": "failed", "error": error}, 31)
        )
    ev_pairs = []
    for etype, msg, data, minute in events:
        ev = FinetuningJobEvent.objects.create(job=job, event_type=etype, message=msg, data=data)
        ev_pairs.append((ev.pk, started + timedelta(minutes=minute)))
    backdate(FinetuningJobEvent, ev_pairs)
    return job


ft_expense_compact = make_ft_job(
    job_id=FT_EXPENSE_COMPACT_ID,
    capability_=receipt_capability,
    dataset=receipt_train,
    eval_dataset=receipt_golden,
    eval_set=receipt_set,
    name="receipt-extractor compact v1",
    use_case="Replace the frontier call for receipt extraction with an owned compact model.",
    base_model="meta-llama/Llama-3.2-3B-Instruct",
    tier="compact",
    born=days_ago(41),
    done=days_ago(41, h=-1.2),
    epochs=3,
    total_steps=260,
    curves=gen_training_curves(
        260, 3, start_loss=1.94, end_loss=0.44, start_acc=0.61, end_acc=0.9, lr0=2e-4
    ),
    hyper=_hyper(3, 2e-4, 4, 8192, 32),
    remote="7wl5x9q:3kq8n2m",
    output_name="baseten/3kq8n2m/final",
    baseline_model="openai/gpt-5.6-terra",
    train_examples=274,
    val_examples=68,
    cost=1.86,
    minutes=23,
)

ft_dispute = make_ft_job(
    job_id=FT_DISPUTE_ID,
    capability_=dispute_capability,
    dataset=dispute_train,
    eval_dataset=dispute_golden,
    eval_set=dispute_set,
    name="dispute-resolver 8B v1",
    use_case="Own the dispute-resolution model; cut cost and lock in the "
    "policy behaviour learned from production.",
    base_model="meta-llama/Llama-3.1-8B-Instruct",
    tier="small",
    born=days_ago(22),
    done=days_ago(22, h=-2.4),
    epochs=3,
    total_steps=372,
    curves=gen_training_curves(
        372, 3, start_loss=1.87, end_loss=0.41, start_acc=0.63, end_acc=0.89, lr0=2e-4
    ),
    hyper=_hyper(3, 2e-4, 3, 8192, 32),
    remote="7wl5x9q:9qw4k2v",
    output_name="baseten/9qw4k2v/final",
    baseline_model="anthropic/claude-sonnet-5",
    train_examples=325,
    val_examples=81,
    cost=2.43,
    minutes=37,
)

ft_expense_v2 = make_ft_job(
    job_id=FT_EXPENSE_V2_ID,
    capability_=receipt_capability,
    dataset=receipt_train,
    eval_dataset=None,
    eval_set=None,
    name="receipt-extractor v2 (qwen3-8b)",
    use_case="Chase the remaining tax_amount errors with a stronger base.",
    base_model="Qwen/Qwen3-8B",
    tier="small",
    born=days_ago(0, h=3.4),
    done=days_ago(0, h=2),
    epochs=2,
    total_steps=180,
    curves=gen_training_curves(
        180, 2, start_loss=1.61, end_loss=0.38, start_acc=0.66, end_acc=0.91, lr0=2e-4
    ),
    hyper=_hyper(2, 2e-4, 4, 8192, 16),
    remote="7wl5x9q:5rp1x8d",
    output_name="baseten/5rp1x8d/final",
    baseline_model=EXPENSE_COMPACT_MODEL_ID,
    train_examples=272,
    val_examples=68,
    cost=2.12,
    minutes=31,
)

ft_onboard_fail = make_ft_job(
    job_id=FT_ONBOARD_FAIL_ID,
    capability_=kyc_capability,
    dataset=kyc_eval,
    eval_dataset=None,
    eval_set=None,
    name="kyc-helper compact v0",
    use_case="First training attempt on the hand-written QA set.",
    base_model="Qwen/Qwen3-1.7B",
    tier="compact",
    born=days_ago(15),
    done=days_ago(15, h=-0.6),
    epochs=3,
    total_steps=90,
    curves=gen_training_curves(
        90, 3, start_loss=2.1, end_loss=0.9, start_acc=0.55, end_acc=0.7, lr0=2e-4
    ),
    hyper=_hyper(3, 2e-4, 8, 4096, 16),
    remote="7wl5x9q:2fd7c1q",
    output_name="",
    baseline_model="google/gemini-2.5-pro",
    train_examples=19,
    val_examples=5,
    status="failed",
    error="Training failed after step 32: CUDA out of memory. The provider "
    "retried twice with the same result — reduce batch_size or context "
    "length and retry.",
)

ft_jobs = [ft_expense_compact, ft_dispute, ft_expense_v2, ft_onboard_fail]

# ModelRefs for the fine-tunes (used by the benchmark runs' variants).
mr_ft_dispute = ModelRef.objects.create(
    project=support_proj,
    label="ft-llama-3.1-8b (dispute)",
    provider="custom",
    model_id=DISPUTE_MODEL_ID,
    finetuning_job=ft_dispute,
    params={"max_tokens": None},
)
mr_ft_expense = ModelRef.objects.create(
    project=expense_proj,
    label="ft-llama-3.2-3b (receipts)",
    provider="custom",
    model_id=EXPENSE_COMPACT_MODEL_ID,
    finetuning_job=ft_expense_compact,
    params={"max_tokens": None},
)
model_refs += [mr_ft_dispute, mr_ft_expense]


def _job_evals(job, bench_run, bench_variants, baseline_id, final_id, born):
    """Baseline + final FinetuningJobEval rows whose scores are read off the
    benchmark run's real summary."""
    summary = bench_run.summary or {}

    def agg(variant):
        metrics = summary.get("variants", {}).get(str(variant.pk), {}).get("metrics", {})
        vals = [
            m.get("mean") if m.get("mean") is not None else m.get("pass_rate")
            for m in metrics.values()
        ]
        vals = [v for v in vals if v is not None]
        return round(sum(vals) / len(vals), 6) if vals else None

    base_score = agg(bench_variants[0])
    final_score = agg(bench_variants[1])
    rows = []
    for kind, model_id, score, delta in (
        ("baseline", baseline_id, base_score, None),
        (
            "final",
            final_id,
            final_score,
            round(final_score - base_score, 6)
            if final_score is not None and base_score is not None
            else None,
        ),
    ):
        row = FinetuningJobEval.objects.create(
            job=job,
            eval_run=bench_run,
            kind=kind,
            status="completed",
            model_id=model_id,
            aggregate_score=score,
            baseline_delta=delta,
            class_metrics=None,
        )
        stamp(row, born, born + timedelta(minutes=30))
        rows.append(row)
    job.progress["judge_evals"] = [
        {
            "id": str(r.pk),
            "kind": r.kind,
            "status": "completed",
            "checkpoint_id": None,
            "checkpoint_step": None,
            "model_id": r.model_id,
            "aggregate_score": r.aggregate_score,
            "baseline_delta": r.baseline_delta,
            "class_metrics": None,
            "eval_run_id": str(bench_run.pk),
            "error_message": None,
            "created_at": born.isoformat(),
            "updated_at": born.isoformat(),
        }
        for r in rows
    ]
    job.save(update_fields=["progress"])
    return rows


_job_evals(
    ft_dispute,
    dispute_bench_run,
    dispute_bench_variants,
    "anthropic/claude-sonnet-5",
    DISPUTE_MODEL_ID,
    days_ago(21, h=-4),
)
_job_evals(
    ft_expense_compact,
    receipt_bench_run,
    receipt_bench_variants,
    "openai/gpt-5.6-terra",
    EXPENSE_COMPACT_MODEL_ID,
    days_ago(40, h=-5),
)
# The judge_evals write bumped updated_at; pin it back to the job timeline.
for job in (ft_dispute, ft_expense_compact):
    FinetuningJob.objects.filter(pk=job.pk).update(updated_at=job.completed_at)


print("Deploying models and generating inference traffic...")

MODAL_URL = "https://undermindhq--overmind-inference-{worker}-web.modal.run?model={model}&max_model_len=8192"

dm_compact = DeployedModel.objects.create(
    finetuning_job=ft_expense_compact,
    project=expense_proj,
    model_id=EXPENSE_COMPACT_MODEL_ID,
    status="ready",
    quantization="fp8",
    base_model_id="meta-llama/Llama-3.2-3B-Instruct",
    gpu_type="L4",
    is_lora=False,
    lora_rank=0,
    weights_path=f"/weights/{EXPENSE_COMPACT_MODEL_ID}",
    checkpoint_hash=hexid(32),
    max_model_len=8192,
    num_parameters=3_212_749_824,
    sla_tier="hot",
    inference_url=MODAL_URL.format(worker="l4-vllm", model=EXPENSE_COMPACT_MODEL_ID),
    deployed_at=days_ago(40, h=-1),
)
DeployedModel.objects.filter(pk=dm_compact.pk).update(created_at=days_ago(41, h=-1.3))

dm_dispute = DeployedModel.objects.create(
    finetuning_job=ft_dispute,
    project=support_proj,
    model_id=DISPUTE_MODEL_ID,
    status="ready",
    quantization="fp8",
    base_model_id="meta-llama/Llama-3.1-8B-Instruct",
    gpu_type="L4",
    is_lora=False,
    lora_rank=0,
    weights_path=f"/weights/{DISPUTE_MODEL_ID}",
    checkpoint_hash=hexid(32),
    max_model_len=8192,
    num_parameters=8_030_261_248,
    sla_tier="standard",
    inference_url=MODAL_URL.format(worker="l4-vllm", model=DISPUTE_MODEL_ID),
    deployed_at=days_ago(21, h=-1),
)
DeployedModel.objects.filter(pk=dm_dispute.pk).update(created_at=days_ago(22, h=-2.6))

dm_v2 = DeployedModel.objects.create(
    finetuning_job=ft_expense_v2,
    project=expense_proj,
    model_id=EXPENSE_V2_MODEL_ID,
    status="ready",
    quantization="fp8",
    base_model_id="Qwen/Qwen3-8B",
    gpu_type="L4",
    is_lora=False,
    lora_rank=0,
    weights_path=f"/weights/{EXPENSE_V2_MODEL_ID}",
    checkpoint_hash=hexid(32),
    max_model_len=8192,
    num_parameters=8_190_735_360,
    sla_tier="standard",
    inference_url=MODAL_URL.format(worker="l4-vllm", model=EXPENSE_V2_MODEL_ID),
    deployed_at=days_ago(0, h=1),
)
DeployedModel.objects.filter(pk=dm_v2.pk).update(created_at=days_ago(0, h=2))

L4_USD_PER_SECOND = 0.80 / 3600

call_rows, call_times, ledger_rows, ledger_times = [], [], [], []


def gen_calls(dm, day, n, user_):
    for _ in range(n):
        t = business_hour(day)
        if t > NOW:
            continue
        cold = random.random() < 0.03
        pt = random.randint(250, 1900)
        ct = random.randint(40, 380)
        latency = rnd(15000, 38000, 1) if cold else rnd(700, 3800, 1)
        tps = None if cold else round(ct / (latency / 1000), 1)
        cost = round((latency / 1000) * L4_USD_PER_SECOND, 6)
        call = InferenceCall(
            deployed_model=dm,
            project_id=dm.project_id,
            prompt_tokens=pt,
            completion_tokens=ct,
            cost=cost,
            tokens_per_second=tps,
            latency_ms=latency,
            is_cold=cold,
        )
        call_rows.append(call)
        call_times.append(t)
        ledger_rows.append(
            BillingTelemetry(
                user=user_,
                project_id=dm.project_id,
                amount=Decimal(str(-cost)),
                service=BillingService.INFERENCE_FT_MODEL,
                idempotency_key=f"inference-ft:{call.pk}",
                metadata={"model": dm.model_id, "prompt_tokens": pt, "completion_tokens": ct},
            )
        )
        ledger_times.append(t)


for i in range(41):
    day = days_ago(40 - i)
    gen_calls(dm_compact, day, daily_volume(30, 40, i, day.weekday()), jonas)
for i in range(22):
    day = days_ago(21 - i)
    gen_calls(dm_dispute, day, daily_volume(150, 21, i, day.weekday()), priya)

# The v2 model went live an hour ago: a cold start, then early traffic — with
# the freshest calls inside the live-badge window. Anchored on real wall-clock
# (not the quantised NOW) so the activity reads as happening right now.
_wall = timezone.now()
for mins, cold in [
    (58, True),
    (54, False),
    (47, False),
    (40, False),
    (33, False),
    (26, False),
    (19, False),
    (12, False),
    (7, False),
    (3, False),
    (1, False),
]:
    t = _wall - timedelta(minutes=mins)
    pt, ct = random.randint(400, 1400), random.randint(60, 260)
    latency = rnd(21000, 33000, 1) if cold else rnd(600, 2400, 1)
    cost = round((latency / 1000) * L4_USD_PER_SECOND, 6)
    call = InferenceCall(
        deployed_model=dm_v2,
        project_id=expense_proj.pk,
        prompt_tokens=pt,
        completion_tokens=ct,
        cost=cost,
        tokens_per_second=None if cold else round(ct / (latency / 1000), 1),
        latency_ms=latency,
        is_cold=cold,
    )
    call_rows.append(call)
    call_times.append(t)
# Keep the dispute model reading "live" too.
for secs in (75, 42, 18):
    t = _wall - timedelta(seconds=secs)
    pt, ct = random.randint(600, 1800), random.randint(80, 300)
    latency = rnd(900, 2600, 1)
    cost = round((latency / 1000) * L4_USD_PER_SECOND, 6)
    call_rows.append(
        InferenceCall(
            deployed_model=dm_dispute,
            project_id=support_proj.pk,
            prompt_tokens=pt,
            completion_tokens=ct,
            cost=cost,
            tokens_per_second=round(ct / (latency / 1000), 1),
            latency_ms=latency,
            is_cold=False,
        )
    )
    call_times.append(t)

InferenceCall.objects.bulk_create(call_rows, batch_size=1000)
backdate(InferenceCall, list(zip((c.pk for c in call_rows), call_times, strict=True)))
BillingTelemetry.objects.bulk_create(ledger_rows, batch_size=1000)
backdate(
    BillingTelemetry,
    list(zip((r.pk for r in ledger_rows), ledger_times, strict=True)),
    column="timestamp",
)


for user_, amt, days_, sess in (
    (priya, "100.0000000", 50, "cs_live_a1B2c3D4"),
    (jonas, "50.0000000", 30, "cs_live_e5F6g7H8"),
):
    row = BillingTelemetry.objects.create(
        user=user_,
        amount=Decimal(amt),
        service=BillingService.STRIPE_TOPUP,
        idempotency_key=f"topup:{sess}",
        metadata={"checkout_session_id": sess},
    )
    backdate(BillingTelemetry, [(row.pk, days_ago(days_))], column="timestamp")

for job in (ft_expense_compact, ft_dispute, ft_expense_v2):
    if job.cost_usd is None:
        continue
    row = BillingTelemetry.objects.create(
        user=jonas,
        project=job.project,
        amount=-job.cost_usd,
        service=BillingService.FINETUNING_JOB,
        idempotency_key=f"finetuning-job:{job.pk}:{job.cost_usd}",
        metadata={
            "job_name": job.name,
            "base_model": job.base_model,
            "billed_minutes": job.billed_minutes,
        },
    )
    backdate(BillingTelemetry, [(row.pk, job.completed_at)], column="timestamp")


for user_, category, text, days_, read in [
    (
        amara,
        "love",
        "The live trace scores caught a mis-routing regression "
        "before our SLA dashboard did. Genuinely saved a fire drill.",
        27,
        True,
    ),
    (
        diego,
        "feature",
        "Would love scheduled eval runs — we run the SQL golden set manually every Monday.",
        19,
        True,
    ),
    (
        sofia,
        "bug",
        "Optimiser activity log occasionally shows commands out of "
        "order when two candidates finish within the same second.",
        16,
        True,
    ),
    (
        jonas,
        "improvement",
        "Training page: surface the eval-vs-train loss gap "
        "directly, we watch for overfitting on every run.",
        9,
        False,
    ),
    (priya, "question", "Can share links be scoped to expire after the board meeting?", 4, False),
]:
    f = Feedback.objects.create(user=user_, category=category, feedback=text, read=read)
    Feedback.objects.filter(pk=f.pk).update(created_at=days_ago(days_))


print("\nSeed complete — Undermind workspace.")
_counts = [
    ("Users", User.objects.filter(email__endswith=f"@{SEED_USER_DOMAIN}").count()),
    ("Projects", len(seed_projects)),
    ("Capabilities", len(capabilities)),
    ("Spans", Span.objects.filter(project__in=seed_projects).count()),
    ("Traces", Span.objects.filter(project__in=seed_projects, parent_span_id__isnull=True).count()),
    ("Sessions", Conversation.objects.filter(project__in=seed_projects).count()),
    ("Datasets", Dataset.objects.filter(project__in=seed_projects).count()),
    ("Cells", Cell.objects.filter(dataset__project__in=seed_projects).count()),
    ("Evaluators", Evaluator.objects.filter(project__in=seed_projects).count()),
    ("Eval runs", EvalRun.objects.filter(project__in=seed_projects).count()),
    ("Eval samples", EvalSample.objects.filter(run__project__in=seed_projects).count()),
    ("Scores", Score.objects.filter(project__in=seed_projects).count()),
    (
        "Optimiser experiments",
        OptimizerExperiment.objects.filter(project__in=seed_projects).count(),
    ),
    (
        "Optimiser commands",
        OptimizerCommand.objects.filter(experiment__project__in=seed_projects).count(),
    ),
    ("Finetuning jobs", FinetuningJob.objects.filter(project__in=seed_projects).count()),
    ("Deployed models", DeployedModel.objects.filter(project__in=seed_projects).count()),
    ("Inference calls", InferenceCall.objects.filter(project__in=seed_projects).count()),
    (
        "Ledger entries",
        BillingTelemetry.objects.filter(user__email__endswith=f"@{SEED_USER_DOMAIN}").count(),
    ),
]
for label, count in _counts:
    print(f"   {label:22}: {count}")

for ds in seed_datasets:
    ds.refresh_from_db()
    head = ds.active_cell
    owner = ds.capability.name if ds.capability_id else f"{ds.project.name} (unassigned)"
    print(f"     - {ds.name} [{ds.intent}/{ds.source_kind}] {head.rows} rows — {owner}")

print("\nDemo login: admin@undermindlab.ai / password  (superuser, all projects)")
print("Team accounts (password: undermind-demo):")
for u in team:
    print(f"   {u.email}")
print("\nAPI keys (plaintext, shown once):")
for label, raw in raw_keys:
    print(f"   {label}: {raw}")
