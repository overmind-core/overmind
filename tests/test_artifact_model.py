from __future__ import annotations

from overbae.services.artifact_model import (
    ARTIFACT_SCHEMA_VERSION,
    Artifact,
    Provenance,
)


def test_provenance_drops_empty_collections():
    p = Provenance(source_id="dataset", produced_by="profiler")
    assert p.to_dict() == {"source_id": "dataset", "produced_by": "profiler"}


def test_provenance_keeps_populated_collections():
    p = Provenance(
        source_id="dataset", produced_by="bundle", row_ids=("a", "b"), columns=("input",)
    )
    d = p.to_dict()
    assert d["row_ids"] == ["a", "b"]
    assert d["columns"] == ["input"]
    assert "spans" not in d  # empty dropped


def test_artifact_manifest_entry_has_typed_provenance_and_legacy():
    a = Artifact(
        id="smells",
        kind="evidence",
        path="smells.json",
        summary="pre-detected smells",
        provenance=Provenance(source_id="dataset", produced_by="workshop_bundle"),
        legacy={"file": "smells.json", "key": "smells", "purpose": "x", "when": "y"},
    )
    e = a.to_manifest_entry()
    assert e["id"] == "smells"
    assert e["kind"] == "evidence"
    assert e["schema_version"] == ARTIFACT_SCHEMA_VERSION
    assert e["provenance"] == {"source_id": "dataset", "produced_by": "workshop_bundle"}
    assert e["file"] == "smells.json" and e["key"] == "smells"  # legacy retained


def test_unknown_kind_falls_back_to_artifact():
    a = Artifact(id="x", kind="bogus", path="x.json", summary="s")
    assert a.to_manifest_entry()["kind"] == "artifact"
