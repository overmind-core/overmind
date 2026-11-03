"""Run: docker compose exec api python manage.py seed_observability [--scale 1.0] [--owner EMAIL]

Six months of production-shaped traffic for the Observability page, in its own project.
Findings come from the real checks over the generated spans, never written by hand. Every
span is final and backdated in the insert that creates it, so the sweeps have nothing to
re-drive; only deterministic checks run, so no provider is contacted.
"""

import csv
import io
import json
import random
import uuid
from collections import Counter, defaultdict
from datetime import UTC, datetime, timedelta

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db import connection, models
from django.utils import timezone

from overbae.innermind import default_set
from overbae.innermind.issues import record_occurrences
from overbae.innermind.signals import dedupe_key, group_key, signal_factory, span_end
from overbae.innermind.traffic import hour_of, rollup_hours
from overbae.models import (
    Capability,
    Conversation,
    Issue,
    IssueTrace,
    Project,
    ProjectMembership,
    SignalCursor,
    SignalEvent,
    SignalSubscription,
    Span,
)
from overbae.models.traces import usage_slice

SLUG = "ledgerline-observability"
DAYS = 180
FLUSH_TRACES = 4_000
OWNER = "frey@overmindlab.ai"

SOL = "openai/gpt-5.6-sol"
TERRA = "openai/gpt-5.6-terra"
SONNET = "anthropic/claude-sonnet-5"
TRAINED_TRIAGE = "ft-7c21e0a4-qwen3-4b"
RATES = {SOL: (3.0, 12.0), TERRA: (1.1, 4.4), SONNET: (3.0, 15.0), TRAINED_TRIAGE: (0.2, 0.6)}

GROUNDED = "The answer is grounded in the retrieved articles."
CITES = "The answer cites the article it relies on."
ROUTED = "The ticket is routed to the team that owns it."
PRIORITY = "Enterprise merchants never get a priority below medium."
POLICY = "The decision follows the dispute policy."
EVIDENCE = "The decision names the evidence it used."
REFUND_LIMIT = "The refund stays within the merchant's limit."
REFUND_ORDER = "The refund references the right order."
FIELDS = "Every invoice field is extracted from the document."
TOTALS = "The extracted totals add up."
FRAUD = "The fraud decision cites the rule it applied."
ONBOARD = "The checklist matches the merchant's country."

CAPABILITIES = {
    "triage": ("Ticket Triage", "route_ticket", 2000, 0.0, (ROUTED, PRIORITY)),
    "kb": ("KB Answerer", "answer_question", 1400, 0.35, (GROUNDED, CITES)),
    "dispute": ("Dispute Resolver", "resolve_dispute", 600, 0.3, (POLICY, EVIDENCE)),
    "refund": ("Refund Agent", "process_refund", 560, 0.1, (REFUND_LIMIT, REFUND_ORDER)),
    "invoice": ("Invoice Parser", "parse_invoice", 240, 0.0, (FIELDS, TOTALS)),
    "fraud": ("Fraud Review", "review_payment", 180, 0.0, (FRAUD,)),
    "onboarding": ("Onboarding Assistant", "guide_merchant", 200, 0.5, (ONBOARD,)),
}
PROMPTS = {
    "triage": "Route the merchant ticket to one team and set its priority.",
    "kb": "Answer from the retrieved articles and cite each one.",
    "dispute": "Decide the dispute from the evidence and the policy.",
    "refund": "Check the order and issue a refund within the limit.",
    "invoice": "Extract the fields and totals from the invoice.",
    "fraud": "Decide whether the payment needs review and cite the rule.",
    "onboarding": "Guide the merchant through the checklist for their country.",
}
TICKETS = (
    "Payout {id} has not arrived at our bank",
    "Chargeback {id} was opened against a delivered order",
    "API returns 401 for webhook {id}",
    "Our statement for {id} shows a duplicate fee",
    "Customer in checkout {id} sees a declined card",
)
QUESTIONS = (
    "How long does settlement take for batch {id}?",
    "Which currencies does account {id} support?",
    "How do I rotate the webhook secret for {id}?",
    "What is the reserve policy on plan {id}?",
)
SHARDS = ("amber", "basil", "cedar", "delta", "ember", "fjord", "gorse", "heron", "iris", "jade")


def hours_ago(hours: float) -> datetime:
    return NOW - timedelta(hours=hours)


