import asyncio
import json
import logging
import os

import httpx

from innermind import deterministic
from innermind.innermind import DeterministicCheck, Span
from innermind.utils import (
    REASON_BELOW,
    EvaluatorResult,
    Metric,
    mean_score,
    read_expectations,
    read_input,
    read_output,
)

logger = logging.getLogger(__name__)

# Jev rejects a request whose state + question exceeds ~31k UTF-8 bytes.
JEV_STATE_LIMIT = 30_000

MAX_CONCURRENT_REQUESTS = 128
openrouter_api_key = os.environ["OPENROUTER_API_KEY"]

if not openrouter_api_key:
    raise ValueError("OPENROUTER_API_KEY is not set")

openrouter_client = httpx.AsyncClient(
    base_url="https://openrouter.ai/api/v1",
    headers={
        "Authorization": f"Bearer {openrouter_api_key}",
        "X-Title": "Overmind",
    },
)
openrouter_slots = asyncio.Semaphore(MAX_CONCURRENT_REQUESTS)


class Evaluator:
    """one of the evaluators run on a span; each stores its own result under its name"""

    name: str
    kind: str

    async def evaluate(self, span: Span) -> EvaluatorResult: ...


class OvermindEvaluator(Evaluator):
    """uses a custom language to score the span: one DeterministicCheck run in the sandbox"""

    kind = "deterministic"
    verdicts = {1.0: "yes", 0.0: "no"}

    def __init__(self, check: DeterministicCheck, trace: list[Span]):
        self.name = check.name
        self.check = check
        self.trace = trace

    async def evaluate(self, span: Span) -> EvaluatorResult:
        metric = self.metric(await deterministic.run(self.check.source, span, self.trace))
        return EvaluatorResult(self.name, self.kind, score=metric.score, metrics=[metric])

    def metric(self, result: dict) -> Metric:
        reason = str(result.get("explanation") or "")
        if result.get("outcome") == "abstained":
            return Metric(self.name, verdict="abstain", reason=reason)
        if result.get("outcome") != "scored":
            return Metric(self.name, error=reason or "script error")
        score = float(result["score"])
        return Metric(self.name, score=score, verdict=self.verdicts.get(score, ""), reason=reason)


class LLMEvaluator(Evaluator):
    """LLM as a judge: one Metric per expectation set on the span"""

    kind = "llm_judge"

    async def evaluate(self, span: Span) -> EvaluatorResult:
        expectations = read_expectations(span.attributes)
        required = {
            "intent": span.eval_attributes.get("intent"),
            "input": read_input(span.attributes),
            "output": read_output(span.attributes),
            "expectations": expectations,
        }
        missing = [name for name, value in required.items() if not value]
        if missing:
            return EvaluatorResult(self.name, self.kind, skipped="missing " + ", ".join(missing))

        metrics = list(await asyncio.gather(*(self.score(span, e) for e in expectations)))
        low = [
            metric for metric in metrics if metric.score is not None and metric.score < REASON_BELOW
        ]
        reasons = await asyncio.gather(
            *(self.explain(self.evidence(span, metric.expectation), metric) for metric in low)
        )
        for metric, reason in zip(low, reasons, strict=True):
            metric.reason = reason
        return EvaluatorResult(self.name, self.kind, score=mean_score(metrics), metrics=metrics)

    def evidence(self, span: Span, expectation: str) -> dict: ...

    async def score(self, span: Span, expectation: str) -> Metric: ...

    async def explain(self, state: dict, metric: Metric) -> str: ...


class JevEvaluator(LLMEvaluator):
    name = "jev"
    decision_model = "systemone/jev-v1"
    explanation_model = "openai/gpt-4o-mini"

    question = {
        "type": "choice",
        "instructions": "Given the intent and the input, does the output meet the expectation?",
        "criteria": {
            "yes": "The output fully meets the expectation.",
            "partial": "The output meets part of the expectation, or meets it with errors.",
            "no": "The output does not meet the expectation.",
        },
    }

    def evidence(self, span: Span, expectation: str) -> dict:
        return {
            "intent": span.eval_attributes["intent"],
            "expectation": expectation,
            "input": read_input(span.attributes),
            "output": read_output(span.attributes),
        }

    async def score(self, span: Span, expectation: str) -> Metric:
        return await self.judge(self.evidence(span, expectation), expectation)

    async def judge(self, state: dict, expectation: str) -> Metric:
        """Raises on transport errors, 429 and 5xx so the caller retries on its next run."""
        values = {"yes": 1.0, "partial": 0.5, "no": 0.0}
        if len(json.dumps(state).encode()) > JEV_STATE_LIMIT:
            return Metric(expectation, error="too large for Jev")
        async with openrouter_slots:
            resp = await openrouter_client.post(
                "/systemone",
                json={
                    "model": self.decision_model,
                    "state": state,
                    "questions": {"meets": self.question},
                },
                timeout=30,
            )
        if resp.status_code == 429 or resp.status_code >= 500:
            resp.raise_for_status()
        if not resp.is_success:
            return Metric(expectation, error=f"Jev HTTP {resp.status_code}")
        answer = resp.json()["answers"]["meets"]
        score = sum(values[choice] * p for choice, p in answer["probabilities"].items())
        return Metric(expectation, score=score, verdict=answer["choice"])

    async def explain(self, state: dict, metric: Metric) -> str:
        prompt = (
            f"An evaluator scored this {metric.score:.2f} out of 1 against its expectation.\n"
            "In one or two sentences, state the specific reason the output falls short. "
            "Name what is missing or wrong; do not restate the expectation.\n\n"
            + json.dumps(state, ensure_ascii=False, indent=1)
        )
        try:
            async with openrouter_slots:
                resp = await openrouter_client.post(
                    "/chat/completions",
                    json={
                        "model": self.explanation_model,
                        "messages": [{"role": "user", "content": prompt}],
                    },
                    timeout=30,
                )
            resp.raise_for_status()
            text = resp.json()["choices"][0]["message"]["content"] or ""
            return " ".join(text.split()) or "(empty reason)"
        except httpx.HTTPError as exc:
            return f"(reason unavailable: {type(exc).__name__})"
