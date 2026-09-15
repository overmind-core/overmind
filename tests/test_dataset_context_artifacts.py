from __future__ import annotations

import json

from overbae.services.artifact_model import ARTIFACT_KINDS
from overbae.services.dataset_context import artifacts as A  # noqa: N812
from overbae.services.dataset_context import prompt as P  # noqa: N812
from overbae.services.dataset_context import spine as S  # noqa: N812


def _valid_card() -> dict:
    return {
        "format": "classification",
        "schema": {
            "kind": "classification",
            "roles": {"input": ["text"], "label": ["sentiment"]},
            "fields": {"text": {"type": "str"}, "sentiment": {"type": "categorical"}},
        },
        "distribution": {"columns": {"text": {"null_rate": 0.0}, "sentiment": {"null_rate": 0.0}}},
        "label_space": {"column": "sentiment", "classes": 3, "balance": "skewed"},
        "target_stats": {"class_stats": [{"label": "pos", "count": 10}]},
        "volume_and_tokens": {"total_rows": 100, "total_files": 1},
        "slices": [{"id": "cluster:0", "kind": "cluster", "label": "topic", "size": 40}],
        "hygiene": {"exact_duplicate_rows": 2, "pii_columns": []},
        "io_mapping": {"text": "input", "sentiment": "label"},
        "reference": {"columns": ["sentiment"], "description": "the gold label"},
        "quality_signals": ["label matches text sentiment"],
        "failure_modes": ["mislabeled rows"],
        "summary": "A sentiment classification dataset with three skewed classes.",
        "provenance": {
            "dataset_id": "ds-1",
            "checkpoint": 3,
            "data_version": "abc123def456",
            "columns": ["text", "sentiment"],
            "row_ids": ["sig1", "sig2"],
        },
    }


def _spine() -> dict:
    return {
        "format": "classification",
        "schema": {
            "kind": "classification",
            "roles": {"input": ["text"], "label": ["sentiment"]},
            "fields": {"text": {"type": "str"}, "sentiment": {"type": "categorical"}},
        },
        "distribution": {"columns": {"text": {}, "sentiment": {}}},
        "label_space": {"column": "sentiment", "classes": 3, "balance": "skewed"},
        "target_stats": {},
        "volume_and_tokens": {"total_rows": 100},
        "slices": [],
        "hygiene": {"exact_duplicate_rows": 2, "pii_columns": [{"column": "text", "count": 5}]},
        "provenance": {
            "dataset_id": "ds-1",
            "checkpoint": 3,
            "data_version": "v0",
            "columns": ["text", "sentiment"],
            "row_ids": ["sig1", "sig2"],
        },
    }


def test_extended_artifact_kinds_present():
    for kind in ("dataset_card", "distribution", "slice_index", "hygiene", "contract_conformance"):
        assert kind in ARTIFACT_KINDS


def test_compute_data_version_is_deterministic_and_order_independent():
    v1 = A.compute_data_version(["a", "b", "c"], columns=["x", "y"])
    v2 = A.compute_data_version(["c", "b", "a"], columns=["y", "x"])
    assert v1 == v2
    assert len(v1) == 16


def test_compute_data_version_changes_with_columns_and_rows():
    base = A.compute_data_version(["a", "b"], columns=["x"])
    assert base != A.compute_data_version(["a", "b", "c"], columns=["x"])
    assert base != A.compute_data_version(["a", "b"], columns=["x", "y"])


def test_compute_data_version_stable_for_empty():
    assert A.compute_data_version([]) == A.compute_data_version([], columns=[])


def test_valid_card_passes_validation():
    assert A.validate_dataset_card(_valid_card()) == []


def test_validation_flags_missing_and_empty_fields():
    card = _valid_card()
    del card["format"]
    card["io_mapping"] = {}
    errors = A.validate_dataset_card(card)
    assert any("format" in e for e in errors)
    assert any("io_mapping" in e for e in errors)