NOW = timezone.now().replace(second=0, microsecond=0)
OUTAGE = (hours_ago(58), hours_ago(52))
# Capabilities ship over time, so early months carry less traffic and fewer failures.
LAUNCHED_DAYS_AGO = {
    "triage": DAYS,
    "kb": 170,
    "dispute": 150,
    "refund": 95,
    "invoice": 70,
    "fraud": 45,
    "onboarding": 30,
}
# One upstream failing for every capability that calls it, at the same time.
MERCHANT_OUTAGE = (hours_ago(30), hours_ago(26))


def days_old(when: datetime) -> float:
    return (NOW - when).total_seconds() / 86_400


def growth(age: float) -> float:
    """Adoption climbs from a quarter of today's traffic, with a large customer joining
    sixty days ago and a quiet summer week."""
    ramp = 0.25 + 0.55 * (1 - age / DAYS)
    joined = 0.25 if age < 60 else 0.0
    quiet = 0.7 if 80 <= age < 87 else 1.0
    return (ramp + joined) * quiet


def hexid(rng: random.Random, bytes_: int) -> str:
    return f"{rng.getrandbits(bytes_ * 8):0{bytes_ * 2}x}"


def run_check(coroutine):
    # Deterministic checks never suspend; driving them directly skips an event loop per call.
    try:
        coroutine.send(None)
    except StopIteration as stop:
        return stop.value
    raise RuntimeError("seed_observability ran a check that awaits")


