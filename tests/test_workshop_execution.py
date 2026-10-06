import json
import uuid
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from conftest import plan_fixture
from django.utils import timezone

from overbae.models import Dataset, Project, WorkshopRecord, WorkshopRun, WorkshopWorkItem
from overbae.services.datasets import (
    generation,
    generation_quality,
    generation_worker,
    land,
    operations,
    paths,
    store,
    use,
)
from overbae.services.datasets.notebook import agent
from overbae.services.datasets.partition import contamination_keys
from overbae.services.mcp.contracts.datasets import serialize_dataset_detail

pytestmark = pytest.mark.django_db


def source_dataset(rows=None, intent="train"):
    project = Project.objects.create(name="Workshop execution", slug=uuid.uuid4().hex)
    dataset = Dataset.objects.create(project=project, name="Library", intent=intent)
    land.land_rows(
        dataset,
        rows
        or [
            {"text": "The library opens at nine. ", "page": 1, "document_id": "library"},
            {"text": "It closes at five. ", "page": 1, "document_id": "library"},
            {"text": "Loans last fourteen days.", "page": 2, "document_id": "library"},
        ],
    )
    dataset.refresh_from_db()
    plan_fixture(dataset)
    return dataset


def example(seed, number, text="The library opens at nine. "):
    return {
        "seed_row": seed,
        "row": {
            "messages": [
                {
                    "role": "user",
                    "content": f"Passage: {text}\nQuestion {number}: When does it open?",
                },
                {"role": "assistant", "content": "At nine."},
            ]
        },
        "evidence": [{"column": "text", "quote": text}],
    }


def test_document_chunking_keeps_every_parent_and_can_seed_generation():
    dataset = source_dataset()
    original = list(store.iter_rows(paths.cell_path(dataset.id, dataset.source.id)))
    original_hash = dataset.source.fingerprint
    tools = agent.Tools(dataset.id, None, lambda _: None)
    result = tools.handlers()["chunk_text"](
        {
            "text_column": "text",
            "group_by": ["document_id", "page"],
            "max_chars": 32,
            "plan_step": "prepare",
        }
    )
    assert result["ok"], result
    chunks = list(store.iter_rows(paths.cell_path(dataset.id, result["id"])))
    assert len({row["source_row"] for row in chunks}) == len(chunks)
    assert "".join(row["text"] for row in chunks) == "".join(row["text"] for row in original)
    parents = set()
    for row in chunks:
        provenance = row["_overmind_provenance"]
        for parent in provenance["parents"]:
            parents.add(parent["row"])
            assert parent["cell"] == str(dataset.source.id)
            assert parent["fingerprint"] == original_hash
            assert contamination_keys(original[parent["row"]]) <= contamination_keys(row)
    assert parents == {0, 1, 2}
    assert store.file_sha256(paths.cell_path(dataset.id, dataset.source.id)) == original_hash
    seeded = tools.seed_examples(
        {
            "mode": "derive",
            "target_rows": 4,
            "instruction": "Grounded questions",
            "plan_step": "prepare",
        }
    )
    assert seeded["source_rows"] == len(chunks)


def test_repeated_failure_stops_even_when_read_only_tools_succeed():
    from overbae.services.datasets import workflow

    dataset = source_dataset()
    tools = agent.Tools(dataset.id, None, lambda _: None)
    handlers = tools.handlers()
    for index in range(3):
        result = handlers["add_cell"]({"title": f"Attempt {index}", "script": "df = missing"})
        assert result["ok"] is False
        assert result["failure"]["code"] == "execution_failed"
        handlers["status"]({})
    assert tools.stop_requested
    saved = workflow.describe(dataset)
    assert saved["state"] == "blocked"
    assert saved["failure"]["attempts"] == 3
    other = agent.Tools(dataset.id, None, lambda _: None)
    assert other.status({})["workflow"]["state"] == "blocked"
    assert dataset.cells.count() == 1


def test_uncommitted_generation_setup_follows_an_explicit_repaired_source():
    dataset = source_dataset()
    tools = agent.Tools(dataset.id, None, lambda _: None)
    request = {
        "mode": "derive",
        "target_rows": 2,
        "instruction": "Grounded questions",
        "plan_step": "prepare",
    }
    first = tools.seed_examples(request)
    repaired = tools.add_cell(
        {
            "title": "Normalize",
            "script": "df['text'] = df['text'].str.strip()",
            "plan_step": "prepare",
        }
    )
    assert repaired["ok"], repaired
    second = tools.seed_examples(request)
    assert first["run_id"] != second["run_id"]
    assert second["source_cell"] == repaired["id"]
    assert second["source_fingerprint"] == dataset.cells.get(pk=repaired["id"]).fingerprint