def test_validation_flags_bad_provenance():
    card = _valid_card()
    card["provenance"] = {"columns": ["text"]}
    assert any("data_version" in e for e in A.validate_dataset_card(card))

    card["provenance"] = {"data_version": "x"}
    assert any("columns" in e for e in A.validate_dataset_card(card))


def test_non_dict_card_is_invalid():
    assert A.validate_dataset_card(None)
    assert A.validate_dataset_card("nope")


def test_normalize_coerces_unknown_format_and_roles():
    card = _valid_card()
    card["format"] = "weird_format"
    card["io_mapping"] = {"text": "INPUT", "sentiment": "bogus_role"}
    norm = A.normalize_dataset_card(card)
    assert norm["format"] == "unknown"
    assert norm["io_mapping"]["text"] == "input"
    assert norm["io_mapping"]["sentiment"] == "metadata"


def test_normalize_fills_all_keys_with_defaults():
    norm = A.normalize_dataset_card({"format": "tabular", "summary": "x"})
    for key in (
        "schema",
        "distribution",
        "label_space",
        "target_stats",
        "volume_and_tokens",
        "hygiene",
        "io_mapping",
        "reference",
        "provenance",
    ):
        assert key in norm
    assert norm["slices"] == []
    assert norm["_fallback"] is False
    assert norm["quality_signals"] == []
    assert norm["failure_modes"] == []
    assert norm["label_space_assessment"] == {
        "valid": False,
        "reason": "",
        "proposed_label_column": "",
    }
    assert norm["slice_exemplars"] == []
    assert norm["contract_conformance"] == []


def test_normalize_coerces_legacy_string_failure_modes_and_signals():
    card = _valid_card()
    card["failure_modes"] = ["mislabeled rows", "", "  "]
    card["quality_signals"] = ["label matches text sentiment"]
    norm = A.normalize_dataset_card(card)
    assert norm["failure_modes"] == [
        {"description": "mislabeled rows", "detection_pattern": "", "example_row_ids": []}
    ]
    assert norm["quality_signals"] == [
        {"signal": "label matches text sentiment", "severity": "weighted"}
    ]


def test_normalize_preserves_structured_failure_modes_and_baselines():
    card = _valid_card()
    card["failure_modes"] = [
        {
            "description": "empty text",
            "detection_pattern": r"^\s*$",
            "example_row_ids": ["sig1"],
            "baseline_match_rate": 0.25,
            "matched_rows": 25,
        },
        {"description": ""},  # description-less entries dropped
        "legacy entry",
    ]
    card["quality_signals"] = [
        {
            "signal": "parses as JSON",
            "severity": "gate",
            "detection_pattern": "json",
            "baseline_match_rate": 0.5,
            "matched_rows": 50,
        },
        {"signal": "concise", "severity": "nonsense"},
    ]
    norm = A.normalize_dataset_card(card)
    assert norm["failure_modes"][0] == {
        "description": "empty text",
        "detection_pattern": r"^\s*$",
        "example_row_ids": ["sig1"],
        "baseline_match_rate": 0.25,
        "matched_rows": 25,
    }
    assert norm["failure_modes"][1] == {
        "description": "legacy entry",
        "detection_pattern": "",
        "example_row_ids": [],
    }
    assert norm["quality_signals"][0] == {
        "signal": "parses as JSON",
        "severity": "gate",
        "detection_pattern": "json",
        "baseline_match_rate": 0.5,
        "matched_rows": 50,
    }
    assert norm["quality_signals"][1] == {"signal": "concise", "severity": "weighted"}


def test_normalize_io_mapping_allows_dotted_keys():
    card = _valid_card()
    card["io_mapping"] = {"metadata": "metadata", "metadata.note": "INPUT", "": "input"}
    norm = A.normalize_dataset_card(card)
    assert norm["io_mapping"] == {"metadata": "metadata", "metadata.note": "input"}


