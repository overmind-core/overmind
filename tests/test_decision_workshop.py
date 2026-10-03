import json
from unittest.mock import patch

import pytest
from conftest import plan_fixture

from overbae.models import Dataset, Project
from overbae.services.datasets import land, lifecycle, paths, store, use
from overbae.services.datasets.context import workshop_context
from overbae.services.datasets.notebook import agent, run, runner

pytestmark = pytest.mark.django_db


def corpus(settings, tmp_path, intent, records):
    settings.MEDIA_ROOT = tmp_path / "media"
    project = Project.objects.create(name="Decision workshop", slug="decision-workshop")
    ds = Dataset.objects.create(project=project, name="Decisions", intent=intent)
    land.land_rows(ds, records)
    ds.refresh_from_db()
    return ds, agent.Tools(ds.id, None, lambda _: None)


def flat(index=0, **changes):
    return dict(
        state="",
        question="Is two greater than one?",
        kind="noul",
        options=[],
        target=[0.7],
        source="reasoning",
        group_id=f"case-{index}",
        **changes,
    )


@pytest.mark.parametrize("intent", ["train", "eval"])
def test_native_preparation_keeps_empty_evidence_soft_targets_and_multiplicity(
    settings, tmp_path, intent
):
    rows = [
        flat(),
        flat(),
        {
            "decision": {
                "state": "Evidence",
                "question": "Choose",
                "kind": "score",
                "options": ["poor", "fair", "good"],
                "target_probabilities": [0.1, 0.2, 0.7],
                "weight": 2.5,
            },
            "source": "ratings",
            "group_id": "rated",
        },
    ]
    ds, tools = corpus(settings, tmp_path, intent, rows)
    plan_fixture(ds)
    tools.automatic = True
    result = tools.prepare_examples({"plan_step": "prepare"})
    assert result["ok"] and not result.get("proposed"), result
    ds.refresh_from_db()
    cell = ds.active_cell
    assert cell.rows == 3
    prepared = list(store.iter_rows(paths.cell_path(ds.id, cell.id)))
    assert prepared[0]["decision"]["state"] == ""
    assert prepared[0]["decision"]["options"] == ["No", "Yes"]
    assert prepared[0]["decision"]["target_probabilities"] == [1 - 0.7, 0.7]
    assert prepared[0]["decision"] == prepared[1]["decision"]
    assert prepared[2]["decision"] == rows[2]["decision"]
    original = list(store.iter_rows(paths.cell_path(ds.id, ds.source.id)))
    assert [r["_overmind_provenance"] for r in prepared] == [
        r["_overmind_provenance"] for r in original
    ]
    assert cell.intent_report[intent]["ok"]
    if intent == "eval":
        for row in prepared:
            request = row["input"]["decision"]
            assert set(request) == {"state", "question", "kind", "options"}
            assert row["expected_output"] == {
                "probabilities": row["decision"]["target_probabilities"]
            }
        assert cell.intent_report["eval"]["input_type"] == "decision"
    again = tools.prepare_examples({"plan_step": "prepare"})
    assert again.get("unchanged"), again
    with patch(
        "overbae.services.datasets.semantic_checks.run_checks",
        side_effect=AssertionError("No automatic paid judge"),
    ):
        audit = tools.check_semantic_quality(
            {
                "checks": [
                    {
                        "name": "answer_support",
                        "question": "Is the target true?",
                        "evidence_columns": ["state"],
                        "answer_columns": ["target"],
                    }
                ]
            }
        )
    assert audit["skipped"] and audit["unverified_rows"] == 3


def test_late_invalid_native_reference_is_retained_and_blocks_technical_use(settings, tmp_path):
    records = [flat(i) for i in range(10_003)]
    records[-1]["target"] = [1.2]
    ds, tools = corpus(settings, tmp_path, "eval", records)
    result = tools.prepare_examples({})
    assert result["ok"], result
    cell = ds.active_cell
    assert cell.rows == len(records)
    assert not cell.intent_report["eval"]["ok"]
    assert any(f.get("row") == 10_002 for f in cell.intent_report["eval"]["failures"])
    last = list(store.iter_rows(paths.cell_path(ds.id, cell.id)))[-1]
    assert last["target"] == [1.2]
    with pytest.raises(ValueError):
        use.check(ds, "eval", cell=cell)


def test_json_encoded_native_eval_cannot_hide_an_invalid_reference(settings, tmp_path):
    request = {"state": "", "question": "Choose", "kind": "choice", "options": ["A", "B"]}
    ds, _ = corpus(
        settings,
        tmp_path,
        "eval",
        [
            {
                "input": json.dumps({"decision": request}),
                "expected_output": {"probabilities": [2, -1]},
            }
        ],
    )
    assert ds.source.rows == 1
    assert not ds.source.intent_report["eval"]["ok"]
    with pytest.raises(ValueError):
        use.check(ds, "eval", cell=ds.source)


