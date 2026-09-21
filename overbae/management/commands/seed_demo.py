"""Run: docker compose exec api python manage.py seed_demo [--owner EMAIL]

One demo project — Support Copilot at Ledgerline, a fictional payments company — with
thirty days of traffic across three capabilities and every downstream surface filled:
tasks and verdicts, datasets with versions and chats, eval runs, optimiser runs, training
jobs, served models and the credits ledger.

Deterministic (seeded RNG, stable ids, timestamps anchored to NOW) and idempotent: a re-run
deletes the project (and the retired Undermind demo, if present) before it seeds. The
project belongs to --owner (default frey@overmindlab.ai); the account is created
with password ``password`` when it does not exist.

Beat-safety — workers and beat stay up while this runs:
- every job, run and experiment is TERMINAL, or the reconcilers re-drive it;
- every root span carries a feedback block and a backdated ``received_at``, and every
  scored trace has a ScoringPass row, or the trace-scoring sweep re-drives it;
- the eval preload and rebind hooks that a real sync fires are patched to no-ops;
- connectors keep ``auto_sync_enabled=False``.
"""

import hashlib
import json
import random
import re
import shutil
import uuid
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db import connection
from django.db.models import Avg
from django.db.models.signals import post_save
from django.utils import timezone

from overbae.models import (
    APIToken,
    Behaviour,
    BillingService,
    BillingTelemetry,
    Capability,
    Cell,
    ConnectorCredential,
    Conversation,
    Dataset,
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
    ModelRef,
    OptimizerCandidate,
    OptimizerCommand,
    OptimizerExperiment,
    OptimizerIteration,
    Project,
    ProjectMembership,
    RunEvaluator,
    Score,
    ScoringPass,
    Span,
    TaskExecution,
    UserOnboarding,
    Verdict,
)
from overbae.models.traces import usage_slice
from overbae.services import sync as sync_service
from overbae.services.capabilities import identity as capability_identity
from overbae.services.datasets import land as dataset_land
from overbae.services.datasets import lifecycle as dataset_lifecycle
from overbae.services.datasets import paths as dataset_paths
from overbae.services.datasets import rows as row_store
from overbae.services.datasets.notebook import run as notebook_run
from overbae.services.eval import composition
from overbae.services.eval import dispatch as eval_dispatch
from overbae.services.eval import snapshots as eval_snapshots
from overbae.services.eval.managed import upsert_managed_evaluators
from overbae.signals import sync_evaluators_on_card_change
from overbae.tasks import eval as eval_tasks