def test_generation_commits_batches_resumes_and_publishes_once(monkeypatch):
    from overbae.services.datasets import generation

    dataset = source_dataset()
    tools = agent.Tools(dataset.id, None, lambda _: None)
    seeded = tools.seed_examples(
        {
            "mode": "derive",
            "target_rows": 3,
            "instruction": "Grounded questions",
            "plan_step": "prepare",
        }
    )
    run_id = seeded["run_id"]
    first = generation.accept_batch(run_id, "first", [example(0, 1)])
    assert first["generated_rows"] == 1
    assert dataset.cells.count() == 1
    assert generation.accept_batch(run_id, "first", [example(0, 1)]) == first
    with pytest.raises(ValueError, match="different"):
        generation.accept_batch(run_id, "first", [example(0, 2)])
    with pytest.raises(ValueError, match="duplicates"):
        generation.accept_batch(run_id, "duplicate", [example(0, 1)])
    resumed = agent.Tools(dataset.id, None, lambda _: None).seed_examples(
        {
            "mode": "derive",
            "target_rows": 3,
            "instruction": "Grounded questions",
            "plan_step": "prepare",
            "run_id": run_id,
        }
    )
    assert resumed["remaining_rows"] == 2
    generation.accept_batch(run_id, "second", [example(0, 2), example(0, 3)])
    cell = generation.publish(run_id)
    assert generation.publish(run_id).id == cell.id
    assert cell.rows == 3 and dataset.cells.count() == 2
    assert len(list(store.iter_rows(paths.cell_path(dataset.id, cell.id)))) == 3
    use.use(dataset, "train", cell=cell)
    with pytest.raises(ValueError, match="published|frozen|complete"):
        generation.accept_batch(run_id, "late", [example(0, 4)])
    detail = serialize_dataset_detail(Dataset.objects.get(pk=dataset.pk))
    assert detail.workflow["generation"]["generated_rows"] == 3
    assert detail.workflow["generation"]["published_cell"] == str(cell.pk)


def test_bad_evidence_batch_never_commits_good_rows_from_the_same_batch():
    from overbae.services.datasets import generation

    dataset = source_dataset()
    seeded = agent.Tools(dataset.id, None, lambda _: None).seed_examples(
        {
            "mode": "derive",
            "target_rows": 2,
            "instruction": "Grounded questions",
            "plan_step": "prepare",
        }
    )
    wrong = example(0, 2)
    wrong["evidence"][0]["quote"] = "The library opens at noon."
    with pytest.raises(ValueError, match="quote"):
        generation.accept_batch(seeded["run_id"], "invalid", [example(0, 1), wrong])
    assert generation.describe(seeded["run_id"])["generated_rows"] == 0
    assert dataset.cells.count() == 1


def test_batch_rejection_identifies_the_wrong_seed_and_preserves_valid_examples():
    dataset = source_dataset()
    tools = agent.Tools(dataset.pk, None, lambda _: None)
    setup = tools.seed_examples(
        {
            "mode": "derive",
            "target_rows": 3,
            "instruction": "Grounded questions",
            "plan_step": "prepare",
        }
    )
    batch = [example(0, 1), example(1, 2), example(0, 3)]
    rejected = tools.handlers()["add_synthetic_rows"]({"examples": batch})
    assert not rejected["ok"]
    assert rejected["failure"]["code"] == "evidence_mismatch"
    assert rejected["validation_errors"] == [
        {
            "path": ["examples", 1, "evidence", 0, "quote"],
            "code": "quote_not_in_seed",
            "seed_row": 1,
            "column": "text",
            "source_cell": str(dataset.source.pk),
            "source_fingerprint": dataset.source.fingerprint,
        }
    ]
    assert generation.describe(setup["run_id"])["generated_rows"] == 0
    assert dataset.cells.count() == 1
    batch[rejected["validation_errors"][0]["path"][1]]["seed_row"] = 0
    accepted = tools.handlers()["add_synthetic_rows"]({"examples": batch})
    assert accepted["ok"] and accepted["generated_rows"] == 3
    assert accepted["published_cell"]
    assert (
        store.file_sha256(paths.cell_path(dataset.pk, dataset.source.pk))
        == dataset.source.fingerprint
    )


def test_background_batch_returns_every_evidence_error_before_a_corrected_commit():
    dataset, run_id = saved_generation(target=3)
    item = generation.claim(run_id, owner="batch-evidence")
    operations.started(dataset.pk, "batch-evidence")
    tools = generation_worker.BatchTools(generation.get(run_id), item, "batch-evidence")
    batch = [example(0, 1), example(1, 2), example(2, 3)]
    rejected = tools.submit_examples({"examples": batch})
    assert not rejected["ok"] and not tools.stop_requested
    assert [issue["path"][1] for issue in rejected["validation_errors"]] == [1, 2]
    assert generation.describe(run_id)["generated_rows"] == 0
    for issue in rejected["validation_errors"]:
        batch[issue["path"][1]]["seed_row"] = 0
    accepted = tools.submit_examples({"examples": batch})
    assert accepted["ok"] and accepted["generated_rows"] == 3
    assert tools.stop_requested