def test_normalize_slice_exemplars_and_contract_conformance():
    card = _valid_card()
    card["slice_exemplars"] = [
        {
            "slice": "cluster:0",
            "good_row_ids": ["sig1"],
            "bad_row_ids": ["sig2"],
            "criteria": "grounded",
        },
        {"slice": "", "good_row_ids": [], "bad_row_ids": [], "criteria": ""},  # dropped
        "not a dict",  # dropped
    ]
    card["contract_conformance"] = [
        {
            "contract_field": "verdict",
            "satisfied": False,
            "violation_rate_estimate": 7,
            "violating_row_ids": ["sig1"],
            "notes": "x",
        },
        {"contract_field": ""},  # dropped
    ]
    norm = A.normalize_dataset_card(card)
    assert norm["slice_exemplars"] == [
        {
            "slice": "cluster:0",
            "good_row_ids": ["sig1"],
            "bad_row_ids": ["sig2"],
            "criteria": "grounded",
        }
    ]
    assert norm["contract_conformance"] == [
        {
            "contract_field": "verdict",
            "satisfied": False,
            "violation_rate_estimate": 1.0,
            "violating_row_ids": ["sig1"],
            "notes": "x",
        }
    ]
    assert A.validate_dataset_card(norm) == []


def test_fallback_card_is_never_empty_and_valid():
    card = A.normalize_dataset_card(A.build_fallback_card(_spine()))
    assert card["_fallback"] is True
    assert card["format"] == "classification"
    assert card["io_mapping"]["text"] == "input"
    assert card["io_mapping"]["sentiment"] == "label"
    assert card["summary"]
    assert A.validate_dataset_card(card) == []
    assert any("PII" in fm["description"] for fm in card["failure_modes"])
    assert any("duplicate" in fm["description"] for fm in card["failure_modes"])
    assert all(
        fm["detection_pattern"] == "" and fm["example_row_ids"] == []
        for fm in card["failure_modes"]
    )
    assert all(qs["severity"] == "weighted" for qs in card["quality_signals"])
    assert card["label_space_assessment"] == {
        "valid": False,
        "reason": "",
        "proposed_label_column": "",
    }
    assert card["slice_exemplars"] == []
    assert card["contract_conformance"] == []


def test_fallback_maps_fields_to_metadata_when_no_roles():
    spine = _spine()
    spine["schema"] = {"fields": {"a": {}, "b": {}}, "roles": {}}
    spine["distribution"] = {"columns": {"a": {}, "b": {}}}
    spine["provenance"]["columns"] = ["a", "b"]
    card = A.normalize_dataset_card(A.build_fallback_card(spine))
    assert set(card["io_mapping"]) == {"a", "b"}
    assert all(role == "metadata" for role in card["io_mapping"].values())


def test_fallback_io_mapping_skips_null_and_unknown_columns():
    spine = _spine()
    # A stray role entry naming a null column + a column not in the dataset must be dropped.
    spine["schema"]["roles"] = {
        "input": ["text", None],
        "label": ["sentiment"],
        "target": ["ghost"],
    }
    card = A.normalize_dataset_card(A.build_fallback_card(spine))
    assert set(card["io_mapping"]) == {"text", "sentiment"}
    assert "None" not in card["io_mapping"]
    assert "ghost" not in card["io_mapping"]


def test_write_and_load_bundle_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(A.settings, "MEDIA_ROOT", tmp_path)
    card = A.normalize_dataset_card(_valid_card())
    sample = [{"__row_id__": "sig1", "text": "good", "sentiment": "pos"}]

    result = A.write_dataset_bundle("ds-1", "v123", card, sample_rows=sample)
    bundle_dir = A.dataset_bundle_dir("ds-1", "v123")
    assert bundle_dir.is_dir()
    for fname in (
        A.DATASET_CARD_FILE,
        A.SCHEMA_FILE,
        A.DISTRIBUTION_FILE,
        A.SLICES_FILE,
        A.HYGIENE_FILE,
        A.SAMPLE_FILE,
        A.MANIFEST_FILE,
    ):
        assert (bundle_dir / fname).is_file()

    raw = (bundle_dir / A.DATASET_CARD_FILE).read_text(encoding="utf-8")
    assert json.loads(raw)["format"] == card["format"]

    manifest = result["manifest"]
    assert manifest["dataset_key"] == "ds-1"
    assert manifest["data_version"] == "v123"
    kinds = {a["kind"] for a in manifest["artifacts"]}
    assert {
        "dataset_card",
        "schema",
        "distribution",
        "slice_index",
        "hygiene",
        "contract_conformance",
    } <= kinds

    loaded = A.load_dataset_bundle("ds-1", "v123")
    assert loaded is not None
    assert loaded["card"]["summary"] == card["summary"]
    assert loaded["io_mapping"] == card["io_mapping"]
    assert loaded["data_version"] == "v123"


