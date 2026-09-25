from unittest.mock import Mock

import pytest
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import DatabaseError
from pydantic import ValidationError

from overbae.core import decisions as transport
from overbae.models import BillingTelemetry, Cell, Dataset, Project
from overbae.services.datasets import land, paths, review, semantic_checks, store
from overbae.services.datasets.context import context_fingerprint

pytestmark = pytest.mark.django_db


@pytest.fixture
def dataset():
    project = Project.objects.create(name="Decision checks", slug="decision-checks")
    dataset = Dataset.objects.create(project=project, name="Evidence", intent="eval")
    land.land_rows(
        dataset,
        [
            {"input": "The box is blue.", "expected_output": "blue"},
            {"input": "The box is green.", "expected_output": "red"},
        ],
    )
    dataset.refresh_from_db()
    return dataset


@pytest.fixture
def provider(monkeypatch):
    cache.clear()

    def invoke(body, **kwargs):
        answers = {}
        for key, question in body["questions"].items():
            row = body["state"]["rows"][key.split("_")[0][1:]]
            choice = "pass" if row["expected_output"] == "blue" else "fail"
            answers[key] = {
                "type": "choice",
                "choice": choice,
                "confidence": 0.99,
                "probabilities": {
                    option: float(option == choice) for option in question["criteria"]
                },
            }
        return {
            "model": "typesafe/jev-1.13-test",
            "answers": answers,
            "usage": {"input_tokens": 10, "output_tokens": 0, "cost": 0.00001},
        }

    call = Mock(side_effect=invoke)
    monkeypatch.setattr(transport, "_request", call)
    return call


def request(max_rows=200):
    return semantic_checks.SemanticReviewRequest(
        checks=[
            semantic_checks.SemanticCheck(
                name="answer_support",
                question="Is the expected answer supported by the input?",
                evidence_columns=["input"],
                answer_columns=["expected_output"],
            )
        ],
        max_rows=max_rows,
    )


def test_check_records_real_rows_without_changing_the_dataset(dataset, provider):
    cell = dataset.active_cell
    before = store.read_frame(paths.cell_path(dataset.id, cell.id))
    result = semantic_checks.run_checks(dataset, cell, request())
    check = result["quality_report"]["checks"][0]
    assert check["rows_checked"] == 2
    assert check["rows_failed"] == 1
    assert check["rows_unknown"] == 0
    assert result["remaining_rows"] == 0
    assert review.same_frame(before, store.read_frame(paths.cell_path(dataset.id, cell.id)))
    assert dataset.cells.count() == 1
    assert not review.readiness(dataset, cell)["quality_passed"]
    assert "results" not in result["quality_report"]["semantic_audit"]


def test_partial_audit_preserves_unknown_coverage_and_resumes(dataset, provider):
    first = semantic_checks.run_checks(dataset, dataset.active_cell, request(max_rows=1))
    assert first["remaining_rows"] == 1
    assert first["quality_report"]["checks"][0]["rows_unknown"] == 1
    second = semantic_checks.run_checks(dataset, dataset.active_cell, request(max_rows=1))
    assert second["remaining_rows"] == 0
    third = semantic_checks.run_checks(dataset, dataset.active_cell, request(max_rows=1))
    assert third["processed_rows"] == 2
    assert provider.call_count == 2


def test_provider_failure_uses_closed_shared_question_resolution(dataset, provider, monkeypatch):
    provider.side_effect = transport.DecisionError("provider_timeout")

    def generate(prompt, response_format, **kwargs):
        assert response_format.__name__ == "QuestionResolution"
        parsed = response_format.model_validate(
            {
                "answers": {
                    "r0_c0": {"reasoning": "Blue is stated.", "choice": "pass"},
                    "r1_c0": {"reasoning": "Green contradicts red.", "choice": "fail"},
                }
            }
        )
        return semantic_checks.funnel.JudgeOutcome(
            parsed=parsed,
            raw="{}",
            stats={"response_cost": 0.001},
            judge_trace_id="fallback",
        )

    monkeypatch.setattr(semantic_checks.funnel, "invoke_judge", generate)
    result = semantic_checks.run_checks(dataset, dataset.active_cell, request())
    check = result["quality_report"]["checks"][0]
    assert check["rows_failed"] == 1
    assert check["rows_unknown"] == 0
    dataset.active_cell.refresh_from_db()
    assert result["remaining_rows"] == 0