def test_recipe_qualification_checks_the_original_request_not_just_its_interpretation(monkeypatch):
    dataset = source_dataset()
    dataset.brief = (
        "Teach the model library facts so it can answer questions without supplied passages."
    )
    dataset.save(update_fields=["brief"])
    setup = agent.Tools(dataset.pk, None, lambda _: None).seed_examples(
        {
            "mode": "derive",
            "target_rows": 2,
            "instruction": "Answer using a passage in every input.",
            "plan_step": "prepare",
        }
    )
    generation.accept_batch(setup["run_id"], "pilot", [example(0, 1)])
    contexts = []

    def judge(records, checks, context, **kwargs):
        contexts.append(context)
        assert context["user_request"] == dataset.brief
        return SimpleNamespace(
            parsed=SimpleNamespace(answers={"r0_c0": "fail", "r0_c1": "pass"}),
            stats={},
            raw="Task mismatch",
            judge_trace_id="task-mismatch",
        )

    monkeypatch.setattr(generation_quality.semantic_checks, "evaluate_batch", judge)
    first = generation_quality.qualify(setup["run_id"])
    second = generation_quality.qualify(setup["run_id"])
    assert first == second and first["status"] == "failed"
    assert len(contexts) == 1
    assert generation.describe(setup["run_id"])["generated_rows"] == 1
    assert generation.describe(setup["run_id"])["failure"]["code"] == "recipe_qualification"


def test_unpublished_pilot_reports_saved_rows_separately_from_dataset_output(monkeypatch):
    dataset = source_dataset()

    class PartialEngine:
        name = "test"

        def run(self, dataset, message, tools, pending):
            tools.seed_examples(
                {
                    "mode": "derive",
                    "target_rows": 2,
                    "instruction": "Grounded questions",
                    "plan_step": "prepare",
                }
            )
            tools.add_synthetic_rows({"examples": [example(0, 1)]})
            yield from pending
            return agent.engines.Outcome(text="Pilot saved.")

    monkeypatch.setattr(agent.engines, "select", lambda user=None: PartialEngine())
    list(agent.follow_up(dataset.pk, "Prepare the requested examples."))
    dataset.refresh_from_db()
    progress = dataset.chat[-1]["progress"]
    assert progress["generated_rows"] == 1
    assert progress["published_rows"] == 0
    assert progress["publication"] == "pending"
    assert dataset.cells.count() == 1


def test_generated_document_lineage_and_review_status_come_from_the_platform():
    dataset = source_dataset(
        rows=[
            {
                "text": "The library opens at nine. ",
                "source_name": "library.md",
                "_overmind_document_id": "document-1",
            }
        ]
    )
    setup = agent.Tools(dataset.pk, None, lambda _: None).seed_examples(
        {
            "mode": "derive",
            "target_rows": 1,
            "instruction": "Grounded questions",
            "plan_step": "prepare",
        }
    )
    generated = example(0, 1)
    generated["row"].update(
        source_name="wrong.md", _overmind_document_id="wrong", human_reviewed=True
    )
    generation.accept_batch(setup["run_id"], "pilot", [generated])
    cell = generation.publish(setup["run_id"])
    row = next(store.iter_rows(paths.cell_path(dataset.pk, cell.pk)))
    assert row["source_name"] == "library.md"
    assert row["_overmind_document_id"] == "document-1"
    assert row["human_reviewed"] is False


def test_automatic_plan_revision_cannot_replenish_or_raise_spent_audit_budget():
    from overbae.services.datasets import preparation

    dataset = source_dataset()
    tools = agent.Tools(dataset.pk, None, lambda _: None)
    tools.automatic = True
    tools.query({"sql": "SELECT * FROM t ORDER BY source_row"})
    spec = dict(dataset.preparation_plan["specification"])
    spec.update(version=str(dataset.source.pk), semantic_row_budget=2)
    spec["outcome"] = {
        "deliverables": ["Library answers"],
        "task": "Answer library questions from the supplied passage.",
        "model_input": "Passage and question.",
    }
    spec["checks"] = [
        {
            "name": "answer_support",
            "category": "semantic",
            "method": "semantic",
            "question": "Supported?",
        }
    ]
    assert tools.record_preparation_plan(spec)["ok"]
    dataset.refresh_from_db()
    assert preparation.reserve_semantic_rows(dataset, dataset.source, ["answer_support"], 2) == 2
    spec["objective"] = "Revised interpretation of the same task"
    assert tools.record_preparation_plan(spec)["ok"]
    dataset.refresh_from_db()
    assert preparation.reserve_semantic_rows(dataset, dataset.source, ["answer_support"], 2) == 0
    spec["semantic_row_budget"] = 4
    result = tools.record_preparation_plan(spec)
    assert not result["ok"] and "budget" in result["error"].lower()