def test_manifest_embeds_artifact_content_and_provenance(tmp_path, monkeypatch):
    monkeypatch.setattr(A.settings, "MEDIA_ROOT", tmp_path)
    card = A.normalize_dataset_card(_valid_card())
    result = A.write_dataset_bundle("ds-1", "v1", card)
    by_id = {a["id"]: a for a in result["manifest"]["artifacts"]}
    # Each artifact embeds its content so the MCP server needs no ORM.
    assert by_id["dataset:dataset_card"]["content"]["format"] == "classification"
    assert "columns" in by_id["dataset:distribution"]["content"]["distribution"]
    # The conformance join is an addressable artifact ([] when no codebase source).
    assert by_id["dataset:contract_conformance"]["content"] == {"contract_conformance": []}
    prov = by_id["dataset:schema"]["provenance"]
    assert prov["source_id"] == "dataset:ds-1:v1"
    assert prov["columns"] == ["text", "sentiment"]
    assert prov["row_ids"] == ["sig1", "sig2"]


def test_latest_version_resolution(tmp_path, monkeypatch):
    monkeypatch.setattr(A.settings, "MEDIA_ROOT", tmp_path)
    card = A.normalize_dataset_card(_valid_card())
    A.write_dataset_bundle("ds-2", "v1", card)
    A.write_dataset_bundle("ds-2", "v2", card)
    assert A.latest_data_version("ds-2") == "v2"
    loaded = A.load_dataset_bundle("ds-2")  # version omitted ⇒ latest
    assert loaded is not None
    assert loaded["data_version"] == "v2"


def test_load_bundle_missing_returns_none(tmp_path, monkeypatch):
    monkeypatch.setattr(A.settings, "MEDIA_ROOT", tmp_path)
    assert A.load_dataset_bundle("does-not-exist") is None


def _profile_fixture() -> dict:
    return {
        "total_rows_exact": 200,
        "data_type": "tabular_mixed",
        "data_format": "flat",
        "numeric_columns": ["score"],
        "conversation_anatomy": {"detected": False, "format": "flat"},
        "token_budget_fit": {"fits": True},
        "data_smells": [],
        "files": [
            {
                "name": "data.csv",
                "kind": "csv",
                "rows_exact": 200,
                "column_stats": [
                    {
                        "column": "text",
                        "is_numeric": False,
                        "null_rate": 0.0,
                        "top_values": [{"value": "hi", "count": 3}],
                    },
                    {
                        "column": "label",
                        "is_numeric": False,
                        "null_rate": 0.0,
                        "top_values": [{"value": "pos", "count": 120}],
                    },
                    {"column": "score", "is_numeric": True, "null_rate": 0.0, "min": 0, "max": 1},
                ],
            }
        ],
    }


def _report_fixture() -> dict:
    return {
        "summary": {"rows": 200, "files": 1, "columns": ["text", "label", "score"]},
        "label_distribution": {
            "column": "label",
            "classes": 2,
            "balance": "skewed",
            "bars": [{"label": "pos", "count": 120}, {"label": "neg", "count": 80}],
        },
        "class_stats": [{"label": "pos", "count": 120, "pct_of_total": 0.6}],
        "token_distribution": {"p50": 20, "p95": 80, "max": 120},
        "clusters": [
            {
                "id": 0,
                "size": 120,
                "label": "positive",
                "pct_of_total": 0.6,
                "top_features": [],
                "examples": [],
            }
        ],
        "redundancy": [{"row_ids": ["a", "b"], "similarity": 0.99}],
        "outliers": [],
    }