def test_answer_cannot_be_its_own_evidence():
    with pytest.raises(ValidationError, match="independent evidence"):
        semantic_checks.SemanticCheck(
            name="answer_support",
            question="Supported?",
            evidence_columns=["answer"],
            answer_columns=["answer"],
        )


def test_missing_columns_fail_before_inference(dataset, provider):
    req = request()
    req.checks[0].evidence_columns = ["missing"]
    with pytest.raises(ValueError, match="Missing check columns"):
        semantic_checks.run_checks(dataset, dataset.active_cell, req)
    provider.assert_not_called()


def test_cross_dataset_cell_rejected(dataset, provider):
    other = Dataset.objects.create(project=dataset.project, name="Other", intent="eval")
    with pytest.raises(ValueError, match="different dataset"):
        semantic_checks.run_checks(other, dataset.active_cell, request())
    provider.assert_not_called()


def test_quality_save_rejects_changed_intent(dataset):
    cell = dataset.active_cell
    frame = store.read_frame(paths.cell_path(dataset.id, cell.id))[[store.SOURCE_ROW]]
    frame["answer_support"] = True
    Dataset.objects.filter(pk=dataset.pk).update(intent="train")
    with pytest.raises(ValueError, match="changed during the audit"):
        review.record_quality_results(
            dataset,
            cell,
            [{"name": "answer_support", "evidence": "test"}],
            frame,
            audit={"method": "semantic_decisions"},
            reviewer="test",
            context=context_fingerprint(None),
        )


def test_quality_save_locks_dataset_not_nullable_capability_join(dataset, provider, monkeypatch):
    lock = Mock(wraps=Dataset.objects.select_for_update)
    monkeypatch.setattr(Dataset.objects, "select_for_update", lock)
    semantic_checks.run_checks(dataset, dataset.active_cell, request())
    lock.assert_called_once_with(of=("self",))


def test_failed_audit_persistence_still_charges_provider_usage(dataset, provider, monkeypatch):
    charge = Mock()
    monkeypatch.setattr(semantic_checks, "charge_llm_usage", charge)
    monkeypatch.setattr(
        review, "record_quality_results", Mock(side_effect=DatabaseError("save failed"))
    )
    with pytest.raises(DatabaseError, match="save failed"):
        semantic_checks.run_checks(dataset, dataset.active_cell, request())
    charge.assert_called_once()
    assert charge.call_args.args[1]["response_cost"] > 0


def many_rows(dataset, count, content="The box is blue."):
    result = Dataset.objects.create(project=dataset.project, name="Batch checks", intent="eval")
    land.land_rows(result, [{"input": content, "expected_output": "blue"} for _ in range(count)])
    result.refresh_from_db()
    return result


@pytest.mark.parametrize("content", ["x" * 4000, "界" * 1500])
def test_batches_fit_actual_utf8_context_without_fallback(dataset, provider, monkeypatch, content):
    dataset = many_rows(dataset, 8, content)
    monkeypatch.setattr(
        semantic_checks.funnel,
        "invoke_judge",
        Mock(side_effect=AssertionError("Rows fit when split.")),
    )
    result = semantic_checks.run_checks(dataset, dataset.active_cell, request())
    assert result["processed_rows"] == 8
    assert provider.call_count > 1
    assert sum(len(call.args[0]["state"]["rows"]) for call in provider.call_args_list) == 8
    assert all(
        row["input"] == content
        for call in provider.call_args_list
        for row in call.args[0]["state"]["rows"].values()
    )