def test_large_generation_uses_batches_without_rewriting_accumulated_output():
    from overbae.services.datasets import generation

    dataset = source_dataset()
    seeded = agent.Tools(dataset.id, None, lambda _: None).seed_examples(
        {
            "mode": "derive",
            "target_rows": 10000,
            "instruction": "Grounded questions",
            "plan_step": "prepare",
        }
    )
    original_hash = dataset.source.fingerprint
    with patch.object(store, "write_rows", wraps=store.write_rows) as write:
        for offset in range(0, 10000, 50):
            examples = [example(0, n) for n in range(offset, offset + 50)]
            if offset == 5000:
                with (
                    patch.object(
                        WorkshopRecord.objects,
                        "bulk_create",
                        side_effect=RuntimeError("database interrupted"),
                    ),
                    pytest.raises(RuntimeError, match="database interrupted"),
                ):
                    generation.accept_batch(seeded["run_id"], str(offset), examples)
                assert generation.describe(seeded["run_id"])["generated_rows"] == 5000
            generation.accept_batch(seeded["run_id"], str(offset), examples)
        assert write.call_count == 201
    summary = generation.describe(seeded["run_id"])
    assert summary["generated_rows"] == 10000 and summary["batches"] == 200
    cell = generation.publish(seeded["run_id"])
    assert cell.rows == 10000
    assert generation.publish(seeded["run_id"]).pk == cell.pk
    rows = list(store.iter_rows(paths.cell_path(dataset.pk, cell.pk)))
    assert len({row["source_row"] for row in rows}) == 10000
    assert len({row["messages"][0]["content"] for row in rows}) == 10000
    assert store.file_sha256(paths.cell_path(dataset.pk, dataset.source.pk)) == original_hash


def test_generation_receipt_survives_unknown_provider_outcome():
    from overbae.services.datasets import generation

    dataset = source_dataset()
    seeded = agent.Tools(dataset.id, None, lambda _: None).seed_examples(
        {
            "mode": "derive",
            "target_rows": 2,
            "instruction": "Grounded questions",
            "plan_step": "prepare",
        }
    )
    unit = generation.claim(seeded["run_id"], owner="worker-a")
    generation.submitting(unit.id, owner="worker-a", provider="fixture")
    generation.interrupted(unit.id, owner="worker-a")
    assert generation.claim(seeded["run_id"], owner="worker-b") is None
    assert generation.describe(seeded["run_id"])["state"] == "blocked"
    assert generation.describe(seeded["run_id"])["failure"]["code"] == "provider_outcome_unknown"


def test_background_generation_reuses_completed_batches_and_reports_partial(monkeypatch):
    from overbae.services.datasets import generation, generation_worker
    from overbae.services.datasets.notebook import engines

    dataset = source_dataset()
    setup = agent.Tools(dataset.pk, None, lambda _: None).seed_examples(
        {
            "mode": "derive",
            "target_rows": 10,
            "instruction": "Grounded questions",
            "plan_step": "prepare",
        }
    )
    calls = []

    class Engine:
        name = "fixture"

        def run(self, dataset, message, tools, pending):
            calls.append(message)
            item = tools.item
            rows = [example(0, item.inputs["offset"] + n) for n in range(item.inputs["rows"])]
            assert tools.handlers()["submit_examples"]({"examples": rows})["ok"]
            if False:
                yield
            return engines.Outcome(stats={"prompt_tokens": 40, "completion_tokens": 80})

    monkeypatch.setattr(engines, "select", lambda user: Engine())
    generation_worker.execute(setup["run_id"], owner="first")
    assert generation.describe(setup["run_id"])["generated_rows"] == 8
    generation_worker.execute(setup["run_id"], owner="second")
    generation_worker.execute(setup["run_id"], owner="redelivery")
    assert len(calls) == 2
    second = json.loads(calls[1])
    assert len(second["batch"]["accepted_examples"]) == 8
    assert second["batch"]["accepted_examples"][0]["messages"][1]["content"] == "At nine."
    assert (
        second["batch"]["accepted_examples"]
        == generation.get(setup["run_id"]).items.get(owner="second").inputs["accepted_examples"]
    )
    summary = generation.describe(setup["run_id"])
    assert summary["generated_rows"] == 10
    assert summary["published_cell"]
    assert summary["usage"]["prompt_tokens"] == 80
    assert summary["source_rows_without_examples"] == 2


def test_generation_pause_prevents_claims_and_stale_resume(monkeypatch):
    from overbae.services.datasets import generation

    dataset = source_dataset()
    setup = agent.Tools(dataset.pk, None, lambda _: None).seed_examples(
        {
            "mode": "derive",
            "target_rows": 2,
            "instruction": "Grounded questions",
            "plan_step": "prepare",
        }
    )
    state = generation.describe(setup["run_id"])
    paused = generation.control(dataset, state["id"], action="pause", revision=state["revision"])
    assert generation.claim(state["id"], owner="worker") is None
    with pytest.raises(ValueError, match="revision"):
        generation.control(dataset, state["id"], action="resume", revision=state["revision"])
    resumed = generation.control(dataset, state["id"], action="resume", revision=paused["revision"])
    assert resumed["state"] == "queued"