class Generator:
    def __init__(self, project, capabilities, scale, rng):
        self.project = project
        self.capabilities = capabilities
        self.scale = scale
        self.rng = rng
        self.spans: list[Span] = []
        self.conversations: list[Conversation] = []
        self.groups: dict[str, list[Span]] = defaultdict(list)
        self.traces = 0
        self.total_spans = 0
        self.events = 0
        self.usage = defaultdict(lambda: Counter())
        self.subscriptions = [
            SignalSubscription(id=None, project_id=project.pk, signal_type=name)
            for name, cls in signal_factory.signals.items()
            if cls.default_enabled
        ]
        self.checks = [(sub, signal_factory.get_signal(sub)) for sub in self.subscriptions]

    def span(self, cap, trace_id, parent, *, name, kind, start, ms, attrs, error="", evals=None):
        end = start + int(ms * 1e6)
        span = Span(
            span_id=hexid(self.rng, 8),
            trace_id=trace_id,
            parent_span_id=parent,
            project_id=self.project.pk,
            capability_id=cap.pk,
            span_type=kind,
            operation=name,
            name=name,
            kind=2 if parent is None else 1,
            start_time_ns=start,
            end_time_ns=end,
            duration_ns=end - start,
            status_code=2 if error else 1,
            status_message=error,
            service_name=cap.slug,
            resource_attrs={"service.name": cap.slug, "deployment.environment": "production"},
            scope_name="overmind.sdk",
            scope_version="0.1.55",
            attributes=attrs,
            usage=usage_slice(attrs),
            eval_attributes=evals or {"version": 1, "score": None, "metrics": {}},
            received_at=datetime.fromtimestamp(end / 1e9, tz=UTC) + timedelta(seconds=2),
        )
        return span

    def llm_attrs(self, key, model, prompt, completion, ms, inp=None, out=None):
        rate_in, rate_out = RATES[model]
        cost = round((prompt * rate_in + completion * rate_out) / 1_000_000, 6)
        attrs = {
            "genai.model": model,
            "genai.provider": model.split("/")[0] if "/" in model else "overmind",
            "genai.prompt_tokens": prompt,
            "genai.completion_tokens": completion,
            "genai.total_tokens": prompt + completion,
            "genai.cost": cost,
            "genai.elapsed_seconds": round(ms / 1000, 3),
        }
        if inp is not None:
            attrs["overmind.input.data"] = [
                {"role": "system", "content": PROMPTS[key]},
                {"role": "user", "content": inp},
            ]
        if out is not None:
            attrs["overmind.output.data"] = [{"role": "assistant", "content": out}]
        return attrs

    def metrics(self, key, failing: tuple[str, ...] = (), unscorable=False):
        expectations = CAPABILITIES[key][4]
        rng = self.rng
        result = {}
        for index, text in enumerate(expectations):
            name = f"check_{index + 1}"
            if unscorable:
                result[name] = {
                    "type": "llm",
                    "expectation": text,
                    "score": None,
                    "verdict": "",
                    "reason": "",
                    "error": "Span payload has no retrieved context to judge against.",
                }
            elif text in failing:
                result[name] = {
                    "type": "llm",
                    "expectation": text,
                    "score": round(rng.uniform(0.05, 0.35), 3),
                    "verdict": "no",
                    "reason": "The output does not meet this expectation.",
                    "error": "",
                }
            else:
                result[name] = {
                    "type": "llm",
                    "expectation": text,
                    "score": round(rng.uniform(0.8, 1.0), 3),
                    "verdict": "yes",
                    "reason": "The output meets this expectation.",
                    "error": "",
                }
        return result

    def trace(self, key, cap, when, conversation, external):
        rng = self.rng
        age = days_old(when)
        trace_id = hexid(rng, 16)
        root_id = hexid(rng, 8)
        start = int(when.timestamp() * 1e9)
        offset = 0
        ticket = hexid(rng, 3)
        children = []
        failing: tuple[str, ...] = ()
        unscorable = False
        root_error = ""
        shared = {"conversation.id": external} if external else {}

        def tool(name, args, ms, error=""):
            nonlocal offset
            attrs = {
                "tool.name": name,
                "overmind.input.data": args,
                **({"tool.error": error} if error else {}),
                **shared,
            }
            children.append(
                self.span(
                    cap,
                    trace_id,
                    root_id,
                    name=name,
                    kind="tool_call",
                    start=start + offset,
                    ms=ms,
                    attrs=attrs,
                    error=error,
                )
            )
            offset += int(ms * 1e6)

        def llm(model, prompt, completion, ms, inp=None, out=None):
            nonlocal offset
            attrs = {**self.llm_attrs(key, model, prompt, completion, ms, inp, out), **shared}
            children.append(
                self.span(
                    cap,
                    trace_id,
                    root_id,
                    name=f"chat {model}",
                    kind="llm_call",
                    start=start + offset,
                    ms=ms,
                    attrs=attrs,
                )
            )
            offset += int(ms * 1e6)
            self.usage[cap.pk]["cost"] += attrs["genai.cost"]

        if key == "triage":
            text = rng.choice(TICKETS).format(id=f"tk_{ticket}")
            output: object = {"team": rng.choice(("payments", "risk", "api")), "priority": "high"}
            # The trained model replaces the routing failures it was trained on.
            model = TRAINED_TRIAGE if age < 12 else SOL if age < 150 else SONNET
            failure = 0.009 if age < 12 else 0.085
            if rng.random() < failure:
                failing = (ROUTED,) if rng.random() < 0.7 else (PRIORITY,)
            if age < 20 and rng.random() < 0.03:
                unscorable = True
            if rng.random() < 0.003:
                output = "I can't help with this ticket, please contact support."
            merchant_down = (63.7 <= age < 64 and rng.random() < 0.5) or (
                MERCHANT_OUTAGE[0] <= when <= MERCHANT_OUTAGE[1] and rng.random() < 0.7
            )
            tool(
                "lookup_merchant",
                {"ticket": f"tk_{ticket}"},
                rng.uniform(40, 160),
                "merchant service returned 503" if merchant_down else "",
            )
            llm(model, rng.randint(900, 1500), rng.randint(40, 90), rng.uniform(500, 1400), text)
            tool("search_runbooks", {"query": text}, rng.uniform(60, 240))
            llm(
                model,
                rng.randint(1100, 1800),
                rng.randint(60, 140),
                rng.uniform(600, 1800),
                None,
                json.dumps(output),
            )
        elif key == "kb":
            text = rng.choice(QUESTIONS).format(id=f"ac_{ticket}")
            model = TERRA if age < 9 else SOL if age < 100 else SONNET
            output = {"answer": "Settlement takes two business days.", "citations": ["payouts"]}
            failure = 0.11 if age < 9 else 0.008 if age < 100 else 0.02
            if rng.random() < failure:
                failing = (GROUNDED,) if rng.random() < 0.8 else (CITES,)
            if age < 1.25 and rng.random() < 0.004:
                # Example addresses: the leak check must see them; evidence redacts them.
                output = {
                    "answer": f"I checked with the account owner at owner.{ticket}@example.com.",
                    "citations": ["payouts"],
                }
            index_down = 120.5 <= age < 121 and rng.random() < 0.6
            tool(
                "search_kb",
                {"query": text},
                rng.uniform(120, 420),
                "search index timeout after 10s" if index_down else "",
            )
            tool("fetch_article", {"slug": f"article-{ticket}"}, rng.uniform(40, 130))
            llm(
                model, rng.randint(1800, 3200), rng.randint(120, 320), rng.uniform(1200, 3600), text
            )
            children[-1].attributes["overmind.output.data"] = [
                {"role": "assistant", "content": json.dumps(output)}
            ]
        elif key == "dispute":
            text = f"Dispute dp_{ticket} on a delivered order"
            output = {"decision": rng.choice(("accept", "contest")), "evidence": ["delivery"]}
            if rng.random() < 0.012:
                failing = (POLICY,)
            tool("lookup_transaction", {"dispute": f"dp_{ticket}"}, rng.uniform(40, 180))
            if OUTAGE[0] <= when <= OUTAGE[1] and rng.random() < 0.8:
                for _ in range(3):
                    tool(
                        "fetch_dispute_evidence",
                        {"dispute": f"dp_{ticket}"},
                        rng.uniform(29_000, 30_500),
                        "evidence store timeout after 30s",
                    )
                root_error = "AgentExecutionError: dispute evidence unavailable"
            else:
                tool("fetch_dispute_evidence", {"dispute": f"dp_{ticket}"}, rng.uniform(90, 360))
            if rng.random() < 0.004:
                tool(
                    "check_policy",
                    {"dispute": f"dp_{ticket}"},
                    rng.uniform(20, 60),
                    "policy cache miss",
                )
            else:
                tool("check_policy", {"dispute": f"dp_{ticket}"}, rng.uniform(30, 90))
            if rng.random() < 0.0015:
                tool(
                    "write_audit",
                    {"dispute": f"dp_{ticket}"},
                    rng.uniform(20, 60),
                    f"ledger shard {rng.choice(SHARDS)}-{rng.choice(SHARDS)} unavailable",
                )
            llm(
                SONNET,
                rng.randint(2200, 3800),
                rng.randint(180, 420),
                rng.uniform(1800, 4800),
                text,
                json.dumps(output),
            )
            # A model swap made it call a tool that doesn't exist, far more often lately.
            if rng.random() < (0.06 if age < 4 else 0.002):
                children[-1].attributes["overmind.output.data"] = [
                    {
                        "role": "assistant",
                        "content": "",
                        "tool_calls": [{"function": {"name": "escalate_to_human"}}],
                    }
                ]
        elif key in ("invoice", "fraud", "onboarding"):
            text = f"{CAPABILITIES[key][0]} request {ticket}"
            output = {"status": "done", "reference": ticket}
            rate = {"invoice": 0.14 if age < 6 else 0.015, "fraud": 0.012, "onboarding": 0.02}[key]
            if rng.random() < rate:
                failing = (CAPABILITIES[key][4][0],)
            down = MERCHANT_OUTAGE[0] <= when <= MERCHANT_OUTAGE[1] and rng.random() < 0.7
            tool(
                "lookup_merchant",
                {"request": ticket},
                rng.uniform(40, 160),
                "merchant service returned 503" if down else "",
            )
            llm(TERRA, rng.randint(700, 1400), rng.randint(60, 180), rng.uniform(500, 1600), text)
            children[-1].attributes["overmind.output.data"] = [
                {"role": "assistant", "content": json.dumps(output)}
            ]
        else:
            text = f"Refund order or_{ticket} for a damaged item"
            output = {"refunded": True, "order": f"or_{ticket}"}
            if rng.random() < (0.04 if age > 70 else 0.01):
                failing = (REFUND_LIMIT,)
            looping = age < 5 and rng.random() < 0.09
            down = MERCHANT_OUTAGE[0] <= when <= MERCHANT_OUTAGE[1] and rng.random() < 0.7
            tool(
                "lookup_merchant",
                {"order": f"or_{ticket}"},
                rng.uniform(40, 160),
                "merchant service returned 503" if down else "",
            )
            tool("lookup_order", {"order": f"or_{ticket}"}, rng.uniform(60, 200))
            llm(TERRA, rng.randint(800, 1400), rng.randint(40, 120), rng.uniform(500, 1500), text)
            if looping:
                for _ in range(3):
                    tool("lookup_order", {"order": f"or_{ticket}"}, rng.uniform(60, 200))
                    # Each pass resends the growing context, which is where the money goes.
                    llm(
                        TERRA,
                        rng.randint(9000, 16000),
                        rng.randint(400, 900),
                        rng.uniform(2500, 6000),
                    )
            slow = rng.random() < 0.005
            tool("issue_refund", {"order": f"or_{ticket}"}, rng.uniform(120, 320))
            llm(
                TERRA,
                rng.randint(900, 1500),
                rng.randint(40, 90),
                rng.uniform(14_000, 22_000) if slow else rng.uniform(500, 1500),
            )
            if (50 > age > 30 and rng.random() < 0.02) or (age < 2 and rng.random() < 0.03):
                output = '{"refunded": true, "order": '
            llm(
                TERRA,
                rng.randint(300, 500),
                rng.randint(30, 70),
                rng.uniform(300, 900),
                None,
                json.dumps(output) if isinstance(output, dict) else output,
            )

        metrics = self.metrics(key, failing, unscorable)
        scores = [metric["score"] for metric in metrics.values() if metric["score"] is not None]
        score = round(sum(scores) / len(scores), 4) if scores else None
        outcome = 0.0 if root_error else score
        trajectory = {
            "span_count": len(children) + 1,
            "score": outcome,
            "passed": None if outcome is None else outcome >= 0.5,
            "metrics": {},
        }
        root_attrs = {
            "overmind.input.data": {"request": text},
            "overmind.span.type": "entry_point",
            "overmind.status": "failed" if root_error else "success",
            **shared,
        }
        if root_error:
            root_attrs["overmind.error.message"] = root_error
        else:
            root_attrs["overmind.output.data"] = output
        root = self.span(
            cap,
            trace_id,
            None,
            name=CAPABILITIES[key][1],
            kind="entry_point",
            start=start,
            ms=offset / 1e6 + rng.uniform(20, 90),
            attrs=root_attrs,
            error=root_error,
            evals={
                "version": 1,
                "intent": text,
                "score": score,
                "metrics": metrics,
                "trajectory": trajectory,
            },
        )
        root.span_id = root_id
        spans = [root, *children]
        for span in spans:
            span.conversation = conversation
        return spans

    def session_or_trace(self, key, cap, when):
        rng = self.rng
        session_share = CAPABILITIES[key][3]
        conversation = None
        external = ""
        turns = 1
        if rng.random() < session_share:
            turns = rng.randint(2, 5)
            external = f"sess_{hexid(rng, 8)}"
            conversation = Conversation(
                project_id=self.project.pk, external_id=external, capability_id=cap.pk
            )
            self.conversations.append(conversation)
        for turn in range(turns):
            at = when + timedelta(minutes=turn * rng.uniform(1, 6))
            if at >= NOW:
                break
            spans = self.trace(key, cap, at, conversation, external)
            self.spans.extend(spans)
            self.traces += 1
            self.total_spans += len(spans)
            self.usage[cap.pk]["traces"] += 1
            self.groups[group_key(spans[0])].extend(spans)

    def flush(self, stdout):
        if not self.spans:
            return
        Conversation.objects.bulk_create(self.conversations, batch_size=2000)
        self.conversations.clear()
        copy_rows(Span, self.spans)
        hours = {hour_of(span.start_time_ns) for span in self.spans}
        events, occurrences = [], []
        for key, group in self.groups.items():
            group.sort(key=lambda span: (span.start_time_ns, span.span_id))
            for subscription, signal in self.checks:
                finding = run_check(signal.evaluate(group))
                if not finding:
                    continue
                last = finding.spans[-1][0] if finding.spans else group[-1]
                occurred = span_end(last)
                events.append(
                    SignalEvent(
                        project_id=self.project.pk,
                        capability_id=last.capability_id,
                        signal_type=signal.name,
                        conversation_id=group[0].conversation_id,
                        trace_id=last.trace_id,
                        message=finding.message,
                        details=signal.details(finding, group, key, last.capability_id),
                        dedupe_key=dedupe_key(subscription, key, occurred.date().isoformat()),
                        occurred_at=occurred,
                        seen_at=occurred,
                    )
                )
                occurrences += signal.occurrences(finding, last.capability_id, group)
        SignalEvent.objects.bulk_create(events, batch_size=1000, ignore_conflicts=True)
        record_occurrences(self.project.pk, occurrences)
        rollup_hours(self.project.pk, hours)
        self.events += len(events)
        self.spans.clear()
        self.groups.clear()
        stdout.write(f"  {self.traces:,} traces, {self.total_spans:,} spans")

    def day(self, index, stdout):
        rng = self.rng
        date = (NOW - timedelta(days=index)).replace(hour=0, minute=0)
        week = 0.45 if date.weekday() >= 5 else 1.0
        peak_hours = [h for h in range(24) for _ in range(1 + 3 * int(8 <= h <= 18))]
        for key, cap in self.capabilities.items():
            if index > LAUNCHED_DAYS_AGO[key]:
                continue
            base = CAPABILITIES[key][2] * growth(index) * week * self.scale
            volume = int(base * rng.uniform(0.9, 1.1))
            for _ in range(volume):
                when = date.replace(hour=rng.choice(peak_hours), minute=rng.randrange(60))
                if when >= NOW:
                    continue
                self.session_or_trace(key, cap, when)
                if len(self.groups) >= FLUSH_TRACES:
                    self.flush(stdout)
        self.flush(stdout)


