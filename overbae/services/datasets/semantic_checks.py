from __future__ import annotations

import hashlib
import json
import uuid
from collections import defaultdict
from typing import Literal

import pandas as pd
from django.db import transaction
from pydantic import BaseModel, ConfigDict, Field, model_validator

from overbae.core.decisions import DecisionError, question_batches
from overbae.services import chatgpt
from overbae.services.billing_ledger import ensure_credits, record_workshop_usage
from overbae.services.datasets import paths, preparation, review, rows, store
from overbae.services.datasets.context import context_fingerprint, preparation_context
from overbae.services.datasets.examples import field_value
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
        if any(
            evidence == answer
            or evidence.startswith(answer + ".")
            or answer.startswith(evidence + ".")
            for evidence in self.evidence_columns
            for answer in self.answer_columns
        ):
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
                "Missing source evidence is insufficient, not a pass. "
                "For task_alignment, the user request defines the goal; the saved preparation is the agent's interpretation. "
                "Do not introduce unstated difficulty or style requirements. A question that conflicts with the requested task is insufficient, not a failure of the example.",
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
    record_workshop_usage(
        user,
        batch["usage"],
        funding_source=batch["usage"].get("funding_source", "platform"),
        project_id=dataset.project_id,
        idempotency_key=f"semantic-check:{dataset.id}:{batch['id']}",
        metadata={"cell_id": str(cell.id), "workload": "semantic_checks", "contract": contract},
    )


def select_audit_rows(records, frame, cell, audit, limit):
    columns = sorted(
        {
            column
            for family in cell.preparation_plan.get("specification", {}).get("families", [])
            for column in family.get("coverage_columns", [])
        }
    )
    strata = defaultdict(list)
    checked = defaultdict(int)
    for original, record in zip(frame.to_dict(orient="records"), records, strict=True):
        values = []
        for column in columns:
            try:
                values.append(field_value(original, column))
            except ValueError:
                values.append(None)
        group = store.json_dumps(values)
        if _row_key(record) in audit["results"]:
            checked[group] += 1
        else:
            digest = hashlib.sha256(
                f"73491:{cell.fingerprint}:{_row_key(record)}".encode()
            ).hexdigest()
            strata[group].append((digest, record))
    for members in strata.values():
        members.sort(reverse=True, key=lambda value: value[0])
    selected, allocations = [], defaultdict(int)
    available = {key: len(value) for key, value in strata.items()}
    while len(selected) < limit and strata:
        group = min(
            strata,
            key=lambda key: (
                checked[key] + allocations[key],
                hashlib.sha256(key.encode()).hexdigest(),
            ),
        )
        selected.append(strata[group].pop()[1])
        allocations[group] += 1
        if not strata[group]:
            del strata[group]
    audit["sampling"] = {
        "method": "stratified_hash",
        "seed": 73491,
        "columns": columns,
        "total_strata": len(set(available) | set(checked)),
        "allocations": [
            {"stratum": key, "available": available[key], "selected": value}
            for key, value in allocations.items()
        ],
    }
    return selected


def evaluate_batch(
    batch, checks, context_data, *, project_id, contract, policy=None, chatgpt_session=None
):
    policy = policy or decisions.DecisionPolicy(backend="jev", min_confidence=0.9)
    state, questions = _batch_request(batch, checks, context_data)

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
            project_id=str(project_id),
        )
        resolved.parsed = _Checks(
            answers={
                key: answer.choice or "insufficient"
                for key, answer in resolved.parsed.answers.items()
            }
        )
        resolved.raw = resolved.parsed.model_dump_json()
        return resolved

    if chatgpt_session is not None:
        answers, stats = chatgpt.semantic_questions(chatgpt_session, state, questions)
        parsed = _Checks(answers=answers)
        outcome = funnel.JudgeOutcome(
            parsed=parsed,
            raw=parsed.model_dump_json(),
            stats=stats,
            judge_trace_id=uuid.uuid4().hex,
        )
    else:
        outcome = decisions.invoke(
            state,
            questions,
            convert=lambda answers: _Checks(
                answers={key: answer.choice or "insufficient" for key, answer in answers.items()}
            ),
            fallback=fallback,
            project_id=str(project_id),
            workload="workshop_semantic_checks",
            contract=contract,
            policy=policy,
            independent=True,
        )
    return outcome


def run_checks(
    dataset, cell, request: SemanticReviewRequest, *, user=None, progress=None, chatgpt_session=None
) -> dict:
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
    roots = {column if column in frame.columns else column.split(".")[0] for column in columns}
    missing = roots - set(frame.columns)
    if missing:
        raise ValueError(f"Missing check columns: {', '.join(sorted(missing))}.")
    records = [
        {
            store.SOURCE_ROW: row[store.SOURCE_ROW],
            **{column: field_value(row, column, decode_result=False) for column in sorted(columns)},
        }
        for row in json.loads(
            frame[[store.SOURCE_ROW, *sorted(roots - {store.SOURCE_ROW})]].to_json(orient="records")
        )
    ]
    context = context_fingerprint(dataset.capability)
    policy = decisions.DecisionPolicy(backend="jev", min_confidence=request.min_confidence)
    audit_policy = (
        {"backend": "chatgpt", "model": chatgpt_session.model}
        if chatgpt_session
        else policy.model_dump()
    )
    definitions = [check.model_dump() for check in request.checks]
    context_data = {
        **preparation_context(dataset.capability),
        "user_request": dataset.brief,
        "intent": dataset.intent,
        "preparation": preparation.for_cell(dataset, cell).get("specification", {}),
    }
    contract = hashlib.sha256(
        json.dumps(
            [
                definitions,
                context_data,
                audit_policy,
                decisions.ADAPTER_VERSION,
                {"account": str(chatgpt_session.account_id), "model": chatgpt_session.model}
                if chatgpt_session
                else "platform",
            ],
            sort_keys=True,
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
    selected = select_audit_rows(records, frame, cell, audit, request.max_rows)
    if selected and user is not None and chatgpt_session is None:
        ensure_credits(user)
    completed = 0
    report = previous
    for batch in _row_batches(selected, request.checks, context_data):
        outcome = evaluate_batch(
            batch,
            request.checks,
            context_data,
            project_id=dataset.project_id,
            contract=contract,
            policy=policy,
            chatgpt_session=chatgpt_session,
        )
        _, questions = _batch_request(batch, request.checks, context_data)
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
                        "policy": audit_policy,
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