def test_failed_publication_resumes_saved_batches_without_rewriting_source(monkeypatch):
    from overbae.services.datasets import generation

    dataset = source_dataset()
    setup = agent.Tools(dataset.pk, None, lambda _: None).seed_examples(
        {
            "mode": "derive",
            "target_rows": 1,
            "instruction": "Grounded questions",
            "plan_step": "prepare",
        }
    )
    generation.accept_batch(setup["run_id"], "only", [example(0, 1)])
    with monkeypatch.context() as patcher:
        patcher.setattr(
            generation.measure,
            "frame",
            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("worker lost")),
        )
        with pytest.raises(RuntimeError, match="worker lost"):
            generation.publish(setup["run_id"])
    assert dataset.cells.count() == 1
    assert generation.describe(setup["run_id"])["generated_rows"] == 1
    cell = generation.publish(setup["run_id"])
    assert cell.rows == 1


def test_completed_provider_response_is_recovered_without_another_model_call(monkeypatch):
    from overbae.services.datasets import generation, generation_worker
    from overbae.services.datasets.notebook import engines

    dataset = source_dataset()
    setup = agent.Tools(dataset.pk, None, lambda _: None).seed_examples(
        {
            "mode": "derive",
            "target_rows": 1,
            "instruction": "Grounded questions",
            "plan_step": "prepare",
        }
    )
    item = generation.claim(setup["run_id"], owner="dead")
    generation.submitting(item.pk, owner="dead", provider="fixture")
    generation.received(
        item.pk, owner="dead", response={"examples": [example(0, 1)]}, usage={"prompt_tokens": 20}
    )
    WorkshopWorkItem.objects.filter(pk=item.pk).update(
        lease_until=timezone.now() - timedelta(seconds=1)
    )
    WorkshopRun.objects.filter(pk=item.run_id).update(
        lease_until=timezone.now() - timedelta(seconds=1)
    )
    monkeypatch.setattr(
        engines, "select", lambda user: pytest.fail("Must reuse the saved response")
    )
    generation_worker.execute(setup["run_id"], owner="recovery")
    assert generation.describe(setup["run_id"])["generated_rows"] == 1


def test_custom_split_and_merge_get_platform_identity_and_full_lineage():
    dataset = source_dataset()
    tools = agent.Tools(dataset.pk, None, lambda _: None)
    result = tools.add_cell(
        {
            "title": "Merge evidence",
            "plan_step": "prepare",
            "script": "df = pd.DataFrame([{'text': ''.join(df['text']), '_overmind_parent_rows': df['source_row'].tolist()}])",
        }
    )
    assert result["ok"], result
    merged = list(store.iter_rows(paths.cell_path(dataset.pk, result["id"])))[0]
    assert {p["row"] for p in merged["_overmind_provenance"]["parents"]} == {0, 1, 2}
    split = tools.add_cell(
        {
            "title": "Split evidence",
            "plan_step": "prepare",
            "script": "df = df.loc[df.index.repeat(2)].copy(); df['part'] = range(len(df))",
        }
    )
    assert split["ok"], split
    output = list(store.iter_rows(paths.cell_path(dataset.pk, split["id"])))
    assert len({r["source_row"] for r in output}) == 2
    assert all(
        r["_overmind_provenance"]["parents"][0]["row"] == merged["source_row"] for r in output
    )
    assert all(contamination_keys(merged) <= contamination_keys(r) for r in output)


def test_declared_outcome_requires_coverage_and_checks_not_just_valid_schema():
    from overbae.services.datasets import preparation, workflow

    dataset = source_dataset(intent="explore")
    plan = preparation.PlanRequest.model_validate(
        {
            "version": "1.0",
            "objective": "Explore source coverage",
            "consumer": "custom",
            "understanding": "Library source fragments",
            "families": [{"name": "library", "evidence": "All rows inspected"}],
            "outcome": {
                "deliverables": ["Coverage report"],
                "task": "Describe the library evidence",
                "confidence": "supported",
                "required_checks": ["page_coverage"],
                "preservation": ["All original rows"],
            },
            "checks": [
                {
                    "name": "page_coverage",
                    "category": "coverage",
                    "method": "deterministic",
                    "question": "Have all pages been accounted for?",
                }
            ],
        }
    )
    saved = preparation.save_plan(dataset, dataset.source, plan)
    workflow.bind_plan(dataset, saved)
    result = workflow.finish(workflow.current(dataset.pk).pk)
    assert result["execution"] == "partial"
    assert result["missing_checks"] == ["page_coverage"]


def saved_generation(target=2):
    dataset = source_dataset()
    setup = agent.Tools(dataset.pk, None, lambda _: None).seed_examples(
        {
            "mode": "derive",
            "target_rows": target,
            "instruction": "Grounded questions",
            "plan_step": "prepare",
        }
    )
    return dataset, setup["run_id"]


def test_received_response_is_not_recovered_while_its_owner_is_live(monkeypatch):
    dataset, run_id = saved_generation()
    item = generation.claim(run_id, owner="live")
    generation.received(item.pk, owner="live", response={"examples": [example(0, 1)]})
    monkeypatch.setattr(
        generation_worker.engines, "select", lambda user: pytest.fail("Concurrent provider call")
    )
    generation_worker.execute(run_id, owner="redelivery")
    assert generation.describe(run_id)["generated_rows"] == 0