def test_sampling_proposal_replays_exact_rows_and_preserves_every_stratum(settings, tmp_path):
    records = []
    for i in range(20_003):
        kind = "noul" if i % 2 else "choice"
        soft = i % 3 == 0
        records.append(
            {
                "source": "rare" if i == 20_002 else "common",
                "group_id": f"g-{i // 2}",
                "decision": {
                    "state": f"case {i}",
                    "question": "Choose",
                    "kind": kind,
                    "options": ["No", "Yes"],
                    "target_probabilities": [0.2, 0.8] if soft else [0.0, 1.0],
                },
            }
        )
    ds, tools = corpus(settings, tmp_path, "train", records)
    args = {
        "rows": 503,
        "seed": 73491,
        "stratify_by": ["source", "decision.kind"],
        "target_type": True,
    }
    result = tools.sample_rows(args)
    assert result["ok"] and result["proposed"], result
    cell = ds.cells.get(pk=result["id"])
    assert cell.review["rows_removed"] == 19_500
    assert cell.review["identity_preserved"]
    repeated = tools.sample_rows(args)
    assert repeated["id"] == str(cell.id) and repeated["reused"]
    lifecycle.accept_proposal(ds, cell)
    with patch.object(runner, "run", side_effect=AssertionError("Approved preview must be reused")):
        run.execute(ds, activate_cell_id=cell.id)
    cell.refresh_from_db()
    selected = list(store.iter_rows(paths.cell_path(ds.id, cell.id)))
    assert len(selected) == 503
    assert [r["source_row"] for r in selected] == sorted({r["source_row"] for r in selected})
    original = list(store.iter_rows(paths.cell_path(ds.id, ds.source.id)))
    assert selected == [original[r["source_row"]] for r in selected]
    assert selected[-1]["source_row"] == 20_002

    def stratum(r):
        return r["source"], r["decision"]["kind"], max(r["decision"]["target_probabilities"]) < 1

    assert set(map(stratum, selected)) == set(map(stratum, original))
    replay = runner.run(
        cell.script, paths.cell_path(ds.id, ds.source.id), library_cache=tmp_path / "libraries"
    )
    assert replay.ok, replay.error
    assert list(store.iter_rows(replay.path)) == selected
    assert use.check(ds, "train", cell=cell).id == cell.id
    profile = workshop_context(ds)["profiles"]["active"]["counts"]
    assert profile["decision_rows"] == 503
    assert profile["soft_target_rows"] == sum(
        max(r["decision"]["target_probabilities"]) < 1 for r in selected
    )


@pytest.mark.parametrize(
    "args",
    [
        {"rows": 1, "seed": 3, "stratify_by": ["source"]},
        {"rows": 5, "seed": 3},
        {"rows": 2, "seed": 3, "stratify_by": ["missing_column"]},
        {"rows": 2, "seed": 3, "unexpected": "ignored"},
    ],
)
def test_incompatible_sample_never_lands_a_partial_cell(settings, tmp_path, args):
    ds, tools = corpus(settings, tmp_path, "train", [flat(), {**flat(1), "source": "other"}])
    result = tools.sample_rows(args)
    assert not result["ok"], result
    assert ds.cells.count() == 1 and ds.source.rows == 2


def test_truncated_diagnostics_are_explicit(tmp_path):
    path = tmp_path / "input.parquet"
    store.write_rows(path, [{"source_row": 0, "x": 1}])
    result = runner.run(
        "print('START' + 'x'*5000 + 'END')",
        path,
        library_cache=tmp_path / "libraries",
        produce_frame=False,
    )
    assert result.ok
    assert "truncated" in result.stdout.lower()
    assert result.stdout.endswith("END\n")


@pytest.mark.parametrize(
    "script",
    [
        "df = df.iloc[:1]",
        "df['decision'] = df.decision.map(lambda d: {**d, 'target_probabilities': [0.0, 1.0]})",
        "df['expected_output'] = [{'probabilities': [0.0, 1.0]}] * len(df)",
    ],
)
def test_automatic_cleanup_cannot_drop_or_relabel_native_decisions(settings, tmp_path, script):
    ds, tools = corpus(settings, tmp_path, "eval", [flat(), flat()])
    plan_fixture(ds)
    tools.automatic = True
    assert tools.prepare_examples({"plan_step": "prepare"})["ok"]
    original = ds.active_cell
    result = tools.add_cell(
        {
            "plan_step": "prepare",
            "title": "Cleanup",
            "script": script,
            "run": True,
            "kind": "mechanical",
        }
    )
    assert result["ok"] and result["proposed"], result
    ds.refresh_from_db()
    assert ds.active_cell.id == original.id
    assert ds.active_cell.rows == 2
