from __future__ import annotations

import hashlib
import json
import uuid
from typing import Literal

import pandas as pd
from django.db import transaction
from pydantic import BaseModel, ConfigDict, Field, model_validator

from overbae.core.decisions import DecisionError, question_batches
from overbae.services.billing_ledger import charge_llm_usage, ensure_credits
from overbae.services.datasets import paths, review, rows, store
from overbae.services.datasets.context import context_fingerprint, preparation_context
from overbae.services.eval import decisions, funnel

MAX_ROWS_PER_CALL = 200


class SemanticCheck(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=200)
    question: str = Field(min_length=1, max_length=4000)
    evidence_columns: list[str] = Field(min_length=1, max_length=30)
    answer_columns: list[str] = Field(default_factory=list, max_length=30)

    @model_validator(mode="after")
    def independent_evidence(self):
        if self.name == store.SOURCE_ROW:
            raise ValueError("source_row is reserved for row identity.")
        if set(self.evidence_columns) & set(self.answer_columns):
            raise ValueError("Answer columns cannot also be independent evidence columns.")
        if self.name == "answer_support" and not self.answer_columns:
            raise ValueError(
                "Answer support requires answer columns and separate evidence columns."
            )
        return self


class SemanticReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: str = ""
    checks: list[SemanticCheck] = Field(min_length=1, max_length=12)
    max_rows: int = Field(default=MAX_ROWS_PER_CALL, ge=1, le=MAX_ROWS_PER_CALL)
    min_confidence: float = Field(default=0.9, ge=0, le=1, allow_inf_nan=False)

    @model_validator(mode="after")
    def unique_checks(self):
        if len({check.name for check in self.checks}) != len(self.checks):
            raise ValueError("Semantic check names must be unique.")
        return self


class _Checks(BaseModel):
    answers: dict[str, Literal["pass", "fail", "insufficient"]]


def _row_key(row: dict) -> str:
    return json.dumps(row[store.SOURCE_ROW], ensure_ascii=False, sort_keys=True)


def _batch_request(batch, checks, context_data):
    questions = {}
    state_rows = {}
    for index, row in enumerate(batch):
        state_rows[str(index)] = row
        for number, check in enumerate(checks):
            questions[f"r{index}_c{number}"] = decisions.decision_question(
                f"Judge only rows['{index}'] against the declared task context. "
                f"Question: {check.question}\n"
                f"Independent evidence columns: {json.dumps(check.evidence_columns)}. "
                f"Answer columns being checked: {json.dumps(check.answer_columns)}. "
                "Never use the answer as evidence for itself. Do not borrow evidence from other rows. "
                "Missing source evidence is insufficient, not a pass.",
            )
    return {"task_context": context_data, "rows": state_rows}, questions


def _row_batches(selected, checks, context_data):
    batch = []
    for row in selected:
        candidate = [*batch, row]
        state, questions = _batch_request(candidate, checks, context_data)
        try:
            fits = len(question_batches(state, questions)) == 1
        except DecisionError as exc:
            if exc.reason not in {"context_budget", "invalid_questions"}:
                raise
            fits = False
        if batch and not fits:
            yield batch
            batch = [row]
        else:
            batch = candidate
    if batch:
        yield batch


def _charge_batch(user, dataset, cell, batch, contract):
    charge_llm_usage(
        user,
        batch["usage"],
        service="data-workshop",
        project_id=dataset.project_id,
        idempotency_key=f"semantic-check:{dataset.id}:{batch['id']}",
        metadata={"cell_id": str(cell.id), "workload": "semantic_checks", "contract": contract},
    )