def test_cancelled_generation_ignores_late_failure_and_exhaustion():
    dataset, run_id = saved_generation()
    item = generation.claim(run_id, owner="live")
    operations.started(dataset.pk, "live")
    tools = generation_worker.BatchTools(generation.get(run_id), item, "live")
    operations.cancel(dataset.pk)
    generation.interrupted(item.pk, owner="live")
    with pytest.raises(ValueError):
        tools.source_exhausted({"reason": "No more evidence"})
    assert generation.describe(run_id)["state"] == "cancelled"
    assert dataset.cells.count() == 1


def test_worker_finishes_publication_after_all_batches_were_committed(monkeypatch):
    dataset, run_id = saved_generation(target=1)
    generation.accept_batch(run_id, "only", [example(0, 1)])
    monkeypatch.setattr(
        generation_worker.engines, "select", lambda user: pytest.fail("No model needed")
    )
    result = generation_worker.execute(run_id, owner="recovery")
    assert result["published_cell"]
    assert dataset.cells.count() == 2


def test_failed_attempt_usage_survives_retry_and_response_recovery():
    dataset, run_id = saved_generation(target=1)
    first = generation.claim(run_id, owner="first")
    generation.submitting(first.pk, owner="first", provider="fixture")
    generation.received(
        first.pk, owner="first", response={"examples": []}, usage={"prompt_tokens": 20}
    )
    generation.rejected(first.pk, owner="first", detail="Missing examples")
    WorkshopRun.objects.filter(pk=run_id).update(owner="", lease_until=None)
    second = generation.claim(run_id, owner="second")
    generation.submitting(second.pk, owner="second", provider="fixture")
    generation.received(
        second.pk,
        owner="second",
        response={"examples": [example(0, 1)]},
        usage={"prompt_tokens": 30},
    )
    generation.accept_batch(run_id, second.key, [example(0, 1)], owner="second")
    assert generation.record_usage(run_id)["prompt_tokens"] == 50


def test_retry_limit_cannot_be_reset_by_resume():
    dataset, run_id = saved_generation()
    for index in range(3):
        WorkshopRun.objects.filter(pk=run_id).update(owner="", lease_until=None)
        item = generation.claim(run_id, owner=str(index))
        generation.rejected(item.pk, owner=str(index), detail="Unsupported examples")
    state = generation.describe(run_id)
    with pytest.raises(ValueError, match="retry"):
        generation.control(dataset, run_id, action="resume", revision=state["revision"])
    assert generation.claim(run_id, owner="fourth") is None


def test_broker_failure_leaves_recoverable_dispatch_without_new_provider_intent(
    monkeypatch, django_capture_on_commit_callbacks
):
    from overbae.tasks import datasets as tasks

    dataset, run_id = saved_generation()
    monkeypatch.setattr(
        tasks.generate,
        "apply_async",
        lambda **kw: (_ for _ in ()).throw(ConnectionError("broker unavailable")),
    )
    with django_capture_on_commit_callbacks(execute=True):
        generation_worker.schedule(run_id)
    state = generation.describe(run_id)
    assert state["state"] == "queued"
    assert state["failure"]["code"] == "dispatch_unconfirmed"
    calls = []
    monkeypatch.setattr(tasks.generate, "apply_async", lambda **kw: calls.append(kw))
    with django_capture_on_commit_callbacks(execute=True):
        generation_worker.recover()
    assert len(calls) == 1
    assert generation.get(run_id).items.count() == 0


def test_generation_qualification_is_saved_and_reused_before_scheduling(monkeypatch):
    from types import SimpleNamespace

    from overbae.services.datasets import generation_quality, semantic_checks

    dataset, run_id = saved_generation(target=10)
    generation.accept_batch(run_id, "pilot", [example(0, 1)])
    calls = []

    def judge(batch, checks, context_data, **kwargs):
        calls.append(batch)
        return SimpleNamespace(
            parsed=SimpleNamespace(
                answers={
                    f"r{i}_c{j}": "pass" for i in range(len(batch)) for j in range(len(checks))
                }
            ),
            stats={"response_cost": 0.001},
            raw="recorded",
            judge_trace_id="qualification-fixture",
        )

    monkeypatch.setattr(semantic_checks, "evaluate_batch", judge)
    first = generation_quality.qualify(run_id)
    second = generation_quality.qualify(run_id)
    assert first == second and first["checked_rows"] == 1
    assert len(calls) == 1
    assert calls[0][0]["source"]["text"] == "The library opens at nine. "
    assert first["status"] == "passed"