def copy_rows(model, rows):
    """COPY sets the backdated ``received_at`` in the insert itself; ``auto_now_add`` would not."""
    fields = model._meta.concrete_fields
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    for row in rows:
        values = []
        for field in fields:
            value = getattr(row, field.attname)
            if value is None:
                values.append("\\N")
            elif isinstance(field, models.JSONField):
                values.append(json.dumps(value))
            elif isinstance(value, datetime):
                values.append(value.isoformat())
            else:
                values.append(value)
        writer.writerow(values)
    buffer.seek(0)
    columns = ", ".join(connection.ops.quote_name(field.column) for field in fields)
    with connection.cursor() as cursor:
        cursor.copy_expert(
            f"COPY {model._meta.db_table} ({columns}) FROM STDIN WITH (FORMAT csv, NULL '\\N')",
            buffer,
        )


def seed_eval_sets(capabilities: dict) -> None:
    """Each capability's expectations as its Default eval set, as a sync would write them."""
    for key, capability in capabilities.items():
        capability.improvement_metadata = {
            "eval_matrix": [
                {"name": f"check_{index + 1}", "type": "llm", "measures": text}
                for index, text in enumerate(CAPABILITIES[key][4])
            ]
        }
        capability.save(update_fields=["improvement_metadata"])
        default_set.upsert(capability)


