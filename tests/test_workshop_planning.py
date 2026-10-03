from unittest.mock import patch

import pytest

from overbae.api.dataset_serializers import DatasetSerializer
from overbae.models import Dataset, Project
from overbae.services.datasets import land, lifecycle, paths, review, store, use
from overbae.services.datasets.notebook import agent, run
from overbae.services.mcp.contracts.datasets import serialize_dataset_detail

pytestmark = pytest.mark.django_db


def setup_dataset(settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path / "media"
    project = Project.objects.create(name="Planning", slug="planning")
    ds = Dataset.objects.create(project=project, name="Unfamiliar decisions", intent="eval")
    land.land_rows(
        ds,
        [
            {
                "evidence_text": "",
                "prompt_text": "Choose",
                "classes": ["Yes", "No"],
                "label_probs": [0.7, 0.3],
                "importance": 2.0,
                "case_key": "one",
                "family": "survey",
            },
            {
                "evidence_text": "Document",
                "prompt_text": "Choose",
                "classes": ["A", "B"],
                "label_probs": [0.0, 1.0],
                "importance": 1.0,
                "case_key": "two",
                "family": "fact",
            },
        ],
    )
    ds.refresh_from_db()
    return ds, agent.Tools(ds.id, None, lambda _: None)


@pytest.mark.parametrize(
    "record",
    [
        {
            "messages": [
                {"role": "user", "content": "Question"},
                {"role": "assistant", "content": "Answer"},
            ]
        },
        {"input": "Question", "expected_output": "Answer"},
    ],
)
def test_landing_explores_shape_without_deciding_training_or_evaluation(settings, tmp_path, record):
    settings.MEDIA_ROOT = tmp_path / "media"
    project = Project.objects.create(name="Unassigned purpose", slug="unassigned")
    dataset = Dataset.objects.create(project=project, name="Explore", intent="pending")
    land.land_rows(dataset, [record])
    dataset.refresh_from_db()
    assert dataset.intent == "pending"
    assert dataset.source.stats["preparation_profile"]["rows_scanned"] == 1
    assert dataset.cells.count() == 1
    assert not agent.Tools(dataset.id, None, lambda _: None).prepare_examples({})["ok"]


def plan_request(ds):
    return {
        "version": str(ds.active_cell.id),
        "objective": "Prepare probability decisions across survey and factual tasks.",
        "consumer": "decision_evaluation",
        "understanding": "Classes and probabilities are aligned in publisher order; empty evidence is allowed.",
        "families": [
            {
                "name": "Source families",
                "evidence": "family distinguishes survey/fact",
                "input_columns": ["evidence_text", "prompt_text", "classes"],
                "target_columns": ["label_probs"],
                "group_columns": ["case_key"],
            }
        ],
        "mapping": {
            "decision.state": "evidence_text",
            "decision.question": "prompt_text",
            "decision.options": "classes",
            "decision.target_probabilities": "label_probs",
            "decision.weight": "importance",
        },
        "constants": {"decision.kind": "choice"},
        "assumptions": ["Publisher class order is authoritative."],
        "unresolved_questions": [
            "Publisher label correctness has not been independently verified."
        ],
        "steps": [
            {
                "id": "shape",
                "description": "Map declared fields without changing source values.",
                "kind": "transform",
            }
        ],
        "checks": [
            {
                "name": "probability_shape",
                "category": "technical",
                "method": "deterministic",
                "question": "Do vectors match ordered options?",
            },
            {
                "name": "source_fidelity",
                "category": "preservation",
                "method": "deterministic",
                "question": "Are source targets and evidence retained?",
            },
            {
                "name": "publisher_truth",
                "category": "semantic",
                "method": "unmeasured",
                "question": "Are publisher labels supported?",
            },
        ],
        "semantic_row_budget": 0,
    }


def test_plan_drives_native_mapping_and_shared_assessment(settings, tmp_path):
    ds, tools = setup_dataset(settings, tmp_path)
    tools.automatic = True
    premature = tools.prepare_examples({})
    assert not premature["ok"] and ds.cells.count() == 1
    discovery = tools.query({"sql": "SELECT family, count(*) AS n FROM t GROUP BY family"})
    assert len(discovery["rows"]) == 2
    saved = tools.record_preparation_plan(plan_request(ds))
    assert saved["ok"], saved
    prepared = tools.prepare_examples({"plan_step": "shape"})
    assert prepared["ok"], prepared
    ds.refresh_from_db()
    cell = ds.active_cell
    records = list(store.iter_rows(paths.cell_path(ds.id, cell.id)))
    assert records[0]["input"]["decision"]["options"] == ["Yes", "No"]
    assert records[0]["expected_output"] == {"probabilities": [0.7, 0.3]}
    assert records[0]["decision"]["weight"] == 2.0
    assert records[0]["label_probs"] == [0.7, 0.3]
    assert "target_probabilities" not in records[0]["input"]["decision"]
    assert cell.preparation_plan["id"] == saved["plan"]["id"]
    assert cell.preparation_plan["step_id"] == "shape"
    assert use.use(ds, "eval").id == cell.id
    checks = [dict(name=c["name"], evidence=c["question"]) for c in plan_request(ds)["checks"]]
    audit = tools.record_quality_review(
        {
            "checks": checks,
            "script": "def transform_batch(df):\n    return pd.DataFrame({'source_row': df.source_row, 'probability_shape': df.decision.map(lambda d: len(d['options']) == len(d['target_probabilities'])), 'source_fidelity': df.apply(lambda r: r['label_probs'] == r['decision']['target_probabilities'], axis=1), 'publisher_truth': None})",
        }
    )
    assert audit["ok"], audit
    cell.refresh_from_db()
    readiness = review.readiness(ds, cell)
    assert readiness["assessment"]["technical"] == "pass"
    assert readiness["assessment"]["preservation"] == "pass"
    assert readiness["assessment"]["semantic"] == "unknown"
    assert "answer_support: not reviewed" not in readiness["quality_reason"]
    rest = DatasetSerializer(ds).data
    mcp = serialize_dataset_detail(ds).model_dump()
    assert rest["preparation_plan"]["id"] == mcp["preparation_plan"]["id"] == saved["plan"]["id"]
    assert mcp["cells"][-1]["readiness"]["assessment"] == readiness["assessment"]


def test_plan_cannot_hide_conflicting_mapping_or_approve_selection(settings, tmp_path):
    ds, tools = setup_dataset(settings, tmp_path)
    assert tools.record_preparation_plan(plan_request(ds))["ok"]
    result = tools.add_cell(
        {
            "title": "Select one",
            "script": "df = df.iloc[:1]",
            "kind": "semantic",
            "plan_step": "shape",
        }
    )
    assert result["proposed"] and ds.active_cell.rows == 2
    proposal = ds.cells.get(pk=result["id"])
    lifecycle.accept_proposal(ds, proposal)
    run.execute(ds)
    ds.refresh_from_db()
    assert ds.active_cell.rows == 1
    raw = list(store.iter_rows(paths.cell_path(ds.id, ds.active_cell.id)))[0]
    assert raw["label_probs"] == [0.7, 0.3]


def test_changed_source_rejects_saved_mapping_before_transform(settings, tmp_path):
    ds, tools = setup_dataset(settings, tmp_path)
    saved = tools.record_preparation_plan(plan_request(ds))
    assert saved["ok"]
    ds.cells.filter(pk=ds.active_cell.id).update(fingerprint="changed")
    failed = tools.prepare_examples({"plan_step": "shape"})
    assert not failed["ok"] and "changed" in failed["error"].lower()
    assert ds.cells.count() == 1


def test_missing_mapping_is_rejected_and_unmeasured_audit_never_calls_a_judge(settings, tmp_path):
    ds, tools = setup_dataset(settings, tmp_path)
    request = plan_request(ds)
    request["mapping"]["decision.question"] = "missing"
    assert not tools.record_preparation_plan(request)["ok"]
    assert tools.record_preparation_plan(plan_request(ds))["ok"]
    tools.automatic = True
    with patch(
        "overbae.services.datasets.semantic_checks.run_checks",
        side_effect=AssertionError("No paid audit in this plan"),
    ):
        outcome = tools.check_semantic_quality(
            {
                "checks": [
                    {
                        "name": "publisher_truth",
                        "question": "Correct?",
                        "evidence_columns": ["evidence_text"],
                        "answer_columns": ["label_probs"],
                    }
                ]
            }
        )
    assert outcome["skipped"] and outcome["unverified_rows"] == 2


def test_mapping_cannot_overwrite_existing_native_target(settings, tmp_path):
    ds, tools = setup_dataset(settings, tmp_path)
    result = tools.add_cell(
        {
            "title": "Existing native fields",
            "script": "df['decision'] = df.apply(lambda r: {'state': r['evidence_text'], 'question': r['prompt_text'], 'kind': 'choice', 'options': r['classes'], 'target_probabilities': [1.0, 0.0]}, axis=1)",
        }
    )
    assert result["ok"]
    ds.refresh_from_db()
    existing = ds.active_cell.id
    assert tools.record_preparation_plan(plan_request(ds))["ok"]
    rejected = tools.prepare_examples({"plan_step": "shape"})
    assert not rejected["ok"] and "conflict" in rejected["error"].lower()
    assert ds.active_cell.id == existing
