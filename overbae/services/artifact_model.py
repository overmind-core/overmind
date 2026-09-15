"""Typed in-run artifact + provenance records.

``to_manifest_entry()`` is additive: it preserves the legacy
``{file,key,purpose,when}`` keys other readers already rely on and only adds
``provenance`` + ``schema_version``.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

ARTIFACT_SCHEMA_VERSION = 1

# ``evidence`` = rows/spans grounding a finding; ``derived_table`` = a computed
# table (e.g. row-pair sidecars).
ARTIFACT_KINDS = frozenset(
    {
        "facts",
        "schema",
        "row_sample",
        "evidence",
        "derived_table",
        "script",
        "artifact",
        # Codebase context: the product capability behind a dataset, from its source repo.
        "capability_card",
        "io_schema",
        "tool_spec",
        "vocabulary",
        "prompt_spec",
        # Dataset context: mirrors the codebase bundle, keyed per data version.
        "dataset_card",
        "distribution",
        "slice_index",
        "hygiene",
        # Per-output-field join against the capability card; [] with no codebase source.
        "contract_conformance",
    }
)


@dataclass(frozen=True)
class Provenance:
    source_id: str
    produced_by: str
    row_ids: tuple[str, ...] = ()
    spans: tuple[str, ...] = ()
    score_ids: tuple[str, ...] = ()
    columns: tuple[str, ...] = ()

    def to_dict(self) -> dict:
        # Drop empty collections to keep the manifest compact.
        out: dict = {"source_id": self.source_id, "produced_by": self.produced_by}
        for k in ("row_ids", "spans", "score_ids", "columns"):
            v = getattr(self, k)
            if v:
                out[k] = list(v)
        return out


@dataclass(frozen=True)
class Artifact:
    id: str
    kind: str
    path: str
    summary: str
    provenance: Provenance | None = None
    schema_version: int = ARTIFACT_SCHEMA_VERSION
    legacy: dict = field(default_factory=dict)

    def to_manifest_entry(self) -> dict:
        kind = self.kind if self.kind in ARTIFACT_KINDS else "artifact"
        entry: dict = {
            "id": self.id,
            "kind": kind,
            "path": self.path,
            "summary": self.summary,
            "schema_version": self.schema_version,
        }
        if self.provenance is not None:
            entry["provenance"] = self.provenance.to_dict()
        for k, v in (self.legacy or {}).items():
            entry.setdefault(k, v)
        return entry

    def to_dict(self) -> dict:
        d = asdict(self)
        d["provenance"] = self.provenance.to_dict() if self.provenance else None
        return d