def test_failed_recipe_qualification_preserves_pilot_and_allows_explicit_revision(monkeypatch):
    from types import SimpleNamespace

    from overbae.services.datasets import generation_quality, semantic_checks

    dataset, run_id = saved_generation(target=10)
    generation.accept_batch(run_id, "pilot", [example(0, 1)])
    monkeypatch.setattr(
        semantic_checks,
        "evaluate_batch",
        lambda batch, checks, context_data, **kw: SimpleNamespace(
            parsed=SimpleNamespace(answers={f"r0_c{j}": "fail" for j in range(len(checks))}),
            stats={},
            raw="recorded",
            judge_trace_id="failure-fixture",
        ),
    )
    result = generation_quality.qualify(run_id)
    assert result["status"] == "failed"
    previous = generation.get(run_id)
    revised = generation.start(
        dataset,
        previous.source,
        target_rows=10,
        instruction="Use the passage in every question",
        mode="derive",
        plan=previous.plan,
    )
    assert revised.pk != previous.pk and revised.generated_rows == 0
    assert generation.get(run_id).generated_rows == 1
    assert generation.get(run_id).state == "cancelled"


def test_repeating_successful_reads_without_progress_stops_the_agent():
    dataset = source_dataset()
    tools = agent.Tools(dataset.pk, None, lambda _: None)
    for _ in range(4):
        tools.handlers()["query"]({"sql": "SELECT count(*) AS n FROM t"})
        tools.handlers()["status"]({})
    assert tools.stop_requested
    assert agent.status(dataset)["workflow"]["failure"]["code"] == "no_progress"


def test_unusable_seed_batch_advances_to_remaining_source_without_padding(monkeypatch):
    from overbae.services.datasets.notebook import engines

    dataset = source_dataset(rows=[{"text": f"Evidence {i}", "family": "pages"} for i in range(20)])
    setup = agent.Tools(dataset.pk, None, lambda _: None).seed_examples(
        {
            "mode": "derive",
            "target_rows": 1,
            "instruction": "Grounded examples",
            "plan_step": "prepare",
        }
    )
    calls = []

    class Engine:
        name = "fixture"

        def run(self, dataset, message, tools, pending):
            calls.append(tools.item.inputs["positions"])
            if len(calls) == 1:
                tools.source_exhausted({"reason": "This supplied seed cannot support the task"})
            else:
                assert tools.submit_examples({"examples": [example(0, 1, "Evidence 0")]})["ok"]
            if False:
                yield
            return engines.Outcome(stats={"prompt_tokens": 10})

    monkeypatch.setattr(engines, "select", lambda user: Engine())
    first = generation_worker.execute(setup["run_id"], owner="first")
    assert first["state"] == "queued" and first["generated_rows"] == 0
    last = generation_worker.execute(setup["run_id"], owner="second")
    assert calls[0] != calls[1]
    assert last["generated_rows"] == 1 and last["published_cell"]


def test_saved_unsuitable_seed_response_recovers_without_exhausting_other_seeds(monkeypatch):
    dataset, run_id = saved_generation()
    item = generation.claim(run_id, owner="lost")
    generation.received(
        item.pk,
        owner="lost",
        response={"tool": "source_exhausted", "reason": "This passage is only a heading"},
    )
    WorkshopWorkItem.objects.filter(pk=item.pk).update(lease_until=timezone.now())
    WorkshopRun.objects.filter(pk=run_id).update(lease_until=timezone.now())
    monkeypatch.setattr(
        generation_worker.engines, "select", lambda user: pytest.fail("Reuse saved response")
    )
    result = generation_worker.execute(run_id, owner="recovery")
    assert result["state"] == "queued"
    assert generation.get(run_id).result["seed_cursor"] == len(item.inputs["positions"])
    assert generation.get(run_id).items.get(pk=item.pk).state == "skipped"


def test_redelivered_finished_task_does_not_claim_a_new_batch(monkeypatch):
    dataset, run_id = saved_generation(target=10)
    first = generation.claim(run_id, owner="same-task")
    operations.started(dataset.pk, "same-task")
    generation.accept_batch(run_id, first.key, [example(0, 1)], owner="same-task")
    operations.finished(dataset.pk, task_id="same-task")
    WorkshopRun.objects.filter(pk=run_id).update(owner="", lease_until=None, state="queued")
    monkeypatch.setattr(
        generation_worker.engines, "select", lambda user: pytest.fail("No new provider call")
    )
    generation_worker.execute(run_id, owner="same-task")
    assert generation.get(run_id).items.count() == 1


def test_local_qualification_error_does_not_leave_unknown_provider_submission():
    from overbae.services.datasets import generation_quality

    dataset, run_id = saved_generation(target=10)
    with pytest.raises(ValueError, match="Save representative"):
        generation_quality.qualify(run_id)
    assert not generation.get(run_id).items.filter(state="submitting").exists()


def test_batch_saves_provider_identity_before_any_output_is_accepted():
    dataset, run_id = saved_generation()
    item = generation.claim(run_id, owner="provider-call")
    operations.started(dataset.pk, "provider-call")
    tools = generation_worker.BatchTools(generation.get(run_id), item, "provider-call")
    tools.before_round("cursor")
    tools.provider_started({"agent_id": "fixture-agent", "run_id": "fixture-run"})
    generation.interrupted(item.pk, owner="provider-call")
    item.refresh_from_db()
    assert item.state == "unknown"
    assert item.provider["attempts"]["1"]["identity"]["run_id"] == "fixture-run"


