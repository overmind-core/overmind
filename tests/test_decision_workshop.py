import json

import pandas as pd
import pytest
from conftest import import_version

from overbae.models import Dataset, Project
from overbae.services.datasets import land, paths, store, use
from overbae.services.datasets.examples import prepare_examples
from overbae.services.datasets.profile import profile_records
from overbae.services.datasets.review import decision_changed

pytestmark = pytest.mark.django_db


def corpus(settings, tmp_path, intent, records):
    settings.MEDIA_ROOT = tmp_path / "media"
    project = Project.objects.create(name="Decision workshop", slug="decision-workshop")
    ds = Dataset.objects.create(project=project, name="Decisions", intent=intent)
    land.land_rows(ds, records)
    ds.refresh_from_db()
    return ds


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


def test_flat_supervision_changes_are_visible_to_workshop_review():
    original = flat(weight=2.5, target_semantics="posterior", target_provenance={"field": "p"})
    for field, value in {
        "weight": 1.0,
        "target_semantics": "teacher_distribution",
        "target_provenance": {"field": "teacher_p"},
    }.items():
        assert decision_changed(original, {**original, field: value})
    prepared = prepare_examples(pd.DataFrame([original]), "train").iloc[0]["decision"]
    assert prepared["weight"] == 2.5
    assert prepared["target_semantics"] == "posterior"
    assert prepared["target_provenance"] == {"field": "p"}


def test_flat_semantics_cannot_hide_invalid_gold_supervision():
    result = profile_records([flat(target_semantics="categorical_gold")])
    assert result["counts"].get("invalid_decision_rows", 0) == 1


@pytest.mark.parametrize("intent", ["train", "eval"])
@pytest.mark.parametrize("encoded", [False, True], ids=["object", "json_container"])
def test_identity_mapping_preserves_json_looking_decision_text(settings, tmp_path, intent, encoded):
    texts = ['{ "evidence": [1, 2] }', "[1, 2]", "true", "null", '"quoted"', " 12 ", ""]
    decisions = [
        {
            "state": text,
            "question": '"Choose"',
            "kind": "choice",
            "options": ["true", "false"],
            "target_probabilities": [0.3, 0.7],
        }
        for text in texts
    ]
    records = [
        {"decision": json.dumps(value) if encoded else value, "group_id": f"case-{index}"}
        for index, value in enumerate(decisions)
    ]
    ds = corpus(settings, tmp_path, intent, records)
    mapping = {f"decision.{field}": f"decision.{field}" for field in decisions[0]}
    prepared = prepare_examples(
        pd.DataFrame([dict(r, source_row=i) for i, r in enumerate(records)]),
        intent,
        mapping=mapping,
    )
    import_version(
        ds, prepared.astype(object).where(pd.notna(prepared), None).to_dict(orient="records")
    )
    ds.refresh_from_db()
    prepared = list(store.iter_rows(paths.cell_path(ds.id, ds.active_cell.id)))
    assert [row["decision"] for row in prepared] == decisions
    assert ds.active_cell.intent_report[intent]["ok"]
    if intent == "eval":
        assert [row["input"]["decision"]["state"] for row in prepared] == texts
        assert all(row["expected_output"] == {"probabilities": [0.3, 0.7]} for row in prepared)


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
    ds = corpus(settings, tmp_path, intent, rows)
    prepared = prepare_examples(
        pd.DataFrame([dict(r, source_row=i) for i, r in enumerate(rows)]), intent
    )
    import_version(
        ds, prepared.astype(object).where(pd.notna(prepared), None).to_dict(orient="records")
    )
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
    for result_row, source_row in zip(prepared, original, strict=True):
        assert all(
            result_row["_overmind_provenance"][k] == v
            for k, v in source_row["_overmind_provenance"].items()
        )
        if "parents" in result_row["_overmind_provenance"]:
            assert (
                result_row["_overmind_provenance"]["parents"][0]["row"] == source_row["source_row"]
            )
    assert cell.intent_report[intent]["ok"]
    if intent == "eval":
        for row in prepared:
            request = row["input"]["decision"]
            assert set(request) == {"state", "question", "kind", "options"}
            assert row["expected_output"] == {
                "probabilities": row["decision"]["target_probabilities"]
            }
        assert cell.intent_report["eval"]["input_type"] == "decision"


def test_late_invalid_native_reference_is_retained_and_blocks_technical_use(settings, tmp_path):
    records = [flat(i) for i in range(10_003)]
    records[-1]["target"] = [1.2]
    ds = corpus(settings, tmp_path, "eval", records)
    prepared = prepare_examples(
        pd.DataFrame([dict(r, source_row=i) for i, r in enumerate(records)]), "eval"
    )
    import_version(
        ds, prepared.astype(object).where(pd.notna(prepared), None).to_dict(orient="records")
    )
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
    ds = corpus(
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


@pytest.mark.parametrize("change", ["remove", "decision", "reference"])
def test_native_repairs_record_impact_and_preserve_prior_decisions(settings, tmp_path, change):
    ds = corpus(settings, tmp_path, "eval", [flat(), flat()])
    prepared = prepare_examples(
        pd.DataFrame([dict(flat(), source_row=i) for i in range(2)]), "eval"
    )
    original = import_version(
        ds, prepared.astype(object).where(pd.notna(prepared), None).to_dict(orient="records")
    )
    changed = list(store.iter_rows(paths.cell_path(ds.pk, original.pk)))
    if change == "remove":
        changed = changed[:1]
    elif change == "decision":
        for row in changed:
            row["decision"]["target_probabilities"] = [0.0, 1.0]
    else:
        for row in changed:
            row["expected_output"] = {"probabilities": [0.0, 1.0]}
    import_version(ds, changed)
    ds.refresh_from_db()
    assert ds.active_cell.id != original.id
    original.refresh_from_db()
    assert original.rows == 2
    assert original.state == "ok"
    assert ds.active_cell.review["input_fingerprint"] == original.fingerprint