def test_build_spine_from_profile_and_report():
    spine = S.build_spine(
        profile=_profile_fixture(),
        report=_report_fixture(),
        rows=[{"text": "hi", "label": "pos"}, {"text": "hi", "label": "pos"}],
        manifest={
            "kind": "classification",
            "roles": {"input": ["text"], "label": ["label"]},
            "fields": {"text": {}, "label": {}, "score": {}},
        },
        smells={
            "data_smells": [
                {"type": "dupes", "message": "dup rows", "count": 1, "row_ids": ["sig"]}
            ]
        },
        dataset_id="ds-9",
        dataset_version=2,
        data_version="hash9",
        row_ids=["sig1", "sig2"],
    )
    assert spine["format"] == "classification"
    assert "text" in spine["distribution"]["columns"]
    assert spine["label_space"]["column"] == "label"
    assert spine["volume_and_tokens"]["total_rows"] == 200
    kinds = {s["kind"] for s in spine["slices"]}
    assert {"cluster", "smell"} <= kinds
    # Two identical rows ⇒ one exact duplicate.
    assert spine["hygiene"]["exact_duplicate_rows"] == 1
    assert spine["hygiene"]["near_duplicate_pairs"] == 1
    assert spine["provenance"]["data_version"] == "hash9"
    assert spine["provenance"]["columns"] == ["text", "label", "score"]


def test_spine_to_fallback_card_is_valid():
    spine = S.build_spine(
        profile=_profile_fixture(),
        report=_report_fixture(),
        rows=[{"text": "hi", "label": "pos"}],
        manifest={
            "kind": "classification",
            "roles": {"input": ["text"], "label": ["label"]},
            "fields": {"text": {}, "label": {}},
        },
        smells=None,
        dataset_id="ds-9",
        dataset_version=2,
        data_version="hash9",
        row_ids=["sig1"],
    )
    card = A.normalize_dataset_card(A.build_fallback_card(spine))
    assert A.validate_dataset_card(card) == []
    assert card["io_mapping"]["text"] == "input"
    assert card["io_mapping"]["label"] == "label"


def test_build_dataset_card_falls_back_when_report_has_no_block():
    card = P.build_dataset_card(_spine())
    assert card["_fallback"] is True
    assert A.validate_dataset_card(card) == []

    card = P.build_dataset_card(_spine(), report={"dataset_card": {}})
    assert card["_fallback"] is True
    assert A.validate_dataset_card(card) == []


def test_build_dataset_card_uses_report_semantic_layer_when_valid():
    report = {
        "dataset_card": {
            "io_mapping": {"text": "input", "sentiment": "label", "ghost": "label"},
            "reference_columns": ["sentiment", "ghost"],
            "reference_description": "gold label",
            "quality_signals": ["clean"],
            "failure_modes": ["noisy"],
            "summary": "a capability-written dataset summary",
        }
    }
    card = P.build_dataset_card(_spine(), report=report)
    assert card["_fallback"] is False
    assert card["summary"] == "a capability-written dataset summary"
    assert card["io_mapping"]["sentiment"] == "label"
    assert "ghost" not in card["io_mapping"]
    assert card["reference"]["columns"] == ["sentiment"]
    assert A.validate_dataset_card(card) == []
    assert card["quality_signals"] == [{"signal": "clean", "severity": "weighted"}]
    assert card["failure_modes"] == [
        {"description": "noisy", "detection_pattern": "", "example_row_ids": []}
    ]


def test_fusion_keeps_dotted_io_mapping_keys_with_real_root():
    report = {
        "dataset_card": {
            "io_mapping": {
                "text": "input",
                "text.note": "metadata",  # root "text" exists → kept
                "ghost.note": "input",  # root "ghost" absent → dropped
                "sentiment": "label",
            },
            "summary": "dotted mapping card",
        }
    }
    card = P.build_dataset_card(_spine(), report=report)
    assert card["io_mapping"] == {
        "text": "input",
        "text.note": "metadata",
        "sentiment": "label",
    }