def test_partial_publication_from_pause_keeps_accepted_rows_and_unmet_target():
    dataset, run_id = saved_generation(target=10)
    generation.accept_batch(run_id, "pilot", [example(0, 1)])
    state = generation.describe(run_id)
    paused = generation.control(dataset, run_id, action="pause", revision=state["revision"])
    published = generation.control(
        dataset, run_id, action="publish_partial", revision=paused["revision"]
    )
    assert published["state"] == "partial"
    assert published["remaining_rows"] == 9
    dataset.refresh_from_db()
    assert dataset.active_cell.rows == 1


def test_partial_publication_waits_for_saved_uncommitted_response():
    dataset, run_id = saved_generation(target=10)
    generation.accept_batch(run_id, "pilot", [example(0, 1)])
    item = generation.claim(run_id, owner="inflight")
    generation.received(item.pk, owner="inflight", response={"examples": [example(0, 2)]})
    state = generation.describe(run_id)
    with pytest.raises(ValueError, match="settle"):
        generation.control(dataset, run_id, action="publish_partial", revision=state["revision"])


def test_redundant_projection_keeps_the_evaluated_version_and_its_checks():
    dataset, run_id = saved_generation(target=1)
    generation.accept_batch(run_id, "pilot", [example(0, 1)])
    published = generation.publish(run_id)
    tools = agent.Tools(dataset.pk, None, lambda _: None)
    audit = tools.record_quality_review(
        {
            "version": str(published.pk),
            "checks": [{"name": "output_schema", "evidence": "A nonempty messages array"}],
            "script": "df = pd.DataFrame({'output_schema': df['messages'].map(lambda x: len(x) > 0)}, index=df.index)",
        }
    )
    assert audit["ok"], audit
    published.refresh_from_db()
    report = published.quality_report
    result = tools.add_cell(
        {"title": "Project output", "script": "df = df[['messages']]", "plan_step": "prepare"}
    )
    assert result.get("unchanged"), result
    dataset.refresh_from_db()
    assert dataset.active_cell.pk == published.pk
    assert dataset.active_cell.quality_report == report


def test_completed_workflow_separates_recovered_tool_failure_from_current_state():
    from overbae.services.datasets import workflow

    dataset = source_dataset(intent="explore")
    tools = agent.Tools(dataset.pk, None, lambda _: None)
    tools.handlers()["query"]({"sql": "SELECT missing_column FROM t"})
    tools.handlers()["query"]({"sql": "SELECT count(*) AS n FROM t"})
    workflow.finish(tools.workflow_id)
    result = workflow.describe(dataset)
    assert result["state"] == "complete" and not result["failure"]
    assert result["result"]["recovered_failure"]["tool"] == "query"


def test_queued_derivation_counts_accepted_examples_instead_of_source_rows():
    from overbae.services.datasets import workflow

    dataset, run_id = saved_generation(target=10)
    generation.accept_batch(run_id, "pilot", [example(0, 1)])
    WorkshopRun.objects.filter(pk=run_id).update(state="queued")
    result = workflow.finish(workflow.current(dataset.pk).pk)
    assert result["task_completion"]["delivered_rows"] == 1


@pytest.mark.parametrize("repair", [True, False])
def test_finalization_returns_missing_checks_to_agent_with_bounded_continuation(
    monkeypatch, repair
):
    from overbae.services.datasets import preparation
    from overbae.services.datasets.notebook import engines

    dataset = source_dataset(rows=[example(0, 1)["row"]])
    spec = dict(dataset.preparation_plan["specification"])
    spec["version"] = str(dataset.source.pk)
    spec["outcome"] = {
        "task": "Passage Q&A",
        "deliverables": ["Training examples with checked schema"],
        "required_checks": ["output_schema"],
    }
    preparation.save_plan(dataset, dataset.source, preparation.PlanRequest.model_validate(spec))
    calls = []

    class Engine:
        name = "fixture"

        def run(self, current, message, tools, pending):
            calls.append(message)
            if len(calls) > 1 and repair:
                assert "output_schema" in message
                assert tools.record_quality_review(
                    {
                        "version": str(current.active_cell.pk),
                        "checks": [{"name": "output_schema", "evidence": "Nonempty messages"}],
                        "script": "df = pd.DataFrame({'output_schema': df['messages'].map(lambda x: len(x) > 0)}, index=df.index)",
                    }
                )["ok"]
            if False:
                yield
            return engines.Outcome(text="Finished.")

    monkeypatch.setattr(engines, "select", lambda user=None: Engine())
    list(agent.diagnose(dataset.pk))
    dataset.refresh_from_db()
    assert len(calls) == 2
    assert dataset.chat[-1]["status"] == ("complete" if repair else "error")