class Command(BaseCommand):
    help = "Seed six months of traffic and findings for the Observability page."

    def add_arguments(self, parser):
        parser.add_argument("--owner", default=OWNER)
        parser.add_argument("--scale", type=float, default=1.0)

    def handle(self, *args, **options):
        rng = random.Random(20261103)
        scale = options["scale"]
        Span.objects.filter(project__slug=SLUG).delete()
        Project.objects.filter(slug=SLUG).delete()
        owner = get_user_model().objects.get(email=options["owner"])
        project = Project.objects.create(
            name="Ledgerline Agents",
            slug=SLUG,
            integration_type="sdk",
            settings={"default_timezone": "UTC", "trace_provider": "sdk", "simulated": True},
        )
        ProjectMembership.objects.create(user=owner, project=project)
        capabilities = {
            key: Capability.objects.create(
                id=uuid.UUID(int=rng.getrandbits(128), version=4),
                project=project,
                name=spec[0],
                slug=spec[0].lower().replace(" ", "-"),
                description=PROMPTS[key],
                entrypoint_fn=spec[1],
                observed=True,
            )
            for key, spec in CAPABILITIES.items()
        }
        # Set first: the beat must not read these spans while they are being written.
        SignalCursor.objects.create(project=project, spans_through=NOW, scores_through=NOW)
        seed_eval_sets(capabilities)
        generator = Generator(project, capabilities, scale, rng)
        for index in range(DAYS, -1, -1):
            generator.day(index, self.stdout)
        self.report(project, generator)

    def report(self, project, generator):
        self.stdout.write(
            f"{project.name} ({project.pk}): {generator.traces:,} traces, "
            f"{generator.total_spans:,} spans, {generator.events:,} signal events"
        )
        since = NOW - timedelta(days=7)
        traffic = dict(
            Span.objects.filter(
                project=project,
                parent_span_id__isnull=True,
                start_time_ns__gte=int(since.timestamp() * 1e9),
            )
            .values_list("capability__name")
            .annotate(models.Count("span_id"))
            .order_by()
        )
        rows = (
            Issue.objects.filter(project=project)
            .select_related("capability")
            .annotate(
                week=models.Count("traces", filter=models.Q(traces__occurred_at__gte=since)),
                total=models.Count("traces"),
            )
            .order_by("-week")
        )
        for issue in rows:
            name = issue.capability.name if issue.capability else "-"
            share = issue.week / max(traffic.get(name, 0), 1)
            self.stdout.write(
                f"{issue.cause:6} {name:16} 7d={issue.week:<6} {share:6.2%} all={issue.total:<6} "
                f"{'REGRESSED ' if issue.regressed_at else ''}{issue.title[:70]}"
            )
        self.stdout.write(
            f"Issue traces: {IssueTrace.objects.filter(issue__project=project).count():,}"
        )