def test_fusion_restricts_cited_row_ids_to_known_spine_ids():
    report = {
        "dataset_card": {
            "io_mapping": {"text": "input", "sentiment": "label"},
            "failure_modes": [
                {
                    "description": "empty text",
                    "detection_pattern": r"^\s*$",
                    "example_row_ids": ["sig1", "invented"],
                }
            ],
            "slice_exemplars": [
                {
                    "slice": "cluster:0",
                    "good_row_ids": ["sig1", "made-up"],
                    "bad_row_ids": ["sig2"],
                    "criteria": "non-empty text",
                }
            ],
            "summary": "restricted ids card",
        }
    }
    card = P.build_dataset_card(_spine(), report=report)  # spine knows sig1 + sig2
    assert card["failure_modes"][0]["example_row_ids"] == ["sig1"]
    assert card["failure_modes"][0]["detection_pattern"] == r"^\s*$"
    assert card["slice_exemplars"] == [
        {
            "slice": "cluster:0",
            "good_row_ids": ["sig1"],
            "bad_row_ids": ["sig2"],
            "criteria": "non-empty text",
        }
    ]


def test_fusion_passes_label_space_assessment_and_conformance_through():
    report = {
        "dataset_card": {
            "io_mapping": {"text": "input"},
            "label_space_assessment": {
                "valid": False,
                "reason": "label column is a timestamp",
                "proposed_label_column": "sentiment",
            },
            "contract_conformance": [
                {
                    "contract_field": "verdict",
                    "satisfied": True,
                    "violation_rate_estimate": 0.0,
                    "violating_row_ids": [],
                    "notes": "",
                },
                "not a dict",  # dropped
            ],
            "summary": "assessment card",
        }
    }
    card = P.build_dataset_card(_spine(), report=report, codebase_card={"name": "capability"})
    assert card["label_space_assessment"] == {
        "valid": False,
        "reason": "label column is a timestamp",
        "proposed_label_column": "sentiment",
    }
    assert card["contract_conformance"] == [
        {
            "contract_field": "verdict",
            "satisfied": True,
            "violation_rate_estimate": 0.0,
            "violating_row_ids": [],
            "notes": "",
        }
    ]

    # Without a codebase card the field is defined as [] — capability-guessed entries are dropped.
    ungated = P.build_dataset_card(_spine(), report=report)
    assert ungated["contract_conformance"] == []


_HEX_A = "a" * 40
_HEX_B = "b" * 40
_HEX_C = "c" * 40


def _evidence_report() -> dict:
    return {
        **_report_fixture(),
        "eval_readiness": {
            "score": 50,
            "issues": [
                {
                    "id": "dup-1",
                    "type": "duplicate_rows",
                    "severity": "warn",
                    "detail": "2 duplicate rows",
                    "row_signatures": [_HEX_A, _HEX_B, _HEX_A],
                },
                {
                    "id": "imb",
                    "type": "class_imbalance",
                    "severity": "warn",
                    "row_signatures": [],
                },  # no rows → no slice
                "garbled",  # odd shape → skipped
            ],
        },
        "proposed_fixes": [
            {
                "id": "fix-1",
                "title": "Drop empty rows",
                "category": "auto",
                "scope": {
                    "fields": ["text"],
                    "selector": {"signatures": [_HEX_C, "not-a-signature"]},
                },
            },
            {"id": "fix-2", "title": "No scope fix", "scope": {}},  # no ids → no slice
        ],
        "agenda_coverage": {
            "intents": [
                {
                    "intent": "refund requests",
                    "coverage_score": 0.7,
                    "example_rows": [_HEX_A, "prose example, not an id"],
                },
                {"intent": "uncited intent", "example_rows": []},
            ],
        },
    }