User = get_user_model()
NOW = timezone.now().replace(minute=0, second=0, microsecond=0)
DAYS = 30
SLUG = "support-copilot"
TEAM_DOMAIN = "ledgerline.dev"
RETIRED_SLUGS = (
    "payments-analyst",
    "expense-audit",
    "growth-outreach",
    "merchant-onboarding",
)
RETIRED_DOMAIN = "undermindlab.ai"
SHA = "7f3c2a9e1b4d8c6f0a2e5b7d9c1f3a5e7b9d1f3a"
TRIAGE_PROMPT = (
    "You are Ledgerline's support triage capability.\n\n"
    "Process:\n"
    "1. Identify the failure surface (API, dashboard, payouts, billing, fraud).\n"
    "2. Call lookup_merchant; enterprise merchants are never `low` urgency.\n"
    "3. Payment-disruption reports route to oncall-payments; suspected fraud to risk-ops.\n"
    "4. Summaries never quote card numbers.\n\n"
    "Return strict JSON matching the schema. Urgency reflects merchant impact, not sentiment."
)
KB_PROMPT = (
    "Answer the merchant's question using only the retrieved help-centre articles. "
    "Every sentence that states a fact carries a citation by article slug. If the articles "
    "do not answer the question, say so and set confidence below 0.3. Return JSON."
)
DISPUTE_PROMPT = (
    "You resolve card disputes for Ledgerline merchants.\n\n"
    "Always: lookup_transaction → fetch_dispute_evidence → check_policy, then decide. "
    "Represent only with two independent evidence classes. Accept friendly-fraud under $25. "
    "Never promise refund timelines. Return strict JSON."
)
TRIAGE_ID = uuid.uuid5(uuid.NAMESPACE_URL, "seed/capability/ticket-triage")
KB_ID = uuid.uuid5(uuid.NAMESPACE_URL, "seed/capability/kb-answerer")
DISPUTE_ID = uuid.uuid5(uuid.NAMESPACE_URL, "seed/capability/dispute-resolver")
JUDGE = "anthropic/claude-sonnet-5"
MODEL_RATES = {
    "openai/gpt-5.6-sol": (3.0, 12.0),
    "openai/gpt-5.6-terra": (1.1, 4.4),
    "anthropic/claude-sonnet-5": (3.0, 15.0),
    "google/gemini-3.1-pro-preview": (1.25, 10.0),
}
FT_TRIAGE_JOB_ID = uuid.uuid5(uuid.NAMESPACE_URL, "seed/ft/triage-qwen3-4b")
FT_TRIAGE_ALT_JOB_ID = uuid.uuid5(uuid.NAMESPACE_URL, "seed/ft/triage-llama-3b")
FT_DISPUTE_JOB_ID = uuid.uuid5(uuid.NAMESPACE_URL, "seed/ft/dispute-8b")
TRIAGE_MODEL_ID = f"ft-{str(FT_TRIAGE_JOB_ID)[:8]}-qwen3-4b"
DISPUTE_MODEL_ID = f"ft-{str(FT_DISPUTE_JOB_ID)[:8]}-llama-3-1-8b-instruct"
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
TEAMS = [
    "oncall-payments",
    "oncall-platform",
    "billing-support",
    "support-general",
    "risk-ops",
    "integrations",
    "product",
]
TICKETS = [
    (
        "Payouts to our {bank} account have been stuck in 'in transit' since {day}. Nothing arrived and our vendors are waiting.",
        "Payouts / Delays",
        "oncall-payments",
        "high",
        "critical",
    ),
    (
        "All API requests started returning 503 about twenty minutes ago. Checkout is down on our site.",
        "API / Availability",
        "oncall-platform",
        "critical",
        "critical",
    ),
    (
        "Webhook deliveries for payment_intent.succeeded are arriving 10–15 minutes late since this morning.",
        "API / Webhooks",
        "oncall-platform",
        "high",
        "high",
    ),
    (
        "We were charged twice for the {month} platform invoice — can you check and refund the duplicate?",
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
        "A customer in {country} says their card was charged but the order never went through. Payment {pid}.",
        "Payments / Failed charge",
        "support-general",
        "medium",
        "high",
    ),
    (
        "We see three refunds we never issued on the dashboard this morning. Is our account compromised?",
        "Fraud / Account security",
        "risk-ops",
        "critical",
        "critical",
    ),
    (
        "The Shopify integration stopped syncing orders after we changed our store domain.",
        "Integrations / Shopify",
        "integrations",
        "medium",
        "high",
    ),
    (
        "Can you export our settlement report as CSV for {month}? The button is greyed out.",
        "Reports / Exports",
        "support-general",
        "low",
        "low",
    ),
    (
        "Our terminal in the {city} store shows 'offline' but the Wi-Fi is fine.",
        "Hardware / Terminal",
        "product",
        "medium",
        "high",
    ),
    (
        "Dispute alerts are not reaching our finance inbox anymore.",
        "Notifications",
        "product",
        "medium",
        "medium",
    ),
    (
        "3DS challenges are failing for every Visa card since the update. Conversion dropped by half.",
        "Payments / 3DS",
        "oncall-payments",
        "critical",
        "critical",
    ),
]
KB_QA = [
    (
        "How long do payouts take to settle?",
        "Payouts settle in two business days for growth plans and next-day for enterprise [payouts-schedule].",
        ["payouts-schedule", "plans-overview"],
    ),
    (
        "Can I issue a partial refund?",
        "Yes — open the payment and choose Refund, then enter an amount below the total [refunds-guide].",
        ["refunds-guide"],
    ),
    (
        "How do I rotate my API key?",
        "Create a new key under Developers → API keys, deploy it, then revoke the old one [api-keys].",
        ["api-keys", "security-best-practices"],
    ),
    (
        "What is the dispute response deadline?",
        "You have 20 days from the dispute notice to submit evidence [disputes-overview].",
        ["disputes-overview"],
    ),
    (
        "Does the terminal work offline?",
        "Offline mode queues up to 50 payments and syncs when the connection returns [terminal-offline].",
        ["terminal-offline"],
    ),
    (
        "How are currency conversions priced?",
        "Cross-currency payments carry a 1% conversion fee on top of the standard rate [pricing-fx].",
        ["pricing-fx", "plans-overview"],
    ),
    (
        "Can I schedule payouts weekly?",
        "Yes — choose Weekly under Settings → Payouts and pick the weekday [payouts-schedule].",
        ["payouts-schedule"],
    ),
    (
        "Where do I find the settlement report?",
        "Reports → Settlements lists every payout with its fees; export is CSV [reports-settlement].",
        ["reports-settlement"],
    ),
]
DISPUTE_REASONS = [
    ("fraudulent", "10.4"),
    ("product_not_received", "13.1"),
    ("duplicate", "12.6"),
    ("credit_not_processed", "13.6"),
    ("product_unacceptable", "13.3"),
    ("subscription_canceled", "13.2"),
]
_TRIAGE_RATIONALES = [
    "Urgency and team match the reference; category wording differs but names the same surface.",
    "All three fields match the golden triage.",
    "Team matches; urgency one level below the reference for an enterprise merchant.",
    "Routed to support-general where the reference routes to oncall-payments.",
]
_TRACE_EVAL_SCRIPT = (
    "df = df[df['input'].notna() & df['output'].notna()]\n"
    "df = df.rename(columns={'output': 'expected_output'})\n"
    "df = df.drop(columns=[c for c in ('messages', 'tools') if c in df.columns])\n"
)
_JUDGE_REASONS = {
    "Triage Accuracy": [
        "Urgency and team match the reference; category wording differs but names the same surface.",
        "Team matches; urgency one level below the reference for an enterprise merchant.",
        "All three fields match the golden triage.",
        "Routed to support-general where the reference routes to oncall-payments.",
    ],
    "SLA Floor Respected": [
        "Urgency at or above the plan floor.",
        "Enterprise merchant routed low.",
    ],
    "Correctness": [
        "The answer matches the reference on every material point.",
        "Substantively correct; one secondary detail differs.",
        "Misses the reference's key qualifier.",
    ],
    "Conciseness": [
        "Tight summary with no filler.",
        "Slightly padded but within reason.",
        "Repeats the ticket text nearly verbatim.",
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
    "Evidence Request Clarity": [
        "Names the missing evidence classes and nothing else.",
        "Asks for 'more information' without naming a class.",
    ],
}
_COMMAND_TEMPLATE = """uv run python - <<'EOF'
from support.triage.capability import run_triage
self.stdout.write(run_triage(**__DATAPOINT_INPUT__))
EOF
"""
_TRIAGE_DIFFS = [
    """diff --git a/support/triage/prompts.py b/support/triage/prompts.py
index 4c1f2ab..8e9d310 100644
--- a/support/triage/prompts.py
+++ b/support/triage/prompts.py
@@ -1,12 +1,18 @@
 SYSTEM_PROMPT = \"\"\"You are Ledgerline's support triage capability.
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
    """diff --git a/support/triage/prompts.py b/support/triage/prompts.py
index 4c1f2ab..2b7a914 100644
--- a/support/triage/prompts.py
+++ b/support/triage/prompts.py
@@ -1,8 +1,13 @@
 SYSTEM_PROMPT = \"\"\"You are Ledgerline's support triage capability.
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
    """diff --git a/support/triage/capability.py b/support/triage/capability.py
index 91c0d44..d02f871 100644
--- a/support/triage/capability.py
+++ b/support/triage/capability.py
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
TRIAGE_GROUP = uuid.uuid5(uuid.NAMESPACE_URL, "seed/ft/group/triage")
MODAL_URL = (
    "https://ledgerline--overmind-inference-{worker}-web.modal.run?model={model}&max_model_len=8192"
)
L4_USD_PER_SECOND = 0.80 / 3600


class Command(BaseCommand):
    help = "Seed the Support Copilot demo project"

    def add_arguments(self, parser):
        parser.add_argument("--owner", default="frey@overmindlab.ai")

    def handle(self, *args, **options):

        random.seed(20260916)

        # Quantised so a re-run within the hour reproduces identical timestamps.
        owner_email = options["owner"]

        def days_ago(d: float, *, h: float = 0.0, m: float = 0.0) -> datetime:
            return NOW - timedelta(days=d, hours=h, minutes=m)

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
            fields = {"created_at": created_at}
            if updated_at is not None and any(
                f.name == "updated_at" for f in obj._meta.get_fields() if hasattr(f, "column")
            ):
                fields["updated_at"] = updated_at
            fields.update(extra_cols)
            type(obj).objects.filter(pk=obj.pk).update(**fields)

        def business_hour(day: datetime) -> datetime:
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

        def daily_volume(base: int, day_index: int, weekday: int) -> int:
            ramp = min(1.0, 0.35 + 0.65 * (day_index / (DAYS * 0.5)))
            week = 0.4 if weekday >= 5 else 1.0
            return max(2, int(base * ramp * week * random.uniform(0.8, 1.2)))

        # ── Reset ────────────────────────────────────────────────────────────────────────

        self.stdout.write("Clearing the previous seed...")

        slugs = (SLUG, *RETIRED_SLUGS)
        doomed_datasets = list(
            Dataset.objects.filter(project__slug__in=slugs).values_list("id", flat=True)
        )
        # Consumers PROTECT the cell they used; they go first so the project cascade is clean.
        # Big tables go first, explicitly: the collector's deferred cascade trips the verdict FK.
        for model in (Verdict, ScoringPass, TaskExecution, Span, Conversation):
            model.objects.filter(project__slug__in=slugs).delete()
        FinetuningJob.objects.filter(project__slug__in=slugs).delete()
        OptimizerExperiment.objects.filter(project__slug__in=slugs).delete()
        EvalRun.objects.filter(project__slug__in=slugs).delete()
        Project.objects.filter(slug__in=slugs).delete()
        for dataset_id in doomed_datasets:
            shutil.rmtree(dataset_paths.dataset_dir(dataset_id), ignore_errors=True)
        User.objects.filter(email__endswith=f"@{RETIRED_DOMAIN}").delete()
        User.objects.filter(email__endswith=f"@{TEAM_DOMAIN}").delete()
        Feedback.objects.filter(user__isnull=True).delete()
        # Ledger rows outlive the project cascade; a re-run must not trip their idempotency keys.
        BillingTelemetry.objects.filter(user__email=owner_email).exclude(
            service=BillingService.FREE_CREDITS
        ).delete()

        upsert_managed_evaluators()

        # ── People and project ───────────────────────────────────────────────────────────

        self.stdout.write("Creating the team and the project...")

        def _user(email, first, last, joined_days_ago, *, password="password"):
            u = User.objects.filter(email=email).first()
            if u is None:
                u = User.objects.create_user(
                    email=email,
                    password=password,
                    first_name=first,
                    last_name=last,
                    timezone="Europe/London",
                    sign_on_method="password",
                    clerk_user_id=f"user_{hashlib.sha256(email.encode()).hexdigest()[:24]}",
                )
                User.objects.filter(pk=u.pk).update(date_joined=days_ago(joined_days_ago))
                BillingTelemetry.objects.filter(user=u, service=BillingService.FREE_CREDITS).update(
                    timestamp=days_ago(joined_days_ago)
                )
            return u

        owner = _user(owner_email, "Frey", "Mehta", DAYS + 4)
        owner.projects_limit = None
        owner.save(update_fields=["projects_limit"])
        amara = _user(f"amara@{TEAM_DOMAIN}", "Amara", "Okafor", DAYS + 2)
        jonas = _user(f"jonas@{TEAM_DOMAIN}", "Jonas", "Weber", DAYS)
        team = [owner, amara, jonas]

        if not UserOnboarding.objects.filter(user=owner).exists():
            ob = UserOnboarding.objects.create(
                user=owner,
                step="done",
                status="completed",
                priorities=["Improve capability accuracy", "Own our models"],
                description="Support copilot in production; no systematic eval or training loop.",
            )
            stamp(ob, owner.date_joined + timedelta(minutes=9))

        project = Project.objects.create(
            name="Support Copilot",
            slug=SLUG,
            integration_type="sdk",
            settings={
                "default_timezone": "UTC",
                "repo_summary": (
                    "Ledgerline's merchant support copilot: a FastAPI service with three "
                    "capabilities (ticket triage, knowledge-base answers, dispute resolution) "
                    "traced with the Overmind SDK."
                ),
                "trace_provider": "sdk",
                "toml_version": SHA[:12],
            },
        )
        stamp(project, days_ago(DAYS + 1), days_ago(0.1))
        for u, born in ((owner, DAYS + 1), (amara, DAYS), (jonas, DAYS - 3)):
            m = ProjectMembership.objects.create(user=u, project=project)
            stamp(m, days_ago(born), days_ago(born))

        raw_keys: list[tuple[str, str]] = []
        for user_, proj, name, made, used in (
            (owner, project, "prod trace exporter", DAYS, 0.01),
            (owner, None, "local CLI", DAYS - 1, 0.6),
            (amara, project, "staging exporter", DAYS - 5, 3.0),
        ):
            raw, tok = APIToken.create_for_user(user_, name=name, project=proj)
            tok.last_used_at = days_ago(used)
            tok.save(update_fields=["last_used_at"])
            stamp(tok, days_ago(made), days_ago(used))
            raw_keys.append((f"{user_.email} / {name}", raw))

        # ── Capabilities, synced the way `overmind sync` lands them ──────────────────────

        self.stdout.write("Syncing capabilities from the local snapshot...")

        def _tool(
            name, purpose, args, *, side_effect="read", returns="", cluster="", integration=""
        ):
            return {
                "name": name,
                "purpose": purpose,
                "args": ", ".join(f"{k}: {t}" for k, t in args.items()),
                "arguments": [{"name": k, "type": t, "required": True} for k, t in args.items()],
                "side_effect": side_effect,
                "returns": returns,
                "cluster": cluster,
                "integration": integration,
                "provenance": ["support/tools.py"],
            }

        def _anchor(qualname, kind, file):
            return {"qualname": qualname, "kind": kind, "file": file}

        snapshot = {
            "project_id": str(project.id),
            "version": SHA[:12],
            "repo_summary": project.settings["repo_summary"],
            "trace_provider": "sdk",
            "capabilities": [
                {
                    "id": str(TRIAGE_ID),
                    "slug": "ticket-triage",
                    "name": "Ticket Triage",
                    "description": (
                        "Classifies inbound support tickets by urgency, category and owning team, "
                        "honouring plan-based SLA floors."
                    ),
                    "entrypoint_fn": "run_triage",
                    "model": "openai/gpt-5.6-sol",
                    "source_path": "support/triage/capability.py",
                    "system_prompt": TRIAGE_PROMPT,
                    "eval_metrics": [],
                    "capability_card": {
                        "task": (
                            "Read an inbound support ticket, look the merchant up, and route the ticket "
                            "to the owning team with an urgency that respects the plan's SLA floor."
                        ),
                        "modality": "text",
                        "domain": "merchant support",
                        "input_schema": {
                            "ticket_text": "The merchant's message, verbatim",
                            "merchant_plan": "starter | growth | enterprise",
                            "previous_tickets": "Open tickets from the same merchant",
                        },
                        "output_fields": {
                            "urgency": "low | medium | high | critical",
                            "category": "The failure surface, e.g. Payouts / Delays",
                            "team": "One of the seven routing teams",
                            "summary": "One line, never quoting card numbers",
                        },
                        "expected_output": {
                            "description": "A routing record the ticketing system applies as-is.",
                            "example": {
                                "urgency": "high",
                                "category": "Payouts / Delays",
                                "team": "oncall-payments",
                                "summary": "Payouts stuck in transit since Monday.",
                            },
                            "quality_signals": [
                                "urgency never below the plan floor",
                                "team is a registered routing team",
                            ],
                        },
                        "tool_spec": [
                            _tool(
                                "lookup_merchant",
                                "Fetch the plan, account age and open incident flags",
                                {"merchant_name": "string"},
                                returns="{plan, account_age_days, open_incidents}",
                                cluster="merchant context",
                                integration="ledger-api",
                            ),
                            _tool(
                                "search_kb",
                                "Retrieve help-centre articles that match the ticket",
                                {"query": "string"},
                                returns="[{slug, title, excerpt}]",
                                cluster="knowledge",
                                integration="search",
                            ),
                            _tool(
                                "route_ticket",
                                "Apply the routing record to the ticket",
                                {"ticket_id": "string", "team": "string", "urgency": "string"},
                                side_effect="write",
                                returns="{ok}",
                                cluster="ticketing",
                                integration="zendesk",
                            ),
                        ],
                        "anchors": [
                            _anchor(
                                "support.triage.run_triage",
                                "entry_point",
                                "support/triage/capability.py#L18-L61",
                            ),
                            _anchor(
                                "support.triage.lookup_merchant", "tool", "support/tools.py#L12-L30"
                            ),
                            _anchor(
                                "support.triage.classify",
                                "function",
                                "support/triage/classify.py#L9-L48",
                            ),
                            _anchor(
                                "support.triage.apply_sla_floor",
                                "function",
                                "support/triage/policy.py#L5-L22",
                            ),
                            _anchor(
                                "support.triage.route_ticket", "tool", "support/tools.py#L33-L47"
                            ),
                            _anchor(
                                "support.triage.escalate",
                                "function",
                                "support/triage/policy.py#L25-L44",
                            ),
                        ],
                        "modes": [],
                        "trajectory_map": [
                            {
                                "id": "classify-and-route",
                                "name": "Classify and route",
                                "claim": "code_path",
                                "verified": True,
                                "routing": "Every ticket without an open incident on the merchant's account.",
                                "anchors": [
                                    "support.triage.run_triage",
                                    "support.triage.lookup_merchant",
                                    "support.triage.classify",
                                    "support.triage.apply_sla_floor",
                                    "support.triage.route_ticket",
                                ],
                                "sequence": [
                                    {
                                        "step": "Look the merchant up",
                                        "kind": "agent_step",
                                        "anchors": ["support.triage.lookup_merchant"],
                                        "may_use": [{"tool": "lookup_merchant", "when": "always"}],
                                    },
                                    {
                                        "step": "Classify the ticket",
                                        "kind": "model_invocation",
                                        "anchors": ["support.triage.classify"],
                                        "input": "ticket text, plan, open incident flags",
                                        "action": "pick the surface, urgency and team",
                                        "output": "routing record as JSON",
                                        "may_use": [
                                            {
                                                "tool": "search_kb",
                                                "when": "the surface is ambiguous",
                                            }
                                        ],
                                    },
                                    {
                                        "step": "Apply the SLA floor",
                                        "kind": "agent_step",
                                        "anchors": ["support.triage.apply_sla_floor"],
                                    },
                                    {
                                        "step": "Route the ticket",
                                        "kind": "agent_step",
                                        "anchors": ["support.triage.route_ticket"],
                                        "may_use": [{"tool": "route_ticket", "when": "always"}],
                                    },
                                ],
                                "tools": ["lookup_merchant", "search_kb", "route_ticket"],
                                "terminal": {
                                    "kind": "emits_record",
                                    "description": "The routing record.",
                                },
                                "divergences": [
                                    "A team outside the registry falls back to support-general."
                                ],
                                "provenance": ["support/triage/capability.py"],
                            },
                            {
                                "id": "escalate-incident",
                                "name": "Escalate an incident",
                                "claim": "code_path",
                                "verified": True,
                                "routing": "lookup_merchant reports an open incident on the account.",
                                "anchors": [
                                    "support.triage.run_triage",
                                    "support.triage.lookup_merchant",
                                    "support.triage.escalate",
                                    "support.triage.route_ticket",
                                ],
                                "sequence": [
                                    {
                                        "step": "Look the merchant up",
                                        "kind": "agent_step",
                                        "anchors": ["support.triage.lookup_merchant"],
                                        "may_use": [{"tool": "lookup_merchant", "when": "always"}],
                                    },
                                    {
                                        "step": "Escalate to on-call",
                                        "kind": "agent_step",
                                        "anchors": ["support.triage.escalate"],
                                    },
                                    {
                                        "step": "Route the ticket",
                                        "kind": "agent_step",
                                        "anchors": ["support.triage.route_ticket"],
                                        "may_use": [{"tool": "route_ticket", "when": "always"}],
                                    },
                                ],
                                "tools": ["lookup_merchant", "route_ticket"],
                                "terminal": {
                                    "kind": "escalates",
                                    "description": "Critical routing to on-call.",
                                },
                                "divergences": [],
                                "provenance": ["support/triage/policy.py"],
                            },
                        ],
                        "success_criteria": [
                            "urgency matches the golden label",
                            "team is exactly the golden team",
                            "enterprise merchants are never routed below medium",
                        ],
                        "failure_modes": [
                            "sentiment read as urgency",
                            "payout disruption routed to support-general",
                            "card number quoted in the summary",
                        ],
                        "vocabulary": {
                            "SLA floor": "the minimum urgency a plan guarantees",
                            "surface": "the product area the ticket is about",
                        },
                        "provenance": {
                            "paths": ["support/triage/capability.py", "support/triage/policy.py"]
                        },
                    },
                },
                {
                    "id": str(KB_ID),
                    "slug": "kb-answerer",
                    "name": "KB Answerer",
                    "description": "Answers how-to questions from the help centre with citations.",
                    "entrypoint_fn": "answer_question",
                    "model": "anthropic/claude-sonnet-5",
                    "source_path": "support/kb/capability.py",
                    "system_prompt": KB_PROMPT,
                    "eval_metrics": [],
                    "capability_card": {
                        "task": (
                            "Answer a merchant's how-to question strictly from retrieved help-centre "
                            "articles, citing every claim by article slug."
                        ),
                        "modality": "text",
                        "domain": "merchant support",
                        "input_schema": {
                            "question": "The merchant's question",
                            "merchant_plan": "starter | growth | enterprise",
                        },
                        "output_fields": {
                            "answer": "Grounded answer with inline citations",
                            "citations": "Article slugs the answer relies on",
                            "confidence": "0 to 1; below 0.3 when the articles do not answer",
                        },
                        "expected_output": {
                            "description": "A cited answer, or a clear statement that the help centre has none.",
                            "example": {
                                "answer": "Payouts settle in two business days [payouts-schedule].",
                                "citations": ["payouts-schedule"],
                                "confidence": 0.91,
                            },
                            "quality_signals": [
                                "every claim cited",
                                "no content beyond the articles",
                            ],
                        },
                        "tool_spec": [
                            _tool(
                                "search_kb",
                                "Hybrid retrieval over the help centre",
                                {"query": "string"},
                                returns="[{slug, title, excerpt}]",
                                cluster="knowledge",
                                integration="search",
                            ),
                            _tool(
                                "fetch_article",
                                "Full article text by slug",
                                {"slug": "string"},
                                returns="{slug, title, body}",
                                cluster="knowledge",
                                integration="search",
                            ),
                        ],
                        "anchors": [
                            _anchor(
                                "support.kb.answer_question",
                                "entry_point",
                                "support/kb/capability.py#L14-L52",
                            ),
                            _anchor(
                                "support.kb.search_kb", "retrieval", "support/tools.py#L50-L68"
                            ),
                            _anchor(
                                "support.kb.fetch_article", "retrieval", "support/tools.py#L71-L82"
                            ),
                            _anchor(
                                "support.kb.compose_answer",
                                "function",
                                "support/kb/compose.py#L7-L41",
                            ),
                            _anchor(
                                "support.kb.decline", "function", "support/kb/compose.py#L44-L55"
                            ),
                        ],
                        "modes": [],
                        "trajectory_map": [
                            {
                                "id": "grounded-answer",
                                "name": "Grounded answer",
                                "claim": "code_path",
                                "verified": True,
                                "routing": "Retrieval returns at least one article above the score floor.",
                                "anchors": [
                                    "support.kb.answer_question",
                                    "support.kb.search_kb",
                                    "support.kb.fetch_article",
                                    "support.kb.compose_answer",
                                ],
                                "sequence": [
                                    {
                                        "step": "Retrieve articles",
                                        "kind": "agent_step",
                                        "anchors": [
                                            "support.kb.search_kb",
                                            "support.kb.fetch_article",
                                        ],
                                        "may_use": [
                                            {"tool": "search_kb", "when": "always"},
                                            {
                                                "tool": "fetch_article",
                                                "when": "a hit needs its full body",
                                            },
                                        ],
                                    },
                                    {
                                        "step": "Compose the cited answer",
                                        "kind": "model_invocation",
                                        "anchors": ["support.kb.compose_answer"],
                                        "input": "question plus retrieved article bodies",
                                        "action": "answer only from the articles, cite each claim",
                                        "output": "answer, citations, confidence",
                                    },
                                ],
                                "tools": ["search_kb", "fetch_article"],
                                "terminal": {
                                    "kind": "emits_record",
                                    "description": "The cited answer.",
                                },
                                "divergences": [],
                                "provenance": ["support/kb/capability.py"],
                            },
                            {
                                "id": "no-answer",
                                "name": "Decline without sources",
                                "claim": "code_path",
                                "verified": True,
                                "routing": "Retrieval returns nothing above the score floor.",
                                "anchors": [
                                    "support.kb.answer_question",
                                    "support.kb.search_kb",
                                    "support.kb.decline",
                                ],
                                "sequence": [
                                    {
                                        "step": "Retrieve articles",
                                        "kind": "agent_step",
                                        "anchors": ["support.kb.search_kb"],
                                        "may_use": [{"tool": "search_kb", "when": "always"}],
                                    },
                                    {
                                        "step": "Say the help centre has no answer",
                                        "kind": "agent_step",
                                        "anchors": ["support.kb.decline"],
                                    },
                                ],
                                "tools": ["search_kb"],
                                "terminal": {
                                    "kind": "returns_empty",
                                    "description": "Low-confidence decline.",
                                },
                                "divergences": [],
                                "provenance": ["support/kb/compose.py"],
                            },
                        ],
                        "success_criteria": [
                            "every claim is supported by a cited article",
                            "no fabricated fees or timelines",
                        ],
                        "failure_modes": [
                            "a claim cites an article that does not contain it",
                            "answering when retrieval was empty",
                        ],
                        "vocabulary": {"slug": "the help-centre article identifier"},
                        "provenance": {
                            "paths": ["support/kb/capability.py", "support/kb/compose.py"]
                        },
                    },
                },
                {
                    "id": str(DISPUTE_ID),
                    "slug": "dispute-resolver",
                    "name": "Dispute Resolver",
                    "description": (
                        "Drafts chargeback resolutions from ledger evidence under the representment policy."
                    ),
                    "entrypoint_fn": "resolve_dispute",
                    "model": "anthropic/claude-sonnet-5",
                    "source_path": "support/disputes/capability.py",
                    "system_prompt": DISPUTE_PROMPT,
                    "eval_metrics": [],
                    "capability_card": {
                        "task": (
                            "Gather the ledger row and the evidence on file for a dispute, apply the "
                            "reason-code policy, and draft the merchant's reply."
                        ),
                        "modality": "text",
                        "domain": "payments",
                        "input_schema": {
                            "dispute_id": "Ledger dispute id",
                            "reason_code": "Card-network reason code",
                            "amount": "Disputed amount in USD",
                            "merchant_note": "Optional free text from the merchant",
                        },
                        "output_fields": {
                            "resolution": "accept | represent | request_evidence",
                            "draft_reply": "The reply sent in the merchant's voice",
                            "evidence_used": "Evidence classes the decision relies on",
                        },
                        "expected_output": {
                            "description": "A policy-compliant resolution with its evidence trail.",
                            "example": {
                                "resolution": "represent",
                                "draft_reply": "The delivery confirmation and AVS match support the charge.",
                                "evidence_used": ["delivery_confirmation", "avs_match"],
                            },
                            "quality_signals": [
                                "two evidence classes when representing",
                                "no timeline promises",
                            ],
                        },
                        "tool_spec": [
                            _tool(
                                "lookup_transaction",
                                "The ledger row for a dispute",
                                {"dispute_id": "string"},
                                returns="{amount, card_last4, settled_at}",
                                cluster="ledger",
                                integration="ledger-api",
                            ),
                            _tool(
                                "fetch_dispute_evidence",
                                "Evidence on file: delivery, AVS/CVV, comms",
                                {"dispute_id": "string"},
                                returns="[evidence_class]",
                                cluster="ledger",
                                integration="ledger-api",
                            ),
                            _tool(
                                "check_policy",
                                "Threshold rules by reason code",
                                {"reason_code": "string", "amount": "number"},
                                returns="{fight, min_evidence}",
                                cluster="policy",
                            ),
                            _tool(
                                "send_reply",
                                "Send the draft to the network",
                                {"dispute_id": "string", "body": "string"},
                                side_effect="write",
                                returns="{ok}",
                                cluster="network",
                                integration="visa-vrol",
                            ),
                        ],
                        "anchors": [
                            _anchor(
                                "support.disputes.resolve_dispute",
                                "entry_point",
                                "support/disputes/capability.py#L21-L74",
                            ),
                            _anchor(
                                "support.disputes.lookup_transaction",
                                "tool",
                                "support/tools.py#L90-L104",
                            ),
                            _anchor(
                                "support.disputes.fetch_dispute_evidence",
                                "tool",
                                "support/tools.py#L107-L126",
                            ),
                            _anchor(
                                "support.disputes.check_policy",
                                "tool",
                                "support/disputes/policy.py#L8-L37",
                            ),
                            _anchor(
                                "support.disputes.draft_reply",
                                "function",
                                "support/disputes/draft.py#L6-L39",
                            ),
                            _anchor(
                                "support.disputes.request_evidence",
                                "function",
                                "support/disputes/draft.py#L42-L58",
                            ),
                        ],
                        "modes": [],
                        "trajectory_map": [
                            {
                                "id": "resolve-dispute",
                                "name": "Resolve a dispute",
                                "claim": "code_path",
                                "verified": True,
                                "routing": "Evidence on file meets the reason code's minimum.",
                                "anchors": [
                                    "support.disputes.resolve_dispute",
                                    "support.disputes.lookup_transaction",
                                    "support.disputes.fetch_dispute_evidence",
                                    "support.disputes.check_policy",
                                    "support.disputes.draft_reply",
                                ],
                                "sequence": [
                                    {
                                        "step": "Read the ledger row",
                                        "kind": "agent_step",
                                        "anchors": ["support.disputes.lookup_transaction"],
                                        "may_use": [
                                            {"tool": "lookup_transaction", "when": "always"}
                                        ],
                                    },
                                    {
                                        "step": "Collect the evidence",
                                        "kind": "agent_step",
                                        "anchors": ["support.disputes.fetch_dispute_evidence"],
                                        "may_use": [
                                            {"tool": "fetch_dispute_evidence", "when": "always"}
                                        ],
                                    },
                                    {
                                        "step": "Check the policy",
                                        "kind": "agent_step",
                                        "anchors": ["support.disputes.check_policy"],
                                        "may_use": [{"tool": "check_policy", "when": "always"}],
                                    },
                                    {
                                        "step": "Draft the reply",
                                        "kind": "model_invocation",
                                        "anchors": ["support.disputes.draft_reply"],
                                        "input": "ledger row, evidence classes, policy verdict",
                                        "action": "decide and write the reply in the merchant's voice",
                                        "output": "resolution, draft_reply, evidence_used",
                                        "may_use": [
                                            {
                                                "tool": "send_reply",
                                                "when": "the merchant enabled auto-send",
                                            }
                                        ],
                                    },
                                ],
                                "tools": [
                                    "lookup_transaction",
                                    "fetch_dispute_evidence",
                                    "check_policy",
                                    "send_reply",
                                ],
                                "terminal": {
                                    "kind": "emits_record",
                                    "description": "The resolution record.",
                                },
                                "divergences": [
                                    "Amounts over $5,000 always require the policy check before accepting."
                                ],
                                "provenance": ["support/disputes/capability.py"],
                            },
                            {
                                "id": "request-evidence",
                                "name": "Request more evidence",
                                "claim": "code_path",
                                "verified": True,
                                "routing": "Fewer evidence classes than the reason code requires.",
                                "anchors": [
                                    "support.disputes.resolve_dispute",
                                    "support.disputes.lookup_transaction",
                                    "support.disputes.fetch_dispute_evidence",
                                    "support.disputes.request_evidence",
                                ],
                                "sequence": [
                                    {
                                        "step": "Read the ledger row",
                                        "kind": "agent_step",
                                        "anchors": ["support.disputes.lookup_transaction"],
                                        "may_use": [
                                            {"tool": "lookup_transaction", "when": "always"}
                                        ],
                                    },
                                    {
                                        "step": "Collect the evidence",
                                        "kind": "agent_step",
                                        "anchors": ["support.disputes.fetch_dispute_evidence"],
                                        "may_use": [
                                            {"tool": "fetch_dispute_evidence", "when": "always"}
                                        ],
                                    },
                                    {
                                        "step": "Ask the merchant for what is missing",
                                        "kind": "agent_step",
                                        "anchors": ["support.disputes.request_evidence"],
                                    },
                                ],
                                "tools": ["lookup_transaction", "fetch_dispute_evidence"],
                                "terminal": {
                                    "kind": "returns_empty",
                                    "description": "A request, not a resolution.",
                                },
                                "divergences": [],
                                "provenance": ["support/disputes/draft.py"],
                            },
                        ],
                        "success_criteria": [
                            "represent only with two evidence classes",
                            "accept friendly fraud under $25",
                            "never promise a refund timeline",
                        ],
                        "failure_modes": [
                            "representment on one evidence class",
                            "a timeline promised in the draft",
                        ],
                        "vocabulary": {"representment": "contesting the chargeback with evidence"},
                        "provenance": {
                            "paths": [
                                "support/disputes/capability.py",
                                "support/disputes/policy.py",
                            ]
                        },
                    },
                },
            ],
        }

        # A real sync fires the eval preload (LLM calls) and a rebind task; the seed writes both
        # outcomes itself.
        sync_service.enqueue_capability_eval_preload_on_commit = lambda cap: None
        capability_identity.enqueue_rebind = lambda project_id: None
        post_save.disconnect(sync_evaluators_on_card_change, sender=Capability)
        eval_tasks.sync_card_evaluators_task.delay = lambda **kwargs: None
        eval_tasks.preload_capability_eval_set.delay = lambda **kwargs: None
        sync_service.apply_snapshot(project, snapshot)

        triage_capability = Capability.objects.get(pk=TRIAGE_ID)
        kb_capability = Capability.objects.get(pk=KB_ID)
        dispute_capability = Capability.objects.get(pk=DISPUTE_ID)
        capabilities = [triage_capability, kb_capability, dispute_capability]
        for cap in capabilities:
            Capability.objects.filter(pk=cap.pk).update(
                cli_version="0.1.55",
                analyzer_model="anthropic/claude-sonnet-5",
                created_at=days_ago(DAYS),
                updated_at=days_ago(0.2),
            )
            Behaviour.objects.filter(capability=cap).update(
                first_seen_sha=SHA,
                last_seen_sha=SHA,
                created_at=days_ago(DAYS),
                updated_at=days_ago(DAYS),
            )

        # A purpose the previous sync saw and this one did not.
        legacy_router = Capability.objects.create(
            project=project,
            name="Legacy Ticket Router",
            slug="legacy-ticket-router",
            description="Routed tickets by keyword before the classifier existed.",
            source_path="support/legacy/router.py",
            entrypoint_fn="route",
            status=Capability.Status.LEFTOVER,
            cli_version="0.1.49",
        )
        stamp(legacy_router, days_ago(DAYS + 1), days_ago(DAYS - 2))

        def behaviour(cap, key) -> Behaviour:
            return Behaviour.objects.get(capability=cap, key=key)

        t_classify = behaviour(triage_capability, "classify-and-route")
        t_escalate = behaviour(triage_capability, "escalate-incident")
        k_answer = behaviour(kb_capability, "grounded-answer")
        k_decline = behaviour(kb_capability, "no-answer")
        d_resolve = behaviour(dispute_capability, "resolve-dispute")
        d_request = behaviour(dispute_capability, "request-evidence")
        for b in (t_classify, t_escalate, k_answer, k_decline, d_resolve, d_request):
            b.version = b.versions.order_by("-created_at").first()

        # ── Evaluators and eval sets ─────────────────────────────────────────────────────

        self.stdout.write("Creating evaluators and eval sets...")

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
            lines = [ln.strip() for ln in (rubric_md or "").splitlines()]
            items = [
                re.sub(r"^[-*]\s*|\*\*", "", ln).strip()
                for ln in lines
                if ln.startswith(("-", "*"))
            ]
            if not items:
                items = [
                    t.strip() for t in re.split(r"(?<=[.!?])\s+", rubric_md or "") if t.strip()
                ]
            items = [t.rstrip(".") for t in items if t][:12] or [
                "Does the output satisfy the rubric?"
            ]
            return [{"id": f"q{i + 1}", "q": q, "weight": 1.0} for i, q in enumerate(items)]

        def _binding(b: Behaviour, role="outcome", segment=()):
            return {
                "behaviour": {
                    "behaviour_key": b.key,
                    "behaviour_id": str(b.id),
                    "role": role,
                    "anchor_segment": list(segment),
                }
            }

        def _evaluator(name, kind, born_days, *, capability=None, config=None, **kw):
            if kind == "llm_judge" and not kw.get("checklist"):
                kw["checklist"] = _checklist(kw.get("rubric_md", ""))
            ev = Evaluator.objects.create(
                project=project,
                capability=capability,
                name=name,
                kind=kind,
                config=config or {},
                **kw,
            )
            if not ev.spec_data:
                from overbae.services.eval.specs import derive_spec_data

                ev.spec_data = derive_spec_data(ev)  # raises with the column that is wrong
                ev.save()
            stamp(ev, days_ago(born_days), days_ago(born_days))
            return ev

        triage_accuracy = _evaluator(
            "Triage Accuracy",
            "llm_judge",
            DAYS - 1,
            capability=triage_capability,
            config=_binding(t_classify),
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
            requires_reference=True,
            created_by=amara,
        )
        routing_valid = _evaluator(
            "Valid Routing Team",
            "deterministic",
            DAYS - 1,
            capability=triage_capability,
            config={
                "check": "regex",
                "pattern": r'"team":\s*"(oncall-payments|oncall-platform|billing-support|support-general|risk-ops|integrations|product)"',
                **_binding(t_classify, "step", ["support.triage.route_ticket"]),
            },
            description="The team field is one of the registered routing teams.",
            score_type="boolean",
            pass_threshold=1.0,
            created_by=amara,
        )
        sla_floor = _evaluator(
            "SLA Floor Respected",
            "llm_judge",
            DAYS - 2,
            capability=triage_capability,
            config=_binding(t_escalate),
            description="Enterprise merchants and open incidents are never routed below medium.",
            rubric_md="Fail when an enterprise merchant, or a merchant with an open incident, is routed low.",
            judge_model=JUDGE,
            score_type="boolean",
            pass_threshold=1.0,
            checklist=[
                {
                    "id": "floor",
                    "q": "Is the urgency at or above the plan floor?",
                    "weight": 1.0,
                    "gate": True,
                }
            ],
            created_by=amara,
        )
        citation_support = _evaluator(
            "Citation Support",
            "llm_judge",
            DAYS - 2,
            capability=kb_capability,
            config=_binding(k_answer),
            description="Every factual claim in the answer is supported by a cited article.",
            rubric_md="Fail if any stated fact lacks a citation or cites an article that does not contain it.",
            judge_model=JUDGE,
            score_type="boolean",
            pass_threshold=1.0,
            checklist=[
                {
                    "id": "grounded",
                    "q": "Is every claim supported by a cited article?",
                    "weight": 1.0,
                    "gate": True,
                }
            ],
            created_by=amara,
        )
        tone_empathy = _evaluator(
            "Tone & Empathy",
            "llm_judge",
            DAYS - 3,
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
                    "q": "Does the reply acknowledge the situation before the fix?",
                    "weight": 0.35,
                    "gate": False,
                },
                {
                    "id": "professional_tone",
                    "q": "Is the tone professional and free of curtness?",
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
                    "q": "Is the empathy specific rather than boilerplate?",
                    "weight": 0.1,
                    "gate": False,
                },
            ],
            created_by=amara,
        )
        policy_compliance = _evaluator(
            "Resolution Policy Compliance",
            "llm_judge",
            DAYS - 4,
            capability=dispute_capability,
            config=_binding(d_resolve),
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
                {
                    "id": "no-promises",
                    "q": "No refund-timeline promises?",
                    "weight": 0.3,
                    "gate": True,
                },
            ],
            created_by=owner,
        )
        tool_sequence = _evaluator(
            "Tool Sequence Accuracy",
            "trajectory",
            DAYS - 4,
            capability=dispute_capability,
            config={
                "mode": "match",
                "reference_source": "expected",
                **_binding(
                    d_resolve,
                    "step",
                    [
                        "support.disputes.lookup_transaction",
                        "support.disputes.fetch_dispute_evidence",
                        "support.disputes.check_policy",
                    ],
                ),
            },
            description="Ledger lookup and evidence fetch happen before the policy check.",
            score_type="boolean",
            pass_threshold=1.0,
            created_by=owner,
        )
        evidence_request_clear = _evaluator(
            "Evidence Request Clarity",
            "llm_judge",
            DAYS - 5,
            capability=dispute_capability,
            config=_binding(d_request),
            description="A request for evidence names exactly what is missing.",
            rubric_md="The request lists the missing evidence classes by name and nothing else.",
            judge_model=JUDGE,
            score_type="numeric",
            created_by=owner,
        )

        def _eval_set(cap, name, born_days, members, *, trace=(), created_by=None):
            es = EvalSet.objects.create(
                project=project,
                capability=cap,
                name=name,
                description=f"Grading rubric for {cap.name}.",
                created_by=created_by,
            )
            stamp(es, days_ago(born_days), days_ago(born_days))
            order = 0
            member_rows, trace_rows = {}, {}
            for ev in members:
                m = EvalSetMember.objects.create(
                    eval_set=es, evaluator=ev, role="generative", enabled=True, order=order
                )
                stamp(m, days_ago(born_days))
                member_rows[ev.name] = m
                order += 1
            for ev in trace:
                m = EvalSetMember.objects.create(
                    eval_set=es, evaluator=ev, role="trace_scoring", enabled=True, order=order
                )
                stamp(m, days_ago(born_days))
                trace_rows[ev.name] = m
                order += 1
            cap.active_eval_set = es
            cap.save(update_fields=["active_eval_set"])
            return es, member_rows, trace_rows

        triage_set, triage_members, triage_trace = _eval_set(
            triage_capability,
            "Triage rubric",
            DAYS - 1,
            [
                triage_accuracy,
                routing_valid,
                sla_floor,
                managed("Correctness"),
                managed("Conciseness"),
            ],
            trace=(triage_accuracy, routing_valid, sla_floor),
            created_by=amara,
        )
        kb_set, kb_members, kb_trace = _eval_set(
            kb_capability,
            "KB answer quality",
            DAYS - 2,
            [citation_support, managed("Faithfulness"), tone_empathy],
            trace=(citation_support, managed("Faithfulness")),
            created_by=amara,
        )
        dispute_set, dispute_members, dispute_trace = _eval_set(
            dispute_capability,
            "Dispute resolution",
            DAYS - 4,
            [policy_compliance, tool_sequence, evidence_request_clear, managed("Correctness")],
            trace=(policy_compliance, tool_sequence, evidence_request_clear),
            created_by=owner,
        )

        # ── Connectors ───────────────────────────────────────────────────────────────────

        langfuse_cred = ConnectorCredential.objects.create(
            project=project,
            name="Legacy Langfuse project",
            connector_type="langfuse",
            base_url="https://cloud.langfuse.com",
            api_key="pk-lf-" + hexid(12),
            api_secret="sk-lf-" + hexid(12),
            is_active=True,
            auto_sync_enabled=False,
            last_synced_at=days_ago(6),
            sync_status="live",
            sync_cursor={"mode": "live", "page": 1, "watermark": days_ago(6).isoformat()},
            backfill_imported=412,
            backfill_total=412,
        )
        stamp(langfuse_cred, days_ago(DAYS - 6), days_ago(6))

        # ── Traces ───────────────────────────────────────────────────────────────────────

        self.stdout.write("Generating thirty days of traces...")

        MODEL_RATES[TRIAGE_MODEL_ID] = (0.04, 0.10)
        MODEL_RATES[DISPUTE_MODEL_ID] = (0.08, 0.25)

        def llm_cost(model, pt, ct):
            rin, rout = MODEL_RATES.get(model, (1.0, 4.0))
            return round((pt * rin + ct * rout) / 1_000_000, 6)

        def wplan():
            r = random.random()
            return "starter" if r < 0.45 else ("growth" if r < 0.83 else "enterprise")

        span_buf: list[Span] = []
        verdict_buf: list[Verdict] = []
        verdict_times: list[datetime] = []
        execution_buf: list[TaskExecution] = []
        pass_buf: list[ScoringPass] = []
        usage_acc = {
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
        member_ids: dict = {}

        def _identifier(member):
            key = str(member.pk)
            if key not in member_ids:
                member_ids[key] = eval_dispatch.member_identifier(member, member.evaluator.spec)
            return member_ids[key]

        def _flush():
            if span_buf:
                for sp in span_buf:
                    sp.usage = usage_slice(sp.attributes)
                Span.objects.bulk_create(span_buf, batch_size=1000)
                # The scoring sweep re-drives any trace received in the last two hours
                # whose pass started before its root landed; land them at trace time.
                backdate(
                    Span,
                    [
                        (
                            sp.pk,
                            datetime.fromtimestamp(sp.end_time_ns / 1e9, tz=UTC)
                            + timedelta(seconds=2),
                        )
                        for sp in span_buf
                    ],
                    "received_at",
                )
                span_buf.clear()
            if verdict_buf:
                Verdict.objects.bulk_create(verdict_buf, batch_size=1000)
                pairs = list(zip((v.pk for v in verdict_buf), verdict_times, strict=True))
                backdate(Verdict, pairs)
                backdate(Verdict, pairs, "updated_at")
                verdict_buf.clear()
                verdict_times.clear()
            if execution_buf:
                TaskExecution.objects.bulk_create(execution_buf, batch_size=1000)
                backdate(TaskExecution, [(te.pk, te.started_at) for te in execution_buf])
                backdate(
                    TaskExecution, [(te.pk, te.started_at) for te in execution_buf], "updated_at"
                )
                execution_buf.clear()
            if pass_buf:
                ScoringPass.objects.bulk_create(pass_buf, batch_size=1000)
                backdate(
                    ScoringPass,
                    [(p.pk, p.finished - timedelta(seconds=40)) for p in pass_buf],
                    "started",
                )
                pass_buf.clear()

        def _acc(cap, span_id, model=None, pt=0, ct=0, cost=0.0, tool=False, end=None):
            u = usage_acc[cap.pk]
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

        def _res_attrs(cap):
            return {
                "service.name": cap.slug,
                "deployment.environment": "production",
                "telemetry.sdk.name": "overmind",
                "telemetry.sdk.language": "python",
                "vcs.ref.head.revision": SHA,
                "overmind.capability.id": str(cap.pk),
                "overmind.project.id": str(project.pk),
            }

        def _feedback_entries(entries, scored_at):
            block = {}
            for name, member, value, passed, rationale in entries:
                block[name] = {
                    "score": value,
                    "passed": passed,
                    "outcome": "scored",
                    "rationale": rationale,
                    "scope": "trace",
                    "grain": member.evaluator.spec.claim.grain,
                    "gate": member.evaluator.spec.claim.type in ("conformance", "verification"),
                    "sub_scores": [],
                    "eval_set_member_id": str(member.pk),
                    "evaluator_id": str(member.evaluator_id),
                    "scored_at": scored_at.isoformat(),
                }
            composed = composition.compose(block)
            if composed:
                block["_execution"] = composed
            block["_scored_at"] = scored_at.isoformat()
            return {"trace_scoring": block}

        def _verdicts(cap, root_id, entries, scored_at):
            total_cost = 0.0
            for name, member, value, passed, rationale in entries:
                ev = member.evaluator
                code = ev.kind in ("deterministic", "statistical")
                cost = 0.0 if code else rnd(0.0003, 0.0014, 6)
                total_cost += cost
                verdict_buf.append(
                    Verdict(
                        project=project,
                        evaluator=ev,
                        evaluator_name=name,
                        target_kind=Verdict.TargetKind.SPAN,
                        target_id=root_id,
                        label="",
                        score=value,
                        outcome=Verdict.Outcome.SCORED,
                        explanation=rationale,
                        unmet=[],
                        metadata=eval_dispatch.verdict_metadata(
                            passed=passed,
                            scope="trace",
                            grain=ev.spec.claim.grain,
                            gate=ev.spec.claim.type in ("conformance", "verification"),
                            sub_scores=[],
                        ),
                        annotator_kind=Verdict.AnnotatorKind.CODE
                        if code
                        else Verdict.AnnotatorKind.LLM,
                        identifier=_identifier(member),
                        judge_trace_id="" if code else hexid(16),
                        cost=cost,
                        input_tokens=None if code else random.randint(900, 2600),
                        output_tokens=None if code else random.randint(60, 220),
                        latency_ms=random.randint(2, 9) if code else random.randint(600, 2400),
                    )
                )
                verdict_times.append(scored_at)
            return total_cost

        _bound_cache: dict = {}

        def _bound_evaluators(cap):
            if cap.pk not in _bound_cache:
                _bound_cache[cap.pk] = list(
                    Evaluator.objects.filter(
                        capability=cap, is_archived=False, config__has_key="behaviour"
                    )
                )
            return _bound_cache[cap.pk]

        def _execution(
            cap,
            b,
            trace_id,
            root_id,
            start_ns,
            end_ns,
            *,
            inp,
            feedback,
            conv_ext,
            error,
            declared,
            anchors_seen,
        ):
            composed = ((feedback or {}).get("trace_scoring") or {}).get("_execution") or {}
            block = (feedback or {}).get("trace_scoring") or {}
            contract = b.version.contract if b is not None and b.version else {}
            sequence = list(contract.get("anchor_sequence") or [])
            matched = [a for a in sequence if a in anchors_seen]
            step_results = []
            if b is not None:
                for ev in _bound_evaluators(cap):
                    binding = (ev.config or {}).get("behaviour") or {}
                    if binding.get("behaviour_key") != b.key:
                        continue
                    entry = block.get(ev.name)
                    role = binding.get("role") or "outcome"
                    if not isinstance(entry, dict):
                        step_results.append(
                            {
                                "evaluator": ev.name,
                                "display_name": "",
                                "role": role,
                                "segment": binding.get("anchor_segment") or [],
                                "outcome": "unscored",
                            }
                        )
                        continue
                    result = {
                        "evaluator": ev.name,
                        "display_name": "",
                        "role": role,
                        "segment": binding.get("anchor_segment") or [],
                        "score": entry.get("score"),
                        "passed": entry.get("passed"),
                        "outcome": "scored",
                        "rationale": entry.get("rationale") or "",
                    }
                    if role == "outcome":
                        result["delivery"] = "delivered" if not error else "failed"
                    step_results.append(result)
            execution_buf.append(
                TaskExecution(
                    project=project,
                    capability=cap,
                    behaviour=b,
                    behaviour_version=b.version if b is not None else None,
                    trace_id=trace_id,
                    unit_span_id=root_id,
                    conversation_id=conv_ext or "",
                    binding_source="declared"
                    if declared
                    else ("anchor_join" if b is not None else "unbound"),
                    observed_route={
                        "sha": SHA,
                        "entry_qualname": sequence[0] if sequence else "",
                        "anchors": anchors_seen,
                        "ancestors": [],
                        "matched_anchors": matched,
                        "unknown_anchors": [],
                        "terminal": "error_exit"
                        if error
                        else (contract.get("terminal") or {}).get("kind") or "emits_record",
                        "contract_sha": SHA,
                        "alignment": {
                            "completion": round(len(matched) / len(sequence), 2)
                            if sequence
                            else 1.0,
                            "order_ok": True,
                            "terminal_match": not error,
                        },
                    },
                    user_intent={
                        "text": str(next(iter(inp.values()), ""))[:200],
                        "source": "first_message",
                    }
                    if isinstance(inp, dict) and inp
                    else {},
                    step_results=step_results,
                    success_score=composed.get("score"),
                    route_flags=["unusual_route_good_outcome"]
                    if (
                        not error
                        and len(matched) < len(sequence)
                        and composed.get("score", 0)
                        and composed["score"] > 0.8
                    )
                    else [],
                    terminal_kind="error_exit"
                    if error
                    else (contract.get("terminal") or {}).get("kind") or "emits_record",
                    status="error" if error else "completed",
                    started_at=datetime.fromtimestamp(start_ns / 1e9, tz=UTC),
                    duration_ms=(end_ns - start_ns) // 1_000_000,
                )
            )

        def emit_trace(
            cap,
            t0,
            inp,
            out,
            *,
            steps,
            task,
            anchors,
            error=None,
            conversation=None,
            conv_ext="",
            entries=None,
            declared=True,
        ):
            """One trace: an entry_point root with LLM/tool children per ``steps``.

            steps: list of ("llm", model, pt, ct, ms) | ("tool", name, args, ms, err?).
            entries: [(evaluator_name, member, score, passed, rationale)] — the live verdicts.
            """
            if t0 > NOW - timedelta(hours=3):
                t0 = NOW - timedelta(hours=3, minutes=random.randint(5, 110))
            trace_id = hexid(16)
            root_id = hexid(8)
            start_ns = int(t0.timestamp() * 1_000_000_000)
            offset_ns = 0
            model_pending = True
            for step in steps:
                if step[0] == "llm":
                    _, smodel, pt, ct, ms = step
                    s_start = start_ns + offset_ns
                    s_end = s_start + int(ms * 1e6)
                    cost = llm_cost(smodel, pt, ct)
                    sid = hexid(8)
                    attrs = {
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
                    }
                    if model_pending:
                        attrs["overmind.input.data"] = [
                            {
                                "role": "system",
                                "content": (cap.improvement_metadata or {}).get(
                                    "system_prompt", ""
                                ),
                            },
                            {"role": "user", "content": json.dumps(inp)},
                        ]
                        attrs["overmind.output.data"] = [
                            {"role": "assistant", "content": json.dumps(out)}
                        ]
                        model_pending = False
                    span_buf.append(
                        Span(
                            span_id=sid,
                            trace_id=trace_id,
                            parent_span_id=root_id,
                            project=project,
                            capability=cap,
                            conversation=conversation,
                            span_type="llm_call",
                            operation="llm.chat",
                            name=f"chat {smodel}",
                            kind=3,
                            start_time_ns=s_start,
                            end_time_ns=s_end,
                            duration_ns=s_end - s_start,
                            status_code=1,
                            service_name=cap.slug,
                            resource_attrs=_res_attrs(cap),
                            scope_name="overmind.sdk",
                            scope_version="0.1.55",
                            feedback_score={},
                            attributes=attrs,
                        )
                    )
                    _acc(cap, sid, model=smodel, pt=pt, ct=ct, cost=cost)
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
                            project=project,
                            capability=cap,
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
                            service_name=cap.slug,
                            resource_attrs=_res_attrs(cap),
                            scope_name="overmind.sdk",
                            scope_version="0.1.55",
                            feedback_score={},
                            attributes={
                                "tool.name": tname,
                                "tool.arg_keys": sorted(targs),
                                "overmind.input.data": targs,
                                "overmind.provenance": "environment",
                                **({"tool.error": terr} if terr else {}),
                                **({"conversation.id": conv_ext} if conv_ext else {}),
                            },
                        )
                    )
                    _acc(cap, sid, tool=True)
                    offset_ns += int(ms * 1e6)
            end_ns = start_ns + offset_ns + int(rnd(20, 90) * 1e6)
            end_dt = datetime.fromtimestamp(end_ns / 1e9, tz=UTC)
            scored_at = end_dt + timedelta(minutes=random.randint(2, 7))
            feedback = (
                _feedback_entries(entries, scored_at)
                if entries
                else {"trace_scoring": {"_scored_at": scored_at.isoformat()}}
            )
            root_attrs = {
                "overmind.input.data": inp,
                "overmind.span.type": "entry_point",
                "overmind.unit_kind": "run",
                "code.namespace": anchors[0].rsplit(".", 1)[0],
                "code.function.name": anchors[0].rsplit(".", 1)[1],
                **({"overmind.behaviour.key": task.key} if task is not None and declared else {}),
                **({"conversation.id": conv_ext} if conv_ext else {}),
            }
            if error:
                root_attrs["overmind.error.type"] = "AgentExecutionError"
                root_attrs["overmind.error.message"] = error
                root_attrs["overmind.status"] = "failed"
            else:
                root_attrs["overmind.output.data"] = out
                root_attrs["overmind.delivery"] = "true"
                root_attrs["overmind.status"] = "success"
            span_buf.append(
                Span(
                    span_id=root_id,
                    trace_id=trace_id,
                    parent_span_id=None,
                    project=project,
                    capability=cap,
                    conversation=conversation,
                    span_type="entry_point",
                    operation=f"{cap.slug}.run",
                    name=anchors[0].rsplit(".", 1)[1],
                    kind=2,
                    start_time_ns=start_ns,
                    end_time_ns=end_ns,
                    duration_ns=end_ns - start_ns,
                    status_code=2 if error else 1,
                    status_message=error or "",
                    service_name=cap.slug,
                    resource_attrs=_res_attrs(cap),
                    scope_name="overmind.sdk",
                    scope_version="0.1.55",
                    feedback_score=feedback,
                    attributes=root_attrs,
                )
            )
            cost = _verdicts(cap, root_id, entries, scored_at) if entries else 0.0
            pass_buf.append(
                ScoringPass(
                    project=project,
                    capability=cap,
                    trace_id=trace_id,
                    finished=scored_at,
                    verdict_counts={"scored": len(entries)} if entries else {"not_applicable": 1},
                    total_cost=round(cost, 6),
                )
            )
            if entries or error:
                _execution(
                    cap,
                    task,
                    trace_id,
                    root_id,
                    start_ns,
                    end_ns,
                    inp=inp,
                    feedback=feedback,
                    conv_ext=conv_ext,
                    error=error,
                    declared=declared,
                    anchors_seen=anchors,
                )
            _acc(cap, root_id, end=end_dt)
            trace_index[cap.pk].append((trace_id, end_dt, inp, out, error is None))
            if len(span_buf) >= 2000:
                _flush()
            return trace_id, end_dt

        conv_buf: dict = {}

        def _session(cap, t):
            ext = f"sess_{hexid(8)}"
            conv = Conversation.objects.create(project=project, external_id=ext, capability=cap)
            stamp(conv, t)
            conv_buf[ext] = conv
            return conv, ext

        def gen_triage_day(day, n):
            for _ in range(n):
                t0 = business_hour(day)
                merchant = pick(MERCHANTS)
                plan = wplan()
                tmpl, category, team, base_u, ent_u = pick(TICKETS)
                text = tmpl.format(
                    bank=pick(["Barclays", "Monzo", "HSBC", "Revolut Business"]),
                    day=pick(["Monday", "Tuesday", "yesterday morning"]),
                    month=pick(["June", "July", "August"]),
                    country=pick(["Brazil", "Vietnam", "Nigeria", "Romania"]),
                    city=pick(["Leeds", "Austin", "Rotterdam", "Lyon"]),
                    pid=f"pay_{hexid(6)}",
                )
                incident = random.random() < 0.12
                urgency = "critical" if incident else (ent_u if plan == "enterprise" else base_u)
                ok = random.random() > 0.03
                good = random.random() > 0.13
                out_team = team if good else pick(["support-general", "billing-support", "product"])
                out_urg = (
                    urgency
                    if good or random.random() < 0.5
                    else ("medium" if urgency in ("high", "critical") else "high")
                )
                inp = {
                    "ticket_text": text,
                    "merchant_plan": plan,
                    "previous_tickets": random.randint(0, 9),
                }
                out = {
                    "urgency": out_urg,
                    "category": category,
                    "team": out_team,
                    "summary": text.split(".")[0][:110] + ".",
                }
                conv = conv_ext = None
                if random.random() < 0.22:
                    conv, conv_ext = _session(triage_capability, t0)
                task = t_escalate if incident else t_classify
                anchors = (
                    [
                        "support.triage.run_triage",
                        "support.triage.lookup_merchant",
                        "support.triage.escalate",
                        "support.triage.route_ticket",
                    ]
                    if incident
                    else [
                        "support.triage.run_triage",
                        "support.triage.lookup_merchant",
                        "support.triage.classify",
                        "support.triage.apply_sla_floor",
                        "support.triage.route_ticket",
                    ]
                )
                steps = [
                    ("tool", "lookup_merchant", {"merchant_name": merchant}, rnd(60, 220, 1)),
                    (
                        "llm",
                        triage_capability.model,
                        random.randint(700, 1400),
                        random.randint(90, 220),
                        rnd(700, 2400, 1),
                    ),
                    (
                        "tool",
                        "route_ticket",
                        {"ticket_id": f"tk_{hexid(4)}", "team": out_team, "urgency": out_urg},
                        rnd(40, 120, 1),
                    ),
                ]
                if random.random() < 0.4 and not incident:
                    steps.insert(
                        1, ("tool", "search_kb", {"query": category.lower()}, rnd(90, 400, 1))
                    )
                entries = None
                if ok and random.random() < 0.86:
                    acc = rnd(0.72, 1.0) if good else rnd(0.15, 0.55)
                    team_ok = out_team in TEAMS
                    floor_ok = not (plan == "enterprise" and out_urg == "low") and not (
                        incident and out_urg in ("low", "medium")
                    )
                    entries = [
                        (
                            "Triage Accuracy",
                            triage_trace["Triage Accuracy"],
                            round(acc, 2),
                            None,
                            pick(_TRIAGE_RATIONALES[:2]) if good else pick(_TRIAGE_RATIONALES[2:]),
                        ),
                        (
                            "Valid Routing Team",
                            triage_trace["Valid Routing Team"],
                            1.0 if team_ok else 0.0,
                            team_ok,
                            "team is a registered routing team"
                            if team_ok
                            else "team is not in the routing registry",
                        ),
                        (
                            "SLA Floor Respected",
                            triage_trace["SLA Floor Respected"],
                            1.0 if floor_ok else 0.0,
                            floor_ok,
                            "Urgency at or above the plan floor."
                            if floor_ok
                            else "Enterprise merchant routed below medium.",
                        ),
                    ]
                emit_trace(
                    triage_capability,
                    t0,
                    inp,
                    out,
                    steps=steps,
                    task=task,
                    anchors=anchors,
                    error=None if ok else "LLM provider timeout after 3 retries",
                    conversation=conv,
                    conv_ext=conv_ext or "",
                    entries=entries,
                    declared=random.random() < 0.7,
                )
                if conv is not None and random.random() < 0.7:
                    q, a, cites = pick(KB_QA)
                    t1 = t0 + timedelta(minutes=random.randint(3, 25))
                    entries2 = None
                    if random.random() < 0.8:
                        grounded = random.random() > 0.1
                        entries2 = [
                            (
                                "Citation Support",
                                kb_trace["Citation Support"],
                                1.0 if grounded else 0.0,
                                grounded,
                                "All claims trace to the cited articles."
                                if grounded
                                else "Second paragraph states a fee not present in any cited article.",
                            ),
                            (
                                "Faithfulness",
                                kb_trace["Faithfulness"],
                                rnd(0.7, 1.0),
                                None,
                                "Answer stays within the retrieved articles.",
                            ),
                        ]
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
                        task=k_answer,
                        anchors=[
                            "support.kb.answer_question",
                            "support.kb.search_kb",
                            "support.kb.fetch_article",
                            "support.kb.compose_answer",
                        ],
                        conversation=conv,
                        conv_ext=conv_ext,
                        entries=entries2,
                    )

        def gen_kb_day(day, n):
            for _ in range(n):
                t0 = business_hour(day)
                q, a, cites = pick(KB_QA)
                ok = random.random() > 0.02
                declined = random.random() < 0.08
                entries = None
                if ok and not declined and random.random() < 0.8:
                    grounded = random.random() > 0.09
                    entries = [
                        (
                            "Citation Support",
                            kb_trace["Citation Support"],
                            1.0 if grounded else 0.0,
                            grounded,
                            "Every factual claim carries a supporting citation."
                            if grounded
                            else "The SLA claim cites payouts-schedule, which covers timing but not SLAs.",
                        ),
                        (
                            "Faithfulness",
                            kb_trace["Faithfulness"],
                            rnd(0.66, 1.0),
                            None,
                            "No content beyond the retrieved articles.",
                        ),
                    ]
                if declined:
                    out = {
                        "answer": "The help centre does not cover this. I have flagged it for the docs team.",
                        "citations": [],
                        "confidence": rnd(0.05, 0.25),
                    }
                    steps = [
                        ("tool", "search_kb", {"query": q[:40]}, rnd(120, 520, 1)),
                        (
                            "llm",
                            kb_capability.model,
                            random.randint(600, 900),
                            random.randint(40, 90),
                            rnd(600, 1400, 1),
                        ),
                    ]
                    task, anchors = (
                        k_decline,
                        [
                            "support.kb.answer_question",
                            "support.kb.search_kb",
                            "support.kb.decline",
                        ],
                    )
                else:
                    out = {"answer": a, "citations": cites, "confidence": rnd(0.6, 0.97)}
                    steps = [
                        ("tool", "search_kb", {"query": q[:40]}, rnd(120, 520, 1)),
                        ("tool", "fetch_article", {"slug": cites[0]}, rnd(40, 170, 1)),
                        (
                            "llm",
                            kb_capability.model,
                            random.randint(1500, 3300),
                            random.randint(170, 430),
                            rnd(1400, 5200, 1),
                        ),
                    ]
                    task, anchors = (
                        k_answer,
                        [
                            "support.kb.answer_question",
                            "support.kb.search_kb",
                            "support.kb.fetch_article",
                            "support.kb.compose_answer",
                        ],
                    )
                emit_trace(
                    kb_capability,
                    t0,
                    {"question": q, "merchant_plan": wplan()},
                    out,
                    steps=steps,
                    task=task,
                    anchors=anchors,
                    error=None if ok else "search_kb: upstream retrieval 502",
                    entries=entries,
                    declared=random.random() < 0.7,
                )

        def gen_dispute_day(day, n):
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
                    if resolution in ("accept", "request_evidence")
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
                    "draft_reply": f"We reviewed dispute {dispute_id} (reason {code}). "
                    + (
                        "Given the amount involved we recommend accepting."
                        if resolution == "accept"
                        else "We need the delivery confirmation before we can respond."
                        if resolution == "request_evidence"
                        else "The evidence on file supports the original charge and we recommend representment."
                    ),
                    "evidence_used": evidence,
                }
                requesting = resolution == "request_evidence"
                if requesting:
                    task = d_request
                    anchors = [
                        "support.disputes.resolve_dispute",
                        "support.disputes.lookup_transaction",
                        "support.disputes.fetch_dispute_evidence",
                        "support.disputes.request_evidence",
                    ]
                    steps = [
                        ("tool", "lookup_transaction", {"dispute_id": dispute_id}, rnd(80, 260, 1)),
                        (
                            "tool",
                            "fetch_dispute_evidence",
                            {"dispute_id": dispute_id},
                            rnd(200, 900, 1),
                        ),
                        (
                            "llm",
                            dispute_capability.model,
                            random.randint(1200, 2200),
                            random.randint(120, 260),
                            rnd(1400, 3200, 1),
                        ),
                    ]
                    entries = (
                        [
                            (
                                "Evidence Request Clarity",
                                dispute_trace["Evidence Request Clarity"],
                                rnd(0.6, 1.0),
                                None,
                                "Names the missing delivery confirmation and nothing else.",
                            )
                        ]
                        if ok and random.random() < 0.8
                        else None
                    )
                else:
                    task = d_resolve
                    anchors = [
                        "support.disputes.resolve_dispute",
                        "support.disputes.lookup_transaction",
                        "support.disputes.fetch_dispute_evidence",
                        "support.disputes.check_policy",
                        "support.disputes.draft_reply",
                    ]
                    steps = [
                        ("tool", "lookup_transaction", {"dispute_id": dispute_id}, rnd(80, 260, 1)),
                        (
                            "tool",
                            "fetch_dispute_evidence",
                            {"dispute_id": dispute_id},
                            rnd(200, 900, 1),
                        ),
                        (
                            "tool",
                            "check_policy",
                            {"reason_code": code, "amount": amount},
                            rnd(30, 120, 1),
                        ),
                        (
                            "llm",
                            dispute_capability.model,
                            random.randint(2400, 4800),
                            random.randint(260, 620),
                            rnd(2600, 7800, 1),
                        ),
                    ]
                    entries = None
                    if ok and random.random() < 0.82:
                        entries = [
                            (
                                "Resolution Policy Compliance",
                                dispute_trace["Resolution Policy Compliance"],
                                1.0 if compliant else 0.0,
                                compliant,
                                "Two evidence classes support representment; no timeline promises in the draft."
                                if compliant
                                else "Represented with a single evidence class — policy requires two.",
                            ),
                            (
                                "Tool Sequence Accuracy",
                                dispute_trace["Tool Sequence Accuracy"],
                                1.0,
                                True,
                                "lookup_transaction → fetch_dispute_evidence → check_policy in order.",
                            ),
                        ]
                emit_trace(
                    dispute_capability,
                    t0,
                    inp,
                    out,
                    steps=steps,
                    task=task,
                    anchors=anchors,
                    error=None if ok else "fetch_dispute_evidence: evidence store timeout",
                    entries=entries,
                    declared=random.random() < 0.75,
                )

        for i in range(DAYS + 1):
            day = days_ago(DAYS - i)
            gen_triage_day(day, daily_volume(52, i, day.weekday()))
            gen_kb_day(day, daily_volume(30, i, day.weekday()))
            gen_dispute_day(day, daily_volume(18, i, day.weekday()))

        # Live-scoring showcase: two members land 0.92 apart, so the composer stamps a conflict.
        _cq, _ca, _ccites = KB_QA[0]
        emit_trace(
            kb_capability,
            NOW - timedelta(hours=3, minutes=20),
            {"question": _cq, "merchant_plan": "growth"},
            {"answer": _ca, "citations": _ccites, "confidence": 0.91},
            steps=[
                ("tool", "search_kb", {"query": _cq}, 320.0),
                ("tool", "fetch_article", {"slug": _ccites[0]}, 95.0),
                ("llm", kb_capability.model, 2100, 240, 2800.0),
            ],
            task=k_answer,
            anchors=[
                "support.kb.answer_question",
                "support.kb.search_kb",
                "support.kb.fetch_article",
                "support.kb.compose_answer",
            ],
            entries=[
                (
                    "Citation Support",
                    kb_trace["Citation Support"],
                    0.0,
                    False,
                    "The 2-business-day settlement claim cites payouts-schedule, which gives timing bands but no settlement guarantee.",
                ),
                (
                    "Faithfulness",
                    kb_trace["Faithfulness"],
                    0.92,
                    None,
                    "Aside from the settlement sentence, every claim stays within the retrieved articles.",
                ),
            ],
        )
        _flush()

        for conv_ext in conv_buf:
            rows = list(
                TaskExecution.objects.filter(project=project, conversation_id=conv_ext).exclude(
                    success_score=None
                )
            )
            if rows:
                score = round(sum(r.success_score for r in rows) / len(rows), 4)
                TaskExecution.objects.filter(project=project, conversation_id=conv_ext).update(
                    session_score=score,
                    session_rationale=f"Mean of {len(rows)} scored turn{'s' if len(rows) != 1 else ''} in the conversation.",
                )

        for a in capabilities:
            u = usage_acc[a.pk]
            Capability.objects.filter(pk=a.pk).update(
                usage_stats={
                    "prompt_tokens": u["prompt_tokens"],
                    "completion_tokens": u["completion_tokens"],
                    "total_tokens": u["total_tokens"],
                    "llm_calls": u["llm_calls"],
                    "tool_calls": u["tool_calls"],
                    "cost_usd": round(u["cost_usd"], 6),
                    "models": u["models"],
                    "_spans": u["_spans"][-2000:],
                    "updated_at": (u["last"] or NOW).isoformat(),
                },
                last_activity_at=u["last"],
            )

        self.stdout.write(
            f"   {Span.objects.filter(project=project).count()} spans across {sum(len(v) for v in trace_index.values())} traces"
        )

        # ── Datasets ─────────────────────────────────────────────────────────────────────

        self.stdout.write("Building datasets...")

        def _stamp_dataset(ds, born):
            stamp(ds, born, born + timedelta(hours=1))
            for i, c in enumerate(ds.cells.order_by("position")):
                Cell.objects.filter(pk=c.pk).update(created_at=born + timedelta(hours=1, minutes=i))

        def _run(ds, born, *, want=None):
            notebook_run.execute(ds)
            ds.refresh_from_db()
            if ds.state != "idle":
                raise RuntimeError(f"seed dataset {ds.name!r}: {ds.error}")
            if want:
                ok, reason = ds.active_cell.fits(want)
                if not ok:
                    raise RuntimeError(f"seed dataset {ds.name!r}: wanted {want} — {reason}")
            _stamp_dataset(ds, born)
            return ds

        def _paste(name, rows, intent, *, capability=None, born):
            ds = Dataset.objects.create(
                project=project,
                capability=capability,
                name=name,
                intent=intent,
                source_spec={"pasted": True},
            )
            dataset_land.land_rows(ds, [dict(r) for r in rows], spec={"pasted": True})
            return _run(ds, born, want=intent if intent != "pending" else None)

        def _from_traces(cap, name, *, want, intent, born):
            entries = [e for e in trace_index[cap.pk] if e[4]]
            step = max(1, len(entries) // want)
            chosen = entries[::step][:want]
            ds = Dataset.objects.create(
                project=project, capability=cap, name=name, intent=intent, source_kind="traces"
            )
            dataset_land.land_traces(ds, {"trace_ids": [tid for tid, *_ in chosen]})
            if intent == "eval":
                dataset_lifecycle.add_cell(
                    ds,
                    title="Eval pairs",
                    script=_TRACE_EVAL_SCRIPT,
                    note="input + delivered output as the reference",
                )
            return _run(ds, born, want=intent)

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

        _triage_tools = [
            _tool_decl(
                "lookup_merchant",
                "Fetch the plan and open incident flags.",
                {"merchant_name": "string"},
            )
        ]
        _dispute_tools = [
            _tool_decl(
                "lookup_transaction",
                "Fetch the ledger row for a dispute.",
                {"dispute_id": "string"},
            ),
            _tool_decl(
                "fetch_dispute_evidence",
                "List evidence on file for a dispute.",
                {"dispute_id": "string"},
            ),
            _tool_decl(
                "check_policy",
                "Policy thresholds for a reason code.",
                {"reason_code": "string", "amount": "number"},
            ),
        ]

        def _triage_row(text, plan, team, urgency, category):
            call_id = f"call_{hexid(4)}"
            return {
                "messages": [
                    {"role": "system", "content": TRIAGE_PROMPT},
                    {"role": "user", "content": f"Plan: {plan}\n\n{text}"},
                    {
                        "role": "assistant",
                        "content": "",
                        "tool_calls": [
                            {
                                "id": call_id,
                                "type": "function",
                                "function": {
                                    "name": "lookup_merchant",
                                    "arguments": json.dumps({"merchant_name": pick(MERCHANTS)}),
                                },
                            }
                        ],
                    },
                    {
                        "role": "tool",
                        "tool_call_id": call_id,
                        "content": json.dumps(
                            {
                                "plan": plan,
                                "open_incidents": 0,
                                "account_age_days": random.randint(30, 900),
                            }
                        ),
                    },
                    {
                        "role": "assistant",
                        "content": json.dumps(
                            {
                                "urgency": urgency,
                                "category": category,
                                "team": team,
                                "summary": text.split(".")[0][:110] + ".",
                            }
                        ),
                    },
                ],
                "tools": _triage_tools,
                "team": team,
            }

        triage_rows = []
        for i in range(360):
            tmpl, category, team, base_u, ent_u = TICKETS[i % len(TICKETS)]
            plan = PLANS[(i // len(TICKETS)) % 3]
            text = tmpl.format(
                bank=pick(["Barclays", "Monzo", "HSBC"]),
                day=pick(["Monday", "yesterday"]),
                month=pick(["June", "July"]),
                country=pick(["Brazil", "Vietnam"]),
                city=pick(["Leeds", "Austin"]),
                pid=f"pay_{hexid(6)}",
            )
            triage_rows.append(
                _triage_row(text, plan, team, ent_u if plan == "enterprise" else base_u, category)
            )
        triage_rows.append(dict(triage_rows[4]))  # planted exact duplicate
        triage_rows.append(dict(triage_rows[9]))

        triage_train = _paste(
            "Ticket Triage — Transcripts",
            triage_rows,
            "train",
            capability=triage_capability,
            born=days_ago(19),
        )
        dataset_lifecycle.add_cell(
            triage_train,
            title="Drop exact duplicates",
            script="df = df.drop_duplicates(subset=['messages']).reset_index(drop=True)\n",
            note="2 rows",
        )
        dataset_lifecycle.add_cell(
            triage_train,
            title="Cap support-general at 20%",
            script=(
                "cap = int(len(df) * 0.20)\n"
                "general = df[df['team'] == 'support-general']\n"
                "drop = general.index[cap:] if len(general) > cap else general.index[:0]\n"
                "df = df.drop(index=drop).reset_index(drop=True)\n"
            ),
            note="class balance",
        )
        _run(triage_train, days_ago(19), want="train")
        triage_golden = _from_traces(
            triage_capability, "Triage Golden Set", want=60, intent="eval", born=days_ago(21)
        )

        kb_rows = []
        for i, (q, a, cites) in enumerate(KB_QA * 5):
            kb_rows.append(
                {
                    "input": {"question": q, "merchant_plan": PLANS[i % 3]},
                    "expected_output": {"answer": a, "citations": cites},
                    "persona": pick(["merchant-admin", "developer", "finance-lead"]),
                }
            )
        kb_golden = _paste(
            "KB Answers — Golden", kb_rows, "eval", capability=kb_capability, born=days_ago(24)
        )

        def _dispute_row(
            dispute_id, code, amount, reason, resolution, evidence, *, merchant_note=""
        ):
            call_id = f"call_{hexid(4)}"
            answer = json.dumps(
                {
                    "resolution": resolution,
                    "draft_reply": f"We reviewed dispute {dispute_id} (reason {code}). "
                    + (
                        "We recommend accepting this dispute."
                        if resolution == "accept"
                        else "The evidence on file supports the original charge; we recommend representment."
                        if resolution == "represent"
                        else "We need additional evidence from the merchant before responding."
                    ),
                    "evidence_used": evidence,
                }
            )
            return {
                "messages": [
                    {"role": "system", "content": DISPUTE_PROMPT},
                    {
                        "role": "user",
                        "content": f"Dispute {dispute_id}: reason {code} ({reason.replace('_', ' ')}), amount ${amount:.2f}."
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
                    {
                        "role": "tool",
                        "tool_call_id": call_id,
                        "content": json.dumps({"evidence": evidence or ["none_on_file"]}),
                    },
                    {"role": "assistant", "content": answer},
                ],
                "tools": _dispute_tools,
            }

        dispute_rows = []
        for i in range(400):
            reason, code = DISPUTE_REASONS[i % len(DISPUTE_REASONS)]
            amount = round(random.choice([rnd(8, 24), rnd(30, 240), rnd(250, 4200)]), 2)
            small_fraud = reason == "fraudulent" and amount < 25
            resolution = (
                "accept"
                if small_fraud
                else ["represent", "represent", "request_evidence", "accept"][i % 4]
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
            dispute_rows.append(
                _dispute_row(
                    f"dp_{hexid(6)}",
                    code,
                    amount,
                    reason,
                    resolution,
                    evidence,
                    merchant_note=pick(MERCHANTS) if i % 3 == 0 else "",
                )
            )
        dispute_rows.append(dict(dispute_rows[7]))
        dispute_rows.append(
            _dispute_row(
                f"dp_{hexid(6)}",
                "13.1",
                148.0,
                "product_not_received",
                "request_evidence",
                [],
                merchant_note="Customer reachable at lena.hoff@gmail.com or +49 171 555 0192.",
            )
        )

        dispute_train = _paste(
            "Dispute Resolutions — Train",
            dispute_rows,
            "train",
            capability=dispute_capability,
            born=days_ago(16),
        )
        dispute_golden = _from_traces(
            dispute_capability,
            "Dispute Golden — from traces",
            want=36,
            intent="eval",
            born=days_ago(15),
        )

        # A train + eval pair cut from one source, the way the New dataset dialog lands it.
        split_source = dataset_land.read_rows(
            [dict(r) for r in triage_rows[:200]], spec={"filename": "triage-august.jsonl"}
        )
        split_train = Dataset.objects.create(
            project=project,
            capability=triage_capability,
            name="Triage August train",
            intent="train",
        )
        split_eval = Dataset.objects.create(
            project=project, capability=triage_capability, name="Triage August eval", intent="eval"
        )
        train_part, eval_part = split_source.split(eval_percent=20, position="random")
        cut = {"eval_percent": 20, "position": "random"}
        dataset_land.commit(
            split_train,
            replace(
                train_part,
                spec={
                    **train_part.spec,
                    "split": {**cut, "role": "train", "sibling": str(split_eval.id)},
                },
            ),
        )
        dataset_land.commit(
            split_eval,
            replace(
                eval_part,
                spec={
                    **eval_part.spec,
                    "split": {**cut, "role": "eval", "sibling": str(split_train.id)},
                },
            ),
        )
        _run(split_train, days_ago(2), want="train")
        dataset_lifecycle.add_cell(
            split_eval,
            title="Eval pairs from transcripts",
            script=(
                "import json\n"
                "msgs = df['messages'].apply(lambda m: json.loads(m) if isinstance(m, str) else m)\n"
                "df['input'] = msgs.apply(lambda m: {'ticket': next(x['content'] for x in m if x['role'] == 'user')})\n"
                "df['expected_output'] = msgs.apply(lambda m: m[-1]['content'])\n"
                "df = df[['input', 'expected_output', 'team']]\n"
            ),
            note="the last assistant turn is the reference",
        )
        _run(split_eval, days_ago(2), want="eval")

        voice_notes = _paste(
            "Voice-of-customer notes (raw)",
            [
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
                {
                    "note": "Three merchants confused net vs gross settlement in reports.",
                    "topic": "product",
                },
                {
                    "note": "Hachi Ramen wants QR pay-at-table before the summer rush.",
                    "topic": "product",
                },
                {
                    "note": "Baltic Board Games: multi-currency pricing beta went 'flawlessly'.",
                    "topic": "wins",
                },
                {
                    "note": "Clearline saw +2.1pp auth rate after 3DS exemption rollout.",
                    "topic": "wins",
                },
                {
                    "note": "Golden Hour Wines blocked on alcohol MCC review for 9 days.",
                    "topic": "ops",
                },
                {
                    "note": "Volt Cycle asked twice about offline terminal mode — launch blocker.",
                    "topic": "product",
                },
            ],
            "pending",
            born=days_ago(5),
        )

        seed_datasets = [
            triage_train,
            triage_golden,
            kb_golden,
            dispute_train,
            dispute_golden,
            split_train,
            split_eval,
            voice_notes,
        ]

        def _chat(ds, turns, born):
            """turns: [(role, text, steps, cells)]; the agent turns read as landed by the workshop."""
            chat = []
            at = born + timedelta(hours=1, minutes=2)
            for i, (role, text, steps, cells) in enumerate(turns):
                entry = {
                    "role": role,
                    "text": text,
                    "at": (at + timedelta(minutes=i * 2)).isoformat(),
                }
                if role == "agent":
                    entry.update(
                        {
                            "error": "",
                            "cells": cells,
                            "steps": steps,
                            "ms": random.randint(24000, 96000),
                            "engine": "openrouter",
                            "model": "anthropic/claude-sonnet-5",
                        }
                    )
                chat.append(entry)
            Dataset.objects.filter(pk=ds.pk).update(chat=chat)

        def _step(i, tool, summary, ms):
            return {
                "type": "activity",
                "phase": "tool_done",
                "id": f"s{i}",
                "status": "done",
                "tool": tool,
                "title": tool,
                "summary": summary,
                "ok": True,
                "durationMs": ms,
            }

        def _think(i, text, ms):
            return {
                "type": "activity",
                "phase": "thinking",
                "id": f"t{i}",
                "status": "done",
                "text": text,
                "durationMs": ms,
            }

        _chat(
            triage_train,
            [
                (
                    "user",
                    "Prepare this dataset: shape it to both contracts, then run the quality checks.",
                    [],
                    [],
                ),
                (
                    "agent",
                    "Train contract holds on the source: every row is a transcript with an assistant turn and declared tools.\n\n"
                    "Quality checks on 362 rows: 2 exact duplicates, 0 truncated finals, 0 undeclared tool calls, 0 rows from another capability. "
                    "Class balance across `team` is uneven — support-general carries 31% of rows.\n\n"
                    "- 1.1 **Drop exact duplicates** — 2 rows.\n"
                    "- 1.2 **Cap support-general at 20%** — 18 rows.\n\n"
                    "intent ok · capability ok. 342 rows on the active version.",
                    [
                        _think(
                            1,
                            "Reading the status, the contracts and the playbook before touching the rows.",
                            4200,
                        ),
                        _step(
                            2, "status", "362 rows × 3 columns · train fits · capability fits", 380
                        ),
                        _step(
                            3,
                            "query",
                            "SELECT COUNT(*) FROM t GROUP BY md5(messages) HAVING COUNT(*) > 1 → 2",
                            910,
                        ),
                        _step(
                            4,
                            "inspect",
                            "team share: support-general 31%, oncall-payments 22%, …",
                            1400,
                        ),
                        _step(5, "add_cell", "1.1 Drop exact duplicates — 360 rows", 3100),
                        _step(6, "add_cell", "1.2 Cap support-general at 20% — 342 rows", 2900),
                    ],
                    [
                        {"id": str(c.id), "action": "ran"}
                        for c in triage_train.cells.filter(position__gt=0)
                    ],
                ),
            ],
            days_ago(19),
        )
        _chat(
            triage_golden,
            [
                (
                    "user",
                    "Prepare this dataset: shape it to both contracts, then run the quality checks.",
                    [],
                    [],
                ),
                (
                    "agent",
                    "Eval contract: the source carries `input` and `output`; cell 1.1 renames the delivered output to `expected_output` and drops the wire transcript.\n\n"
                    "Quality checks on 60 rows: 0 empty references, 0 ambiguous duplicates, 0 reference leakage. "
                    "Every `input` carries the capability's required keys.\n\n"
                    "intent ok · capability ok.",
                    [
                        _think(
                            1,
                            "The rows are one per trace, so the reference is the delivered output.",
                            2600,
                        ),
                        _step(2, "status", "60 rows × 18 columns · pending", 340),
                        _step(
                            3,
                            "try_script",
                            "rename output → expected_output on 1.0: 60 rows, fits eval",
                            2200,
                        ),
                        _step(4, "set_intent", "eval", 120),
                    ],
                    [
                        {"id": str(c.id), "action": "ran"}
                        for c in triage_golden.cells.filter(position__gt=0)
                    ],
                ),
                ("user", "How many rows route to oncall-payments?", [], []),
                (
                    "agent",
                    "14 of 60 rows have `expected_output.team = oncall-payments`; 11 of them are payout delays and 3 are 3DS failures.",
                    [
                        _step(
                            1,
                            "query",
                            "SELECT COUNT(*) FROM t WHERE json_extract(expected_output, '$.team') = 'oncall-payments' → 14",
                            640,
                        )
                    ],
                    [],
                ),
            ],
            days_ago(21),
        )

        # The pair's chats: the eval half needed a cell, the train half did not.
        _chat(
            split_eval,
            [
                (
                    "user",
                    "Prepare this dataset: shape it to both contracts, then run the quality checks.",
                    [],
                    [],
                ),
                (
                    "agent",
                    "The rows are transcripts, so the eval contract failed on the source. Cell 1.1 lifts the user message into `input` and the last assistant turn into `expected_output`, keeping `team` for the class metrics.\n\n"
                    "Quality checks on 40 rows: 0 empty references, 0 duplicates, 0 leakage.\n\nintent ok · capability ok.",
                    [
                        _think(
                            1,
                            "Transcripts cannot fit eval as-is; derive the pair from the messages.",
                            3100,
                        ),
                        _step(
                            2,
                            "status",
                            "40 rows × 3 columns · eval does not fit: no input column",
                            300,
                        ),
                        _step(
                            3,
                            "try_script",
                            "derive input + expected_output: 40 rows, fits eval",
                            2400,
                        ),
                        _step(4, "add_cell", "1.1 Eval pairs from transcripts — 40 rows", 2800),
                    ],
                    [
                        {"id": str(c.id), "action": "ran"}
                        for c in split_eval.cells.filter(position__gt=0)
                    ],
                ),
            ],
            days_ago(2),
        )

        # ── Eval runs ────────────────────────────────────────────────────────────────────

        self.stdout.write("Creating evaluation runs...")

        mr_sol = ModelRef.objects.create(
            project=project,
            label="GPT-5.6 Sol (incumbent)",
            provider="openai",
            model_id="gpt-5.6-sol",
            params={"temperature": 0.2, "max_tokens": 1024},
        )
        mr_sonnet = ModelRef.objects.create(
            project=project,
            label="Claude Sonnet 5",
            provider="anthropic",
            model_id="claude-sonnet-5",
            params={"temperature": 0.2},
        )
        mr_gemini = ModelRef.objects.create(
            project=project,
            label="Gemini 3.1 Pro",
            provider="google",
            model_id="gemini-3.1-pro-preview",
            params={"temperature": 0.2},
        )

        def clampq(quality, spread=0.12):
            return round(min(1.0, max(0.0, random.gauss(quality, spread))), 2)

        def _traj(
            user_text,
            final_text,
            *,
            tools=(),
            model,
            mode="existing",
            trace_id="",
            pt=None,
            ct=None,
        ):
            pt = pt or random.randint(700, 2600)
            ct = ct or random.randint(120, 500)
            ms = rnd(900, 3800, 1)
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
                messages.append(
                    {"role": "tool", "tool_call_id": call_id, "content": json.dumps(result)}
                )
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
                "salient_steps": [
                    {"type": "final_answer", "ref": "final", "preview": final_text[:80]}
                ],
                "approx_tokens": pt + ct,
                "num_messages": len(messages),
                "num_tool_calls": len(nodes),
                "num_turns": 1,
            }
            return trajectory, structured

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
                subs = [
                    {
                        "id": item["id"],
                        "verdict": ok,
                        "score": 1.0 if ok else 0.0,
                        "reasoning": reasons[0 if ok else -1][:160],
                    }
                    for item in (snap.get("checklist") or [{"id": "check", "q": ""}])
                ]
                subs.append(
                    {
                        "_threshold": {
                            "pass_threshold": snap.get("pass_threshold"),
                            "gated_fail": not ok,
                        }
                    }
                )
                subs.append(_resolution_sub())
                value, passed = (1.0, True) if ok else (0.0, False)
                reasoning = reasons[0] if ok else reasons[-1]
            else:
                value = clampq(quality)
                passed = None
                subs = []
                for item in snap.get("checklist") or []:
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
                project=project,
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
                project=project,
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
                sub_scores=[
                    {"_resolution": {"output": {"strategy": "final_output", "shape": "str"}}}
                ],
                cost=0.0,
                latency_ms=rnd(1, 8, 1),
            )

        def _trajectory_score(run, variant, sample, run_ev, quality, ref_tools):
            ok = random.random() < quality
            actual = list(ref_tools) if ok else list(ref_tools)[:-1]
            return Score(
                project=project,
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
                reasoning=f"trajectory match: {'pass' if ok else 'fail'} ({len(actual)} actual vs {len(ref_tools)} reference calls)",
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

        def make_eval_run(
            *,
            name,
            description,
            dataset,
            eval_set,
            members,
            variants,
            born,
            n_samples,
            sample_fn,
            failed=None,
            use=True,
        ):
            """variants: [(label, model_name, model_ref, mode, is_baseline, quality)]."""
            version = dataset.active_cell
            if use:
                # Historical demo consumption is not a fabricated semantic validation pass.
                Cell.objects.filter(pk=version.pk).update(used_at=born)
                version.refresh_from_db()
            run = EvalRun.objects.create(
                project=project,
                name=name,
                description=description,
                data_source="dataset",
                dataset=dataset,
                cell=version,
                max_items=n_samples,
                eval_set=eval_set,
                sampling=1.0,
                status="pending",
                triggered_by=owner,
                celery_task_id=str(uuid.uuid4()),
            )
            if failed:
                run.status = "failed"
                run.error = failed
                run.save(update_fields=["status", "error"])
                stamp(
                    run, born, born + timedelta(minutes=3), completed_at=born + timedelta(minutes=3)
                )
                return run, []
            run_evs = [
                RunEvaluator.objects.create(
                    run=run,
                    evaluator=m.evaluator,
                    snapshot=eval_snapshots.build_snapshot(m.evaluator),
                    sampling=1.0,
                    enabled=True,
                    order=o,
                )
                for o, m in enumerate(members)
            ]
            variant_rows = []
            for order, (label, model_name, model_ref, mode, is_baseline, quality) in enumerate(
                variants
            ):
                v = EvalVariant.objects.create(
                    run=run,
                    label=label,
                    model_ref=model_ref,
                    model_name=model_name,
                    mode=mode,
                    is_baseline=is_baseline,
                    order=order,
                )
                variant_rows.append(
                    (v, quality, model_name or (model_ref.model_id if model_ref else ""))
                )
            datapoints = row_store.sample_rows(dataset, n_samples)
            score_buf, sample_pairs = [], []
            for variant, quality, vmodel in variant_rows:
                for dp in datapoints:
                    trajectory, structured, expected, ref_tools = sample_fn(
                        dp, quality, vmodel, variant.mode
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
            Score.objects.bulk_create(score_buf, batch_size=500)
            backdate(EvalSample, sample_pairs)
            backdate(Score, [(s.pk, born + timedelta(minutes=rnd(4, 14))) for s in score_buf])
            eval_tasks.aggregate_run.apply(kwargs={"eval_run_id": str(run.pk)})
            run.refresh_from_db()
            stamp(
                run, born, born + timedelta(minutes=15), completed_at=born + timedelta(minutes=15)
            )
            return run, [v for v, _, _ in variant_rows]

        def triage_sample(dp, quality, model, mode):
            inp = dp.input if isinstance(dp.input, dict) else {}
            text = inp.get("ticket_text") or inp.get("ticket") or ""
            expected = dp.expected_output or {}
            if isinstance(expected, str):
                try:
                    expected = json.loads(expected)
                except ValueError:
                    expected = {"summary": expected}
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
            return traj, structured, expected, ["lookup_merchant"]

        def kb_sample(dp, quality, model, mode):
            inp = dp.input if isinstance(dp.input, dict) else {}
            expected = dp.expected_output or {}
            answer = expected.get("answer", "") if isinstance(expected, dict) else ""
            cites = expected.get("citations", []) if isinstance(expected, dict) else []
            final = json.dumps(
                {"answer": answer, "citations": cites, "confidence": clampq(quality, 0.08)}
            )
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
            return traj, structured, expected, ["search_kb", "fetch_article"]

        def dispute_sample(dp, quality, model, mode):
            inp = dp.input if isinstance(dp.input, dict) else {}
            expected = dp.expected_output or {}
            final = json.dumps(expected if isinstance(expected, dict) else {})
            ref_tools = ["lookup_transaction", "fetch_dispute_evidence", "check_policy"]
            traj, structured = _traj(
                f"Resolve dispute {inp.get('dispute_id', 'dp_x')} (reason {inp.get('reason_code', '10.4')}, ${inp.get('amount', 0)}).",
                final,
                tools=[
                    (t, {"dispute_id": inp.get("dispute_id", "dp_x")}, {"ok": True})
                    for t in ref_tools
                ],
                model=model,
                mode=mode,
            )
            return traj, structured, expected, ref_tools

        triage_first_run, _ = make_eval_run(
            name="Triage rubric — first baseline",
            description="Production model against the golden set.",
            dataset=triage_golden,
            eval_set=triage_set,
            members=list(triage_set.members.filter(role="generative")),
            variants=[
                ("production (gpt-5.6-sol)", "openai/gpt-5.6-sol", mr_sol, "existing", True, 0.71)
            ],
            born=days_ago(20),
            n_samples=60,
            sample_fn=triage_sample,
        )
        triage_v3_run, triage_v3_variants = make_eval_run(
            name="Triage — prompt v3 verification",
            description="Optimiser winner replayed against the incumbent prompt.",
            dataset=triage_golden,
            eval_set=triage_set,
            members=list(triage_set.members.filter(role="generative")),
            variants=[
                ("prompt v2 (baseline)", "openai/gpt-5.6-sol", mr_sol, "generate", True, 0.72),
                ("prompt v3", "openai/gpt-5.6-sol", mr_sol, "generate", False, 0.86),
            ],
            born=days_ago(11),
            n_samples=60,
            sample_fn=triage_sample,
            use=False,
        )
        kb_run, _ = make_eval_run(
            name="KB answers — citation audit",
            description="Sonnet against Gemini on the golden questions.",
            dataset=kb_golden,
            eval_set=kb_set,
            members=list(kb_set.members.filter(role="generative")),
            variants=[
                (
                    "claude-sonnet-5 (production)",
                    "anthropic/claude-sonnet-5",
                    mr_sonnet,
                    "generate",
                    True,
                    0.84,
                ),
                (
                    "gemini-3.1-pro",
                    "google/gemini-3.1-pro-preview",
                    mr_gemini,
                    "generate",
                    False,
                    0.77,
                ),
            ],
            born=days_ago(13),
            n_samples=40,
            sample_fn=kb_sample,
        )
        kb_failed_run, _ = make_eval_run(
            name="KB answers — citation audit (retry)",
            description="",
            dataset=kb_golden,
            eval_set=kb_set,
            members=[],
            variants=[],
            born=days_ago(13, h=-2),
            n_samples=40,
            sample_fn=kb_sample,
            failed="Generation stalled: the judge provider returned 429 for 20 minutes.",
            use=False,
        )

        # ── Optimiser runs ───────────────────────────────────────────────────────────────

        self.stdout.write("Creating optimiser runs...")

        def _variant_score(run, variant) -> float:
            metrics = (
                (run.summary or {}).get("variants", {}).get(str(variant.pk), {}).get("metrics", {})
            )
            vals = [
                m.get("mean") if m.get("mean") is not None else m.get("pass_rate")
                for m in metrics.values()
            ]
            vals = [v for v in vals if v is not None]
            return round(sum(vals) / len(vals) * 100, 1) if vals else 0.0

        def build_experiment(
            *,
            cap,
            dataset,
            eval_set,
            born,
            done,
            entrypoint,
            baseline_q,
            iteration_qs,
            diffs,
            status="completed",
            num_iterations=None,
            with_eval_runs=False,
            sample_fn=triage_sample,
            mode="optimize",
            model_ids=(),
        ):
            """iteration_qs: per iteration, a list of (quality, target_model) or plain qualities."""
            version = dataset.active_cell
            exp = OptimizerExperiment.objects.create(
                project=project,
                capability=cap,
                dataset=dataset,
                cell=version,
                eval_set=eval_set,
                triggered_by=owner,
                entrypoint=entrypoint,
                code_trigger=f"{entrypoint}(**datapoint)",
                mode=mode,
                model_ids=list(model_ids),
                status=status,
                num_iterations=num_iterations or len(iteration_qs),
                num_candidates_per_iteration=len(iteration_qs[0]) if iteration_qs else 3,
                command_template=_COMMAND_TEMPLATE,
                cursor_usage={},
            )
            datapoints = list(row_store.iter_rows(version))
            span_hours = max(4.0, (done - born).total_seconds() / 3600.0)
            total_iters = 1 + len(iteration_qs)
            cmd_pairs, cand_pairs, iter_pairs = [], [], []

            def _mk_eval_run(candidate, label, quality, t):
                run, variants = make_eval_run(
                    name=f"Optimiser · {cap.name} · {label}",
                    description=f"Optimiser run {exp.pk}, {label}",
                    dataset=dataset,
                    eval_set=eval_set,
                    members=list(eval_set.members.filter(role="generative")),
                    variants=[(label, cap.model, None, "existing", candidate.is_baseline, quality)],
                    born=t,
                    n_samples=len(datapoints),
                    sample_fn=sample_fn,
                    use=False,
                )
                variants[0].params = {"optimizer_candidate_id": str(candidate.pk)}
                variants[0].save(update_fields=["params"])
                return run

            def _commands_for(candidate, iteration, quality, t, trace_type, originals):
                rows, per = [], []
                for idx, dp in enumerate(datapoints):
                    score = round(min(100.0, max(5.0, random.gauss(quality * 100, 7.0))), 1)
                    per.append(score)
                    out = (
                        dp.expected_output
                        if isinstance(dp.expected_output, dict)
                        else {"output": dp.expected_output}
                    )
                    rows.append(
                        OptimizerCommand(
                            experiment=exp,
                            candidate=candidate,
                            iteration=iteration,
                            datapoint_index=idx,
                            input=dp.input,
                            command=exp.command_template.replace(
                                "__DATAPOINT_INPUT__", repr(dp.input)
                            ),
                            output=json.dumps(out)[:400],
                            result={
                                "output": json.dumps(out)[:400],
                                "trace_id": hexid(16),
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
                cmd_pairs.extend((r.pk, t + timedelta(minutes=j * 0.5)) for j, r in enumerate(rows))
                return rows, round(sum(per) / len(per), 1)

            def _align(candidate, target, current):
                from django.db.models import F

                OptimizerCommand.objects.filter(candidate=candidate).update(
                    score=F("score") + (target - current)
                )

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
            base_cmds, base_score = _commands_for(
                base_cand, base_iter, baseline_q, t, "original", None
            )
            base_originals = [c.result["trace_id"] for c in base_cmds]
            if with_eval_runs:
                base_run = _mk_eval_run(base_cand, "baseline", baseline_q, t)
                new_score = _variant_score(base_run, base_run.variants.first())
                _align(base_cand, new_score, base_score)
                base_score = new_score
                base_cand.eval_run = base_run
            base_cand.score = base_score
            base_cand.save(update_fields=["score", "eval_run"] if with_eval_runs else ["score"])
            base_iter.scores = {"best": base_score}
            base_iter.save(update_fields=["scores"])
            iter_pairs.append((base_iter.pk, t))
            cand_pairs.append((base_cand.pk, t))

            scores = {"baseline": base_score, "best": base_score}
            by_model = {}
            stalled = 0
            winner = None
            for i, cand_qs in enumerate(iteration_qs, start=1):
                t = born + timedelta(hours=span_hours * i / total_iters)
                iteration = OptimizerIteration.objects.create(
                    experiment=exp, order=i, name=f"Iteration{i}", status="completed"
                )
                iter_pairs.append((iteration.pk, t))
                iter_scores, iter_models = {}, {}
                iter_best_cand, iter_best = None, -1.0
                for ci, spec in enumerate(cand_qs):
                    q, target_model = spec if isinstance(spec, tuple) else (spec, "")
                    cand = OptimizerCandidate.objects.create(
                        experiment=exp,
                        iteration=iteration,
                        candidate_index=ci,
                        code_path="" if target_model else diffs[(i + ci) % len(diffs)],
                        target_model=target_model,
                        is_baseline=False,
                        status="evaluated",
                    )
                    _, cscore = _commands_for(
                        cand,
                        iteration,
                        q,
                        t + timedelta(minutes=5 + ci * 10),
                        "replay",
                        base_originals,
                    )
                    if with_eval_runs:
                        crun = _mk_eval_run(
                            cand, f"candidate {ci}", q, t + timedelta(minutes=5 + ci * 10)
                        )
                        new_score = _variant_score(crun, crun.variants.first())
                        _align(cand, new_score, cscore)
                        cscore = new_score
                        cand.eval_run = crun
                    cand.score = cscore
                    if target_model:
                        cand.scores = {
                            "measurement": {
                                "coverage_rate": rnd(0.92, 1.0),
                                "excluded_commands": 0,
                                "uncovered_card_claims": [],
                            }
                        }
                        iter_models[target_model] = cscore
                        by_model[target_model] = cscore
                    cand.save()
                    cand_pairs.append((cand.pk, t + timedelta(minutes=5 + ci * 10)))
                    iter_scores[str(cand.pk)] = cscore
                    if cscore > iter_best:
                        iter_best, iter_best_cand = cscore, cand
                scores[str(i)] = iter_scores
                iteration.scores = {
                    "best": iter_best,
                    **({"models": iter_models} if iter_models else {}),
                }
                iteration.save(update_fields=["scores"])
                if iter_best > scores["best"] + 1.0:
                    scores["best"] = iter_best
                    winner = iter_best_cand
                    stalled = 0
                else:
                    stalled += 1
            if by_model:
                scores["by_model"] = by_model
                scores["models"] = by_model
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
            if status == "completed" and winner is not None and mode == "optimize":
                state["winner_score"] = scores["best"]
                state["winner_candidate_id"] = str(winner.pk)
            if mode == "model_comparison" and by_model:
                best_model = max(by_model, key=by_model.get)
                incumbent_wins = base_score >= by_model[best_model]
                state["model_comparison"] = {
                    "selected_winner": best_model,
                    "selected_winner_score": by_model[best_model],
                    "incumbent_score": base_score,
                    "overall_winner": "incumbent" if incumbent_wins else best_model,
                    "incumbent_wins": incumbent_wins,
                }
            exp.state = state
            exp.save()
            backdate(OptimizerCommand, cmd_pairs)
            backdate(OptimizerCandidate, cand_pairs)
            backdate(OptimizerIteration, iter_pairs)
            OptimizerExperiment.objects.filter(pk=exp.pk).update(created_at=born, updated_at=done)
            return exp, winner

        triage_opt_subset = _from_traces(
            triage_capability, "Triage Optimiser Subset", want=12, intent="eval", born=days_ago(12)
        )
        Cell.objects.filter(pk=triage_opt_subset.active_cell.pk).update(used_at=days_ago(12))
        triage_exp, triage_winner = build_experiment(
            cap=triage_capability,
            dataset=triage_opt_subset,
            eval_set=triage_set,
            born=days_ago(12, h=-2),
            done=days_ago(11, h=-3),
            entrypoint="run_triage",
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
        kb_compare_exp, _ = build_experiment(
            cap=kb_capability,
            dataset=kb_golden,
            eval_set=kb_set,
            born=days_ago(6),
            done=days_ago(6, h=-3),
            entrypoint="answer_question",
            baseline_q=0.84,
            iteration_qs=[
                [
                    (0.79, "openai/gpt-5.6-terra"),
                    (0.86, "google/gemini-3.1-pro-preview"),
                    (0.81, "qwen/qwen3.6-235b"),
                ]
            ],
            diffs=[],
            sample_fn=kb_sample,
            mode="model_comparison",
            model_ids=[
                "openai/gpt-5.6-terra",
                "google/gemini-3.1-pro-preview",
                "qwen/qwen3.6-235b",
            ],
            num_iterations=1,
        )
        dispute_cancelled_exp, _ = build_experiment(
            cap=dispute_capability,
            dataset=dispute_golden,
            eval_set=dispute_set,
            born=days_ago(3),
            done=days_ago(3, h=-1.5),
            entrypoint="resolve_dispute",
            baseline_q=0.79,
            iteration_qs=[[0.80, 0.77, 0.81]],
            diffs=_TRIAGE_DIFFS[2:],
            sample_fn=dispute_sample,
            status="cancelled",
            num_iterations=5,
        )

        # ── Training ─────────────────────────────────────────────────────────────────────

        self.stdout.write("Creating training runs...")

        def gen_training_curves(
            total_steps, epochs, *, start_loss, end_loss, start_acc, end_acc, lr0, cut=None
        ):
            metrics_history, eval_history, checkpoints = [], [], []
            loss_series, lr_series, gn_series, acc_series = [], [], [], []
            eval_every = max(1, total_steps // (epochs * 2))
            warmup = max(1, int(total_steps * 0.05))
            last = cut or total_steps
            for step in range(10, last + 1, 10):
                frac = step / total_steps
                train_loss = round(
                    end_loss
                    + (start_loss - end_loss) * (1 - frac) ** 1.6
                    + random.uniform(-0.03, 0.03),
                    4,
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
                if step > last:
                    break
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
                        "created_at": 0,
                        "resumable": True,
                        "has_eval": True,
                        "upload_status": "uploaded",
                    }
                )
            epoch_losses = [
                {"epoch": i + 1, "train_loss": cp["train_loss"], "valid_loss": cp["valid_loss"]}
                for i, cp in enumerate(checkpoints)
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
            }

        def make_ft_job(
            *,
            job_id,
            cap,
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
            output_name,
            baseline_model,
            train_examples,
            val_examples,
            cost=None,
            minutes=None,
            status="succeeded",
            error="",
            group=None,
            percent=100.0,
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
            activity = [
                {"ts": int((started + timedelta(seconds=s)).timestamp() * 1000), "message": msg}
                for s, msg in [
                    (0, "Preparing dataset"),
                    (
                        40,
                        f"Materialised {train_examples} training, {val_examples} validation examples",
                    ),
                    (95, "Uploading training file"),
                    (170, "Loading base model…"),
                    (400, "Training started"),
                ]
            ]
            if status == "succeeded":
                activity.append(
                    {
                        "ts": int((started + timedelta(seconds=elapsed - 60)).timestamp() * 1000),
                        "message": "Training complete — registering adapter",
                    }
                )
            progress = {
                "epochs_completed": epochs
                if status == "succeeded"
                else round(epochs * percent / 100, 2),
                "tokens_processed": int(
                    train_examples * random.randint(380, 520) * epochs * percent / 100
                ),
                "trained_steps": latest["step"],
                "total_steps": total_steps,
                "percent": percent,
                "estimated_finish": None,
                "eta_seconds": None,
                "elapsed_seconds": elapsed,
                "phase": "finalizing" if status == "succeeded" else "training",
                "stage": "model_loaded",
                "download": None,
                "provider_status": status,
                "latest_train_loss": latest["train_loss"],
                "latest_eval_loss": latest_eval["eval_loss"],
                "train_loss": latest["train_loss"],
                "eval_loss": latest_eval["eval_loss"],
                "learning_rate": latest["lr"],
                "token_accuracy": latest["token_accuracy"],
                "eval_token_accuracy": latest_eval["eval_token_accuracy"],
                "current_epoch": float(epochs) * percent / 100,
                "eta_s": 0.0,
                "metrics_history": metrics_history,
                "eval_history": eval_history,
                "activity": activity,
                "metrics": metrics,
                "checkpoints": checkpoints,
            }
            result = (
                {
                    "epoch_losses": epoch_losses,
                    "model": output_name,
                    "metrics": metrics,
                    "checkpoints": checkpoints,
                }
                if status == "succeeded"
                else {}
            )
            job = FinetuningJob.objects.create(
                id=job_id,
                project=project,
                capability=cap,
                dataset=dataset,
                cell=dataset.active_cell,
                validation_enabled=True,
                validation_split_ratio=0.2,
                split_method="random",
                triggered_by=jonas,
                eval_dataset=eval_dataset,
                eval_cell=eval_dataset.active_cell,
                eval_set=eval_set,
                name=name,
                use_case=use_case,
                group_id=group,
                model_tier=tier,
                provider="modal",
                base_model=base_model,
                hyperparameters=hyper,
                baseline_model=baseline_model,
                status=status,
                remote_job_id=f"fc-{hexid(6)}",
                output_model_name=output_name if status == "succeeded" else "",
                progress=progress,
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
                ("status_change", "Submitted to Modal", {"status": "running"}, 4),
            ]
            for i, s in enumerate(
                [total_steps // 4, total_steps // 2, (3 * total_steps) // 4, total_steps]
            ):
                if s > latest["step"]:
                    break
                near = min(metrics_history, key=lambda r: abs(r["step"] - s))
                events.append(
                    (
                        "progress",
                        f"step {near['step']}",
                        {
                            "step": near["step"],
                            "train_loss": near["train_loss"],
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
            elif status == "cancelled":
                events.append(
                    (
                        "status_change",
                        "Cancelled by jonas@ledgerline.dev",
                        {"status": "cancelled"},
                        45,
                    )
                )
            else:
                events.append(("error", error, {"error": error}, 30))
            ev_pairs = []
            for etype, msg, data, minute in events:
                ev = FinetuningJobEvent.objects.create(
                    job=job, event_type=etype, message=msg, data=data
                )
                ev_pairs.append((ev.pk, started + timedelta(minutes=minute)))
            backdate(FinetuningJobEvent, ev_pairs)
            return job

        Cell.objects.filter(
            pk__in=[triage_train.active_cell.pk, dispute_train.active_cell.pk]
        ).update(used_at=days_ago(10))

        ft_triage = make_ft_job(
            job_id=FT_TRIAGE_JOB_ID,
            cap=triage_capability,
            dataset=triage_train,
            eval_dataset=triage_golden,
            eval_set=triage_set,
            name="triage qwen3-4b",
            use_case="Own the triage classifier; cut the per-ticket cost of the frontier call.",
            base_model="Qwen/Qwen3-4B",
            tier="compact",
            born=days_ago(9),
            done=days_ago(9, h=-1.4),
            epochs=3,
            total_steps=252,
            curves=gen_training_curves(
                252, 3, start_loss=1.82, end_loss=0.36, start_acc=0.63, end_acc=0.92, lr0=1e-4
            ),
            hyper=_hyper(3, 1e-4, 4, 4096, 16),
            output_name=TRIAGE_MODEL_ID,
            baseline_model="openai/gpt-5.6-sol",
            train_examples=270,
            val_examples=68,
            cost=1.42,
            minutes=26,
            group=TRIAGE_GROUP,
        )
        ft_triage_alt = make_ft_job(
            job_id=FT_TRIAGE_ALT_JOB_ID,
            cap=triage_capability,
            dataset=triage_train,
            eval_dataset=triage_golden,
            eval_set=triage_set,
            name="triage llama-3.2-3b",
            use_case="Own the triage classifier; cut the per-ticket cost of the frontier call.",
            base_model="meta-llama/Llama-3.2-3B-Instruct",
            tier="compact",
            born=days_ago(9),
            done=days_ago(9, h=-1.0),
            epochs=3,
            total_steps=252,
            curves=gen_training_curves(
                252,
                3,
                start_loss=1.95,
                end_loss=0.52,
                start_acc=0.59,
                end_acc=0.88,
                lr0=1e-4,
                cut=190,
            ),
            hyper=_hyper(3, 1e-4, 4, 4096, 16),
            output_name="",
            baseline_model="openai/gpt-5.6-sol",
            train_examples=270,
            val_examples=68,
            cost=0.98,
            minutes=19,
            status="cancelled",
            group=TRIAGE_GROUP,
            percent=75.4,
        )
        ft_dispute = make_ft_job(
            job_id=FT_DISPUTE_JOB_ID,
            cap=dispute_capability,
            dataset=dispute_train,
            eval_dataset=dispute_golden,
            eval_set=dispute_set,
            name="dispute-resolver 8B",
            use_case="Own the dispute-resolution model and lock in the policy behaviour learned from production.",
            base_model="meta-llama/Llama-3.1-8B-Instruct",
            tier="small",
            born=days_ago(14),
            done=days_ago(14, h=-2.4),
            epochs=3,
            total_steps=372,
            curves=gen_training_curves(
                372, 3, start_loss=1.87, end_loss=0.41, start_acc=0.63, end_acc=0.89, lr0=2e-4
            ),
            hyper=_hyper(3, 2e-4, 3, 8192, 32),
            output_name=DISPUTE_MODEL_ID,
            baseline_model="anthropic/claude-sonnet-5",
            train_examples=321,
            val_examples=81,
            cost=2.43,
            minutes=37,
        )

        mr_ft_triage = ModelRef.objects.create(
            project=project,
            label="ft qwen3-4b (triage)",
            provider="custom",
            model_id=TRIAGE_MODEL_ID,
            finetuning_job=ft_triage,
            params={"max_tokens": None},
        )
        mr_ft_dispute = ModelRef.objects.create(
            project=project,
            label="ft llama-3.1-8b (disputes)",
            provider="custom",
            model_id=DISPUTE_MODEL_ID,
            finetuning_job=ft_dispute,
            params={"max_tokens": None},
        )

        def bench_runs(
            *, prefix, dataset, eval_set, baseline, final, born, n_samples, sample_fn, use
        ):
            """One single-variant run per FinetuningJobEval, the shape the monitor syncs from."""
            members = list(eval_set.members.filter(role="generative"))
            base_run, _ = make_eval_run(
                name=f"{prefix} — baseline",
                description="Baseline judge eval for the fine-tune.",
                dataset=dataset,
                eval_set=eval_set,
                members=members,
                variants=[baseline],
                born=born,
                n_samples=n_samples,
                sample_fn=sample_fn,
                use=use,
            )
            final_run, _ = make_eval_run(
                name=f"{prefix} — final",
                description="Final judge eval for the fine-tune.",
                dataset=dataset,
                eval_set=eval_set,
                members=members,
                variants=[final],
                born=born + timedelta(minutes=20),
                n_samples=n_samples,
                sample_fn=sample_fn,
                use=False,
            )
            return base_run, final_run

        triage_bench = bench_runs(
            prefix="Triage FT benchmark — qwen3-4b vs incumbent",
            dataset=triage_golden,
            eval_set=triage_set,
            baseline=(
                "baseline · gpt-5.6-sol",
                "openai/gpt-5.6-sol",
                mr_sol,
                "generate",
                True,
                0.73,
            ),
            final=("final · ft qwen3-4b", TRIAGE_MODEL_ID, mr_ft_triage, "generate", False, 0.85),
            born=days_ago(9, h=-2),
            n_samples=60,
            sample_fn=triage_sample,
            use=False,
        )
        dispute_bench = bench_runs(
            prefix="Dispute FT benchmark — llama-3.1-8b vs incumbent",
            dataset=dispute_golden,
            eval_set=dispute_set,
            baseline=(
                "baseline · claude-sonnet-5",
                "anthropic/claude-sonnet-5",
                mr_sonnet,
                "generate",
                True,
                0.76,
            ),
            final=(
                "final · ft llama-3.1-8b",
                DISPUTE_MODEL_ID,
                mr_ft_dispute,
                "generate",
                False,
                0.82,
            ),
            born=days_ago(14, h=-3),
            n_samples=36,
            sample_fn=dispute_sample,
            use=True,
        )

        def _class_metrics(quality):
            labels = [
                "oncall-payments",
                "oncall-platform",
                "billing-support",
                "support-general",
                "risk-ops",
                "integrations",
                "product",
            ]
            support = [14, 9, 6, 13, 5, 6, 7]
            matrix = []
            for i, n in enumerate(support):
                row = [0] * len(labels)
                correct = round(n * clampq(quality, 0.06))
                row[i] = correct
                for _ in range(n - correct):
                    row[(i + random.choice([1, 3])) % len(labels)] += 1
                matrix.append(row)
            classes = []
            total_correct = 0
            for i, label in enumerate(labels):
                tp = matrix[i][i]
                fp = sum(matrix[r][i] for r in range(len(labels))) - tp
                precision = tp / (tp + fp) if tp + fp else 0.0
                recall = tp / support[i]
                f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
                classes.append(
                    {
                        "label": label,
                        "precision": round(precision, 3),
                        "recall": round(recall, 3),
                        "f1": round(f1, 3),
                        "support": support[i],
                    }
                )
                total_correct += tp
            n = sum(support)
            macro = {
                k: round(sum(c[k] for c in classes) / len(classes), 3)
                for k in ("precision", "recall", "f1")
            }
            weighted = {
                k: round(sum(c[k] * c["support"] for c in classes) / n, 3)
                for k in ("precision", "recall", "f1")
            }
            acc = round(total_correct / n, 3)
            return {
                "classes": classes,
                "aggregates": {
                    "accuracy": acc,
                    "n": n,
                    "macro": macro,
                    "micro": {"precision": acc, "recall": acc, "f1": acc},
                    "weighted": weighted,
                },
                "confusion_matrix": {"labels": labels, "matrix": matrix},
            }

        def _job_evals(job, runs, baseline_id, final_id, born, *, class_quality=None):
            def agg(run):
                mean = run.scores.aggregate(mean=Avg("value"))["mean"]
                return round(mean, 6) if mean is not None else None

            base_score, final_score = agg(runs[0]), agg(runs[1])
            rows = []
            for kind, run, model_id, score, delta, q in (
                ("baseline", runs[0], baseline_id, base_score, None, 0.74),
                (
                    "final",
                    runs[1],
                    final_id,
                    final_score,
                    round(final_score - base_score, 6)
                    if final_score is not None and base_score is not None
                    else None,
                    0.9,
                ),
            ):
                row = FinetuningJobEval.objects.create(
                    job=job,
                    eval_run=run,
                    kind=kind,
                    status="completed",
                    model_id=model_id,
                    aggregate_score=score,
                    baseline_delta=delta,
                    class_metrics=_class_metrics(q) if class_quality else None,
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
                    "class_metrics": r.class_metrics,
                    "eval_run_id": str(r.eval_run_id),
                    "error_message": None,
                    "created_at": born.isoformat(),
                    "updated_at": born.isoformat(),
                }
                for r in rows
            ]
            job.save(update_fields=["progress"])
            FinetuningJob.objects.filter(pk=job.pk).update(updated_at=job.completed_at)

        _job_evals(
            ft_triage,
            triage_bench,
            "openai/gpt-5.6-sol",
            TRIAGE_MODEL_ID,
            days_ago(9, h=-2),
            class_quality=True,
        )
        _job_evals(
            ft_dispute,
            dispute_bench,
            "anthropic/claude-sonnet-5",
            DISPUTE_MODEL_ID,
            days_ago(14, h=-3),
        )

        # ── Serving ──────────────────────────────────────────────────────────────────────

        self.stdout.write("Deploying models and generating inference traffic...")

        dm_triage = DeployedModel.objects.create(
            finetuning_job=ft_triage,
            project=project,
            model_id=TRIAGE_MODEL_ID,
            status="ready",
            quantization="bf16",
            base_model_id="Qwen/Qwen3-4B",
            gpu_type="L4",
            is_lora=True,
            lora_rank=16,
            weights_path="/weights/base/Qwen--Qwen3-4B",
            adapter_path=f"/weights/adapters/{TRIAGE_MODEL_ID}",
            checkpoint_hash=hexid(32),
            max_model_len=4096,
            num_parameters=4_022_468_096,
            sla_tier="hot",
            inference_url=MODAL_URL.format(worker="l4-vllm", model=TRIAGE_MODEL_ID),
            deployed_at=days_ago(9, h=-1.5),
            status_changed_at=days_ago(9, h=-1.5),
        )
        DeployedModel.objects.filter(pk=dm_triage.pk).update(created_at=days_ago(9, h=-1.4))
        dm_dispute = DeployedModel.objects.create(
            finetuning_job=ft_dispute,
            project=project,
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
            deployed_at=days_ago(14, h=-2.6),
            status_changed_at=days_ago(14, h=-2.6),
        )
        DeployedModel.objects.filter(pk=dm_dispute.pk).update(created_at=days_ago(14, h=-2.5))
        # The dispute model went live behind the capability alias on day 12.
        Capability.objects.filter(pk=dispute_capability.pk).update(active_model=dm_dispute)

        call_rows, call_times, ledger_rows, ledger_times = [], [], [], []

        def gen_calls(dm, day, n):
            for _ in range(n):
                t = business_hour(day)
                if t > NOW:
                    continue
                cold = random.random() < 0.03
                pt, ct = random.randint(250, 1900), random.randint(40, 380)
                latency = rnd(15000, 38000, 1) if cold else rnd(700, 3800, 1)
                cost = round((latency / 1000) * L4_USD_PER_SECOND, 6)
                call = InferenceCall(
                    deployed_model=dm,
                    project_id=project.pk,
                    prompt_tokens=pt,
                    completion_tokens=ct,
                    cost=cost,
                    tokens_per_second=None if cold else round(ct / (latency / 1000), 1),
                    latency_ms=latency,
                    is_cold=cold,
                )
                call_rows.append(call)
                call_times.append(t)
                ledger_rows.append(
                    BillingTelemetry(
                        user=owner,
                        project_id=project.pk,
                        amount=Decimal(str(-cost)),
                        service=BillingService.INFERENCE_FT_MODEL,
                        idempotency_key=f"inference-ft:{call.pk}",
                        metadata={
                            "model": dm.model_id,
                            "prompt_tokens": pt,
                            "completion_tokens": ct,
                        },
                    )
                )
                ledger_times.append(t)

        for i in range(15):
            day = days_ago(14 - i)
            gen_calls(dm_dispute, day, daily_volume(120, i + 16, day.weekday()))
        for i in range(10):
            day = days_ago(9 - i)
            gen_calls(dm_triage, day, daily_volume(220, i + 21, day.weekday()))
        _wall = timezone.now()
        for secs in (95, 61, 34, 12):
            pt, ct = random.randint(400, 1400), random.randint(60, 260)
            latency = rnd(600, 2400, 1)
            cost = round((latency / 1000) * L4_USD_PER_SECOND, 6)
            call_rows.append(
                InferenceCall(
                    deployed_model=dm_triage,
                    project_id=project.pk,
                    prompt_tokens=pt,
                    completion_tokens=ct,
                    cost=cost,
                    tokens_per_second=round(ct / (latency / 1000), 1),
                    latency_ms=latency,
                    is_cold=False,
                )
            )
            call_times.append(_wall - timedelta(seconds=secs))

        InferenceCall.objects.bulk_create(call_rows, batch_size=1000)
        backdate(InferenceCall, list(zip((c.pk for c in call_rows), call_times, strict=True)))
        BillingTelemetry.objects.bulk_create(ledger_rows, batch_size=1000)
        backdate(
            BillingTelemetry,
            list(zip((r.pk for r in ledger_rows), ledger_times, strict=True)),
            column="timestamp",
        )

        for job in (ft_triage, ft_triage_alt, ft_dispute):
            if job.cost_usd is None:
                continue
            row = BillingTelemetry.objects.create(
                user=owner,
                project=project,
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
        for ds, days_, amt in (
            (triage_train, 19, "0.4180000"),
            (triage_golden, 21, "0.2260000"),
            (split_eval, 2, "0.1930000"),
        ):
            row = BillingTelemetry.objects.create(
                user=owner,
                project=project,
                amount=Decimal("-" + amt),
                service=BillingService.DATA_WORKSHOP,
                idempotency_key=f"data-workshop:{ds.pk}:seed",
                metadata={
                    "dataset_id": str(ds.pk),
                    "engine": "openrouter",
                    "model": "anthropic/claude-sonnet-5",
                },
            )
            backdate(BillingTelemetry, [(row.pk, days_ago(days_, h=-1))], column="timestamp")
        row = BillingTelemetry.objects.create(
            user=owner,
            amount=Decimal("50.0000000"),
            service=BillingService.STRIPE_TOPUP,
            idempotency_key="topup:cs_live_seed_a1B2c3",
            metadata={"checkout_session_id": "cs_live_seed_a1B2c3"},
        )
        backdate(BillingTelemetry, [(row.pk, days_ago(10))], column="timestamp")

        for user_, category, text, days_, read in [
            (
                amara,
                "love",
                "The live trace scores caught a mis-routing regression before our SLA dashboard did.",
                17,
                True,
            ),
            (
                jonas,
                "improvement",
                "Training page: surface the eval-vs-train loss gap directly, we watch for overfitting on every run.",
                6,
                False,
            ),
        ]:
            f = Feedback.objects.create(user=user_, category=category, feedback=text, read=read)
            Feedback.objects.filter(pk=f.pk).update(created_at=days_ago(days_))

        # ── Summary ──────────────────────────────────────────────────────────────────────

        self.stdout.write("\nSeed complete — Support Copilot.")
        for label, count in [
            ("Capabilities", Capability.objects.filter(project=project).count()),
            ("Tasks", Behaviour.objects.filter(project=project).count()),
            ("Spans", Span.objects.filter(project=project).count()),
            ("Traces", Span.objects.filter(project=project, parent_span_id__isnull=True).count()),
            ("Sessions", Conversation.objects.filter(project=project).count()),
            ("Task executions", TaskExecution.objects.filter(project=project).count()),
            ("Verdicts", Verdict.objects.filter(project=project).count()),
            ("Datasets", Dataset.objects.filter(project=project).count()),
            ("Cells", Cell.objects.filter(dataset__project=project).count()),
            ("Evaluators", Evaluator.objects.filter(project=project).count()),
            ("Eval runs", EvalRun.objects.filter(project=project).count()),
            ("Scores", Score.objects.filter(project=project).count()),
            ("Optimiser runs", OptimizerExperiment.objects.filter(project=project).count()),
            ("Training jobs", FinetuningJob.objects.filter(project=project).count()),
            ("Deployed models", DeployedModel.objects.filter(project=project).count()),
            ("Inference calls", InferenceCall.objects.filter(project=project).count()),
            ("Ledger entries", BillingTelemetry.objects.filter(user=owner).count()),
        ]:
            self.stdout.write(f"   {label:18}: {count}")
        for ds in seed_datasets:
            ds.refresh_from_db()
            head = ds.active_cell
            self.stdout.write(
                f"     - {ds.name} [{ds.intent}/{ds.source_kind}] {head.rows if head else 0} rows"
            )
        self.stdout.write(
            f"\nOwner: {owner.email}  (password `password` if the account was created now)"
        )
        self.stdout.write("API keys (plaintext, shown once):")
        for label, raw in raw_keys:
            self.stdout.write(f"   {label}: {raw}")