def run_checks(dataset, cell, request: SemanticReviewRequest, *, user=None, progress=None) -> dict:
    if cell.dataset_id != dataset.id:
        raise ValueError("The version belongs to a different dataset.")
    cell.refresh_from_db(fields=["quality_report", "fingerprint"])
    rows.verify(cell)
    frame = store.read_frame(paths.cell_path(dataset.id, cell.id))
    if not frame[store.SOURCE_ROW].is_unique:
        raise ValueError("Semantic checks require unique source row identities.")
    columns = {
        column
        for check in request.checks
        for column in [*check.evidence_columns, *check.answer_columns]
    }
    missing = columns - set(frame.columns)
    if missing:
        raise ValueError(f"Missing check columns: {', '.join(sorted(missing))}.")
    context = context_fingerprint(dataset.capability)
    policy = decisions.DecisionPolicy(backend="jev", min_confidence=request.min_confidence)
    definitions = [check.model_dump() for check in request.checks]
    contract = hashlib.sha256(
        json.dumps(
            [definitions, policy.model_dump(), decisions.ADAPTER_VERSION], sort_keys=True
        ).encode()
    ).hexdigest()
    previous = cell.quality_report or {}
    expected_audit = previous.get("semantic_audit")
    audit = previous.get("semantic_audit") or {}
    if not (
        previous.get("fingerprint") == cell.fingerprint
        and previous.get("context_fingerprint") == context
        and previous.get("intent") == dataset.intent
        and audit.get("contract") == contract
    ):
        audit = {
            "method": "semantic_decisions",
            "contract": contract,
            "definitions": definitions,
            "results": {},
            "batches": [],
        }
    else:
        audit = json.loads(json.dumps(audit))
    for saved in audit["batches"]:
        _charge_batch(user, dataset, cell, saved, contract)
    records = json.loads(
        frame[[store.SOURCE_ROW, *sorted(columns - {store.SOURCE_ROW})]].to_json(orient="records")
    )
    selected = [row for row in records if _row_key(row) not in audit["results"]][: request.max_rows]
    context_data = preparation_context(dataset.capability)
    if selected and user is not None:
        ensure_credits(user)
    completed = 0
    report = previous
    for batch in _row_batches(selected, request.checks, context_data):
        state, questions = _batch_request(batch, request.checks, context_data)

        def fallback(state=state, questions=questions):
            prompt = json.dumps(
                {
                    "state": state,
                    "questions": {key: q.model_dump() for key, q in questions.items()},
                },
                ensure_ascii=False,
            )
            if len(prompt.encode()) > 350_000:
                parsed = _Checks(answers=dict.fromkeys(questions, "insufficient"))
                return funnel.JudgeOutcome(
                    parsed=parsed,
                    raw=parsed.model_dump_json(),
                    stats={"response_cost": 0.0, "response_ms": 0},
                    judge_trace_id=uuid.uuid4().hex,
                )
            resolved = decisions.resolve_questions(
                state,
                questions,
                project_id=str(dataset.project_id),
            )
            resolved.parsed = _Checks(
                answers={
                    key: answer.choice or "insufficient"
                    for key, answer in resolved.parsed.answers.items()
                }
            )
            resolved.raw = resolved.parsed.model_dump_json()
            return resolved

        outcome = decisions.invoke(
            state,
            questions,
            convert=lambda answers: _Checks(
                answers={key: answer.choice or "insufficient" for key, answer in answers.items()}
            ),
            fallback=fallback,
            project_id=str(dataset.project_id),
            workload="workshop_semantic_checks",
            contract=contract,
            policy=policy,
            independent=True,
        )
        checked = getattr(outcome.parsed, "answers", {}) or {}
        if set(checked) != set(questions):
            checked = dict.fromkeys(questions, "insufficient")
        for index, row in enumerate(batch):
            audit["results"][_row_key(row)] = {
                check.name: {"pass": True, "fail": False}.get(checked[f"r{index}_c{number}"])
                for number, check in enumerate(request.checks)
            }
        audit["batches"].append(
            {
                "source_rows": [row[store.SOURCE_ROW] for row in batch],
                "id": outcome.judge_trace_id,
                "decision": outcome.stats.get("decision", {}),
                "usage": {key: value for key, value in outcome.stats.items() if key != "decision"},
            }
        )
        measured = pd.DataFrame(
            [
                {
                    store.SOURCE_ROW: row[store.SOURCE_ROW],
                    **{
                        check.name: audit["results"].get(_row_key(row), {}).get(check.name)
                        for check in request.checks
                    },
                }
                for row in records
            ],
            columns=[store.SOURCE_ROW, *(check.name for check in request.checks)],
        )
        try:
            with transaction.atomic():
                report = review.record_quality_results(
                    dataset,
                    cell,
                    [{"name": check.name, "evidence": check.question} for check in request.checks],
                    measured,
                    audit={
                        "method": "semantic_decisions",
                        "contract": contract,
                        "policy": policy.model_dump(),
                    },
                    reviewer="semantic_decision_engine",
                    context=context,
                    semantic_audit=audit,
                    expected_semantic_audit=expected_audit,
                    original=frame,
                )
                _charge_batch(user, dataset, cell, audit["batches"][-1], contract)
        except Exception:
            # Failed persistence still incurred provider spend; charge outside the rollback.
            _charge_batch(user, dataset, cell, audit["batches"][-1], contract)
            raise
        expected_audit = json.loads(json.dumps(audit))
        completed += len(batch)
        if progress:
            progress(completed, len(selected))
    return {
        "quality_report": review.summary(report),
        "processed_rows": len(audit["results"]),
        "total_rows": len(frame),
        "remaining_rows": len(frame) - len(audit["results"]),
    }