def test_small_rows_pack_by_context_not_a_fixed_eight_row_limit(dataset, provider):
    dataset = many_rows(dataset, 200)
    result = semantic_checks.run_checks(dataset, dataset.active_cell, request())
    assert result["remaining_rows"] == 0
    assert provider.call_count <= 5
    assert max(len(call.args[0]["state"]["rows"]) for call in provider.call_args_list) > 8


def test_interrupted_audit_resumes_durable_batches_without_rechecking(dataset, provider):
    dataset = many_rows(dataset, 200)

    def interrupt(done, total):
        assert 0 < done < total
        raise RuntimeError("Worker interrupted")

    with pytest.raises(RuntimeError, match="interrupted"):
        semantic_checks.run_checks(dataset, dataset.active_cell, request(), progress=interrupt)
    cell = dataset.active_cell
    cell.refresh_from_db()
    audit = cell.quality_report["semantic_audit"]
    assert len(audit["results"]) > 0
    assert cell.quality_report["checks"][0]["rows_unknown"] == 200 - len(audit["results"])
    completed = set(audit["results"])
    provider.reset_mock()
    result = semantic_checks.run_checks(dataset, cell, request())
    assert result["remaining_rows"] == 0
    checked_again = {
        semantic_checks._row_key(row)
        for call in provider.call_args_list
        for row in call.args[0]["state"]["rows"].values()
    }
    assert not completed.intersection(checked_again)


def test_concurrent_audit_checkpoint_cannot_be_overwritten(dataset, provider):
    cell = dataset.active_cell
    original = provider.side_effect

    def race(body, **kwargs):
        Cell.objects.filter(pk=cell.pk).update(
            quality_report={"semantic_audit": {"contract": "another audit"}}
        )
        return original(body, **kwargs)

    provider.side_effect = race
    with pytest.raises(ValueError, match="semantic audit changed"):
        semantic_checks.run_checks(dataset, cell, request())
    cell.refresh_from_db()
    assert cell.quality_report["semantic_audit"]["contract"] == "another audit"


def test_resume_reads_latest_checkpoint_even_with_stale_cell_instance(dataset, provider):
    stale = dataset.active_cell
    other = Cell.objects.get(pk=stale.pk)
    semantic_checks.run_checks(dataset, other, request(max_rows=1))
    result = semantic_checks.run_checks(dataset, stale, request(max_rows=1))
    assert result["remaining_rows"] == 0
    assert provider.call_count == 2


def test_completed_batch_billing_is_idempotent_on_resume(dataset, provider):
    user = get_user_model().objects.create_user(email="semantic-charge@example.com", password="x")
    result = semantic_checks.run_checks(dataset, dataset.active_cell, request(), user=user)
    assert result["remaining_rows"] == 0
    charges = BillingTelemetry.objects.filter(
        user=user, idempotency_key__startswith="semantic-check:"
    )
    charged = list(charges.values_list("idempotency_key", "amount"))
    assert len(charged) == 1
    semantic_checks.run_checks(dataset, dataset.active_cell, request(), user=user)
    assert list(charges.values_list("idempotency_key", "amount")) == charged
    assert provider.call_count == 1


def test_stale_result_spend_is_not_lost(dataset, provider, monkeypatch):
    original = provider.side_effect

    def change_intent(body, **kwargs):
        Dataset.objects.filter(pk=dataset.pk).update(intent="train")
        return original(body, **kwargs)

    provider.side_effect = change_intent
    charged = Mock()
    monkeypatch.setattr(semantic_checks, "charge_llm_usage", charged)
    with pytest.raises(ValueError, match="changed during the audit"):
        semantic_checks.run_checks(dataset, dataset.active_cell, request())
    charged.assert_called_once()
    assert charged.call_args.args[1]["response_cost"] == 0.00001