def test_spine_slices_populated_from_report_evidence():
    spine = S.build_spine(
        profile=_profile_fixture(),
        report=_evidence_report(),
        rows=[],
        manifest=None,
        smells=None,
        data_version="hashX",
        row_ids=[_HEX_A, _HEX_B, _HEX_C],
    )
    by_id = {s["id"]: s for s in spine["slices"]}

    issue = by_id["issue:dup-1"]
    assert issue["kind"] == "issue"
    assert issue["row_ids"] == [_HEX_A, _HEX_B]  # de-duped, order preserved
    assert issue["size"] == 2
    assert "issue:imb" not in by_id  # empty evidence → no slice

    fix = by_id["fix:fix-1"]
    assert fix["kind"] == "fix_scope"
    assert fix["row_ids"] == [_HEX_C]
    assert "fix:fix-2" not in by_id

    intent = by_id["intent:0"]
    assert intent["kind"] == "intent"
    assert intent["label"] == "refund requests"
    assert intent["row_ids"] == [_HEX_A]
    assert "intent:1" not in by_id

    assert any(s["kind"] == "cluster" for s in spine["slices"])


def test_spine_slices_guarded_against_odd_shapes():
    report = _report_fixture()
    report["eval_readiness"] = {"issues": "not-a-list"}
    report["proposed_fixes"] = [None, {"scope": "nope"}]
    report["agenda_coverage"] = {"intents": [42]}
    spine = S.build_spine(
        profile=_profile_fixture(),
        report=report,
        rows=[],
        data_version="h",
        row_ids=[],
    )
    assert all(s["kind"] in ("cluster", "smell") for s in spine["slices"])


def test_apply_pattern_baselines_records_match_rates():
    from overbae.tasks.dataset_context import apply_pattern_baselines

    card = {
        "failure_modes": [
            {"description": "contains ERROR", "detection_pattern": r"ERROR", "example_row_ids": []},
            {"description": "not regex-detectable", "detection_pattern": "", "example_row_ids": []},
            {
                "description": "invalid pattern",
                "detection_pattern": r"([unclosed",
                "example_row_ids": [],
            },
        ],
        "quality_signals": [
            {"signal": "no panic marker", "severity": "gate", "detection_pattern": r"panic"},
            {
                "signal": "weighted with pattern is skipped",
                "severity": "weighted",
                "detection_pattern": r"ERROR",
            },
            {"signal": "gate without pattern is skipped", "severity": "gate"},
        ],
    }
    rows = [
        {"text": "all good"},
        {"text": "ERROR: failed to parse"},
        {"text": "panic at the disco"},
        {"text": "ERROR again"},
    ]
    out = apply_pattern_baselines(card, rows, manifest=None)

    fm = out["failure_modes"]
    assert fm[0]["matched_rows"] == 2
    assert fm[0]["baseline_match_rate"] == 0.5
    # Empty + invalid patterns are skipped, never crash, never annotate.
    assert "baseline_match_rate" not in fm[1]
    assert "baseline_match_rate" not in fm[2]

    qs = out["quality_signals"]
    assert qs[0]["matched_rows"] == 1
    assert qs[0]["baseline_match_rate"] == 0.25
    assert "baseline_match_rate" not in qs[1]
    assert "baseline_match_rate" not in qs[2]


def test_apply_pattern_baselines_no_rows_records_nothing():
    from overbae.tasks.dataset_context import apply_pattern_baselines

    card = {
        "failure_modes": [{"description": "x", "detection_pattern": r"y", "example_row_ids": []}],
        "quality_signals": [],
    }
    out = apply_pattern_baselines(card, [], manifest=None)
    assert "baseline_match_rate" not in out["failure_modes"][0]


def test_baselines_survive_bundle_write(tmp_path, monkeypatch):
    from overbae.tasks.dataset_context import apply_pattern_baselines

    monkeypatch.setattr(A.settings, "MEDIA_ROOT", tmp_path)
    card = A.normalize_dataset_card(_valid_card())
    card["failure_modes"] = [
        {"description": "contains bad", "detection_pattern": r"bad", "example_row_ids": []}
    ]
    card = apply_pattern_baselines(card, [{"text": "bad row"}, {"text": "fine"}], None)

    result = A.write_dataset_bundle("ds-base", "v1", card)
    stored = result["manifest"]["card"]["failure_modes"][0]
    assert stored["baseline_match_rate"] == 0.5
    assert stored["matched_rows"] == 1
