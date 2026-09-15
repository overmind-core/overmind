"""Typed, provenanced Dataset Capability Card + artifact bundle.

Written deterministically to ``MEDIA_ROOT/dataset/<dataset_key>/<data_version>/``.
The manifest EMBEDS each artifact's content so the standalone dataset-context MCP
server can serve every kind from the copied ``dataset_manifest.json`` alone.
"""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
from typing import Any

from django.conf import settings

from overbae.services.artifact_model import Artifact, Provenance

logger = logging.getLogger(__name__)

CARD_SCHEMA_VERSION = 1

# Anything else collapses to ``"unknown"``.
DATASET_FORMATS: tuple[str, ...] = (
    "sft_messages",
    "prompt_completion",
    "classification",
    "tabular",
    "unknown",
)

# The product role a column plays w.r.t. the capability's I/O contract.
IO_ROLES: tuple[str, ...] = ("input", "reference", "label", "metadata")

# Allowed severities for a quality signal ("gate" = hard eval filter, "weighted" = gradient).
SIGNAL_SEVERITIES: tuple[str, ...] = ("gate", "weighted")

# DatasetContextSource copies only dataset_card.json into the workshop dir.
DATASET_CARD_FILE = "dataset_card.json"
SCHEMA_FILE = "schema.json"
DISTRIBUTION_FILE = "distribution.json"
SLICES_FILE = "slices.json"
HYGIENE_FILE = "hygiene.json"
SAMPLE_FILE = "sample.jsonl"
MANIFEST_FILE = "dataset_manifest.json"


def dataset_bundle_dir(dataset_key: str, data_version: str) -> Path:
    return Path(settings.MEDIA_ROOT) / "dataset" / str(dataset_key) / str(data_version)


def dataset_versions_dir(dataset_key: str) -> Path:
    return Path(settings.MEDIA_ROOT) / "dataset" / str(dataset_key)


def _canonical(value: Any) -> str:
    """Stable JSON encoding for hashing: sorted keys, no whitespace drift."""
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str)


def compute_data_version(
    row_ids: Any = None,
    *,
    columns: Any = None,
    extra: Any = None,
) -> str:
    """A deterministic content hash identifying one dataset version.

    Row ids are sorted, so two runs over byte-identical rows agree regardless of
    row order; ``columns`` are folded in so a schema change also yields a new
    version. Empty inputs hash to a stable sentinel.
    """
    sigs = sorted({str(s) for s in (row_ids or []) if str(s).strip()})
    cols = sorted({str(c) for c in (columns or []) if str(c).strip()})
    payload = _canonical({"row_ids": sigs, "columns": cols, "extra": extra or {}})
    digest = hashlib.sha1(payload.encode("utf-8")).hexdigest()  # noqa: S324 — identity, not security
    return digest[:16]


def _provenance_columns(card: dict[str, Any]) -> list[str]:
    prov = card.get("provenance")
    if not isinstance(prov, dict):
        return []
    cols = prov.get("columns")
    if not isinstance(cols, list):
        return []
    return [str(c).strip() for c in cols if str(c).strip()]


def _provenance_row_ids(card: dict[str, Any]) -> list[str]:
    prov = card.get("provenance")
    if not isinstance(prov, dict):
        return []
    ids = prov.get("row_ids")
    if not isinstance(ids, list):
        return []
    return [str(r).strip() for r in ids if str(r).strip()]


def validate_dataset_card(card: Any) -> list[str]:
    """Return a list of human-readable problems with ``card`` (empty list ⇒ valid).

    Deliberately strict enough to trigger a deterministic fallback, lenient enough
    not to loop.
    """
    if not isinstance(card, dict):
        return ["dataset_card must be a JSON object"]

    errors: list[str] = []

    fmt = card.get("format")
    if not isinstance(fmt, str) or not fmt.strip():
        errors.append("'format' must be a non-empty string")

    schema = card.get("schema")
    if not isinstance(schema, dict) or not schema:
        errors.append("'schema' must be a non-empty object describing roles/fields")

    io_mapping = card.get("io_mapping")
    if not isinstance(io_mapping, dict) or not io_mapping:
        errors.append("'io_mapping' must map at least one column to input/reference/label/metadata")

    summary = card.get("summary")
    if not isinstance(summary, str) or not summary.strip():
        errors.append("'summary' must be a non-empty description of the dataset")

    prov = card.get("provenance")
    if not isinstance(prov, dict):
        errors.append("'provenance' must be an object carrying data_version + columns")
    else:
        if not str(prov.get("data_version") or "").strip():
            errors.append("'provenance.data_version' must be a non-empty content-hash string")
        if not _provenance_columns(card):
            errors.append("'provenance.columns' must name at least one column")

    return errors


def _as_str_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(v).strip() for v in value if str(v).strip()]


def _coerce_format(value: Any) -> str:
    fmt = str(value or "").strip().lower()
    return fmt if fmt in DATASET_FORMATS else "unknown"


def _coerce_role(value: Any) -> str:
    role = str(value or "").strip().lower()
    return role if role in IO_ROLES else "metadata"


def _coerce_io_mapping(value: Any) -> dict[str, str]:
    """Coerce ``io_mapping`` (column -> role); unknown roles → ``metadata``.

    Keys may be dotted sub-field paths (``"metadata.note"``); the fusion layer
    guarantees the root column exists, so they pass through unchanged.
    """
    if not isinstance(value, dict):
        return {}
    out: dict[str, str] = {}
    for key, role in value.items():
        col = str(key).strip()
        if col:
            out[col] = _coerce_role(role)
    return out


def _as_obj(value: Any) -> dict[str, Any]:
    return {str(k): v for k, v in value.items()} if isinstance(value, dict) else {}


def _coerce_failure_modes(value: Any) -> list[dict[str, Any]]:
    """Structured failure modes; legacy plain-string entries coerce to the structured shape.

    Kernel-computed ``baseline_match_rate`` / ``matched_rows`` must survive
    re-normalization — the bundle write normalizes again after the baseline pass.
    """
    out: list[dict[str, Any]] = []
    for item in value if isinstance(value, list) else []:
        if isinstance(item, dict):
            description = str(item.get("description") or "").strip()
            if not description:
                continue
            entry: dict[str, Any] = {
                "description": description,
                "detection_pattern": str(item.get("detection_pattern") or "").strip(),
                "example_row_ids": _as_str_list(item.get("example_row_ids")),
            }
            if isinstance(item.get("baseline_match_rate"), (int, float)):
                entry["baseline_match_rate"] = float(item["baseline_match_rate"])
            if isinstance(item.get("matched_rows"), int):
                entry["matched_rows"] = item["matched_rows"]
            out.append(entry)
        else:
            description = str(item or "").strip()
            if description:
                out.append(
                    {"description": description, "detection_pattern": "", "example_row_ids": []}
                )
    return out


def _coerce_quality_signals(value: Any) -> list[dict[str, Any]]:
    """Severity-tagged quality signals; legacy plain strings coerce to severity=weighted."""
    out: list[dict[str, Any]] = []
    for item in value if isinstance(value, list) else []:
        if isinstance(item, dict):
            signal = str(item.get("signal") or "").strip()
            if not signal:
                continue
            severity = str(item.get("severity") or "").strip().lower()
            entry: dict[str, Any] = {
                "signal": signal,
                "severity": severity if severity in SIGNAL_SEVERITIES else "weighted",
            }
            pattern = str(item.get("detection_pattern") or "").strip()
            if pattern:
                entry["detection_pattern"] = pattern
            if isinstance(item.get("baseline_match_rate"), (int, float)):
                entry["baseline_match_rate"] = float(item["baseline_match_rate"])
            if isinstance(item.get("matched_rows"), int):
                entry["matched_rows"] = item["matched_rows"]
            out.append(entry)
        else:
            signal = str(item or "").strip()
            if signal:
                out.append({"signal": signal, "severity": "weighted"})
    return out


def _coerce_label_space_assessment(value: Any) -> dict[str, Any]:
    obj = _as_obj(value)
    return {
        "valid": bool(obj.get("valid", False)),
        "reason": str(obj.get("reason") or "").strip(),
        "proposed_label_column": str(obj.get("proposed_label_column") or "").strip(),
    }


def _coerce_slice_exemplars(value: Any) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for item in value if isinstance(value, list) else []:
        if not isinstance(item, dict):
            continue
        entry = {
            "slice": str(item.get("slice") or "").strip(),
            "good_row_ids": _as_str_list(item.get("good_row_ids")),
            "bad_row_ids": _as_str_list(item.get("bad_row_ids")),
            "criteria": str(item.get("criteria") or "").strip(),
        }
        if entry["slice"] or entry["good_row_ids"] or entry["bad_row_ids"]:
            out.append(entry)
    return out


def _coerce_contract_conformance(value: Any) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for item in value if isinstance(value, list) else []:
        if not isinstance(item, dict):
            continue
        field_name = str(item.get("contract_field") or "").strip()
        if not field_name:
            continue
        try:
            rate = float(item.get("violation_rate_estimate") or 0.0)
        except (TypeError, ValueError):
            rate = 0.0
        out.append(
            {
                "contract_field": field_name,
                "satisfied": bool(item.get("satisfied", False)),
                "violation_rate_estimate": max(0.0, min(1.0, rate)),
                "violating_row_ids": _as_str_list(item.get("violating_row_ids")),
                "notes": str(item.get("notes") or "").strip(),
            }
        )
    return out


def normalize_dataset_card(card: dict[str, Any]) -> dict[str, Any]:
    """Coerce a card into the canonical shape stored in the bundle.

    Every spine + semantic key gets a safe default so older/partial cards
    round-trip losslessly.
    """
    provenance = _as_obj(card.get("provenance"))
    normalized: dict[str, Any] = {
        "format": _coerce_format(card.get("format")),
        "schema": _as_obj(card.get("schema")),
        "distribution": _as_obj(card.get("distribution")),
        "label_space": _as_obj(card.get("label_space")),
        "target_stats": _as_obj(card.get("target_stats")),
        "volume_and_tokens": _as_obj(card.get("volume_and_tokens")),
        "slices": [s for s in (card.get("slices") or []) if isinstance(s, dict)],
        "hygiene": _as_obj(card.get("hygiene")),
        # Semantic layer: LLM-produced, so defaults must keep a spine-only card valid.
        "io_mapping": _coerce_io_mapping(card.get("io_mapping")),
        "reference": _as_obj(card.get("reference")),
        "quality_signals": _coerce_quality_signals(card.get("quality_signals")),
        "failure_modes": _coerce_failure_modes(card.get("failure_modes")),
        "label_space_assessment": _coerce_label_space_assessment(
            card.get("label_space_assessment")
        ),
        "slice_exemplars": _coerce_slice_exemplars(card.get("slice_exemplars")),
        "contract_conformance": _coerce_contract_conformance(card.get("contract_conformance")),
        "summary": str(card.get("summary") or "").strip(),
        "provenance": {
            "dataset_id": str(provenance.get("dataset_id") or "").strip(),
            "dataset_version": provenance.get("dataset_version"),
            "data_version": str(provenance.get("data_version") or "").strip(),
            "columns": _as_str_list(provenance.get("columns")),
            "row_ids": _as_str_list(provenance.get("row_ids")),
        },
        "_fallback": bool(card.get("_fallback")),
    }
    return normalized


_NULL_COLUMN_NAMES: frozenset[str] = frozenset({"", "none", "null", "nan"})


def _clean_column_name(col: Any) -> str:
    """A real column name, or ``""`` for a sentinel entry — so we never map ``"None"``."""
    if col is None:
        return ""
    name = str(col).strip()
    return "" if name.lower() in _NULL_COLUMN_NAMES else name


def _io_mapping_from_schema(
    schema: dict[str, Any], allowed: set[str] | None = None
) -> dict[str, str]:
    """Invert ``schema.roles`` into ``column -> role`` when the LLM pass is unavailable.

    ``allowed``, when given, restricts the result to columns that really exist.
    Falls back to mapping every field to ``metadata`` so the mapping is never empty.
    """

    def _ok(name: str) -> bool:
        return bool(name) and (allowed is None or name in allowed)

    mapping: dict[str, str] = {}
    roles = schema.get("roles")
    if isinstance(roles, dict):
        for role_name, cols in roles.items():
            key = str(role_name).lower()
            if key in ("target", "answer", "output", "completion", "reference"):
                role = "reference"
            elif key == "label":
                role = "label"
            elif key == "input":
                role = "input"
            else:
                role = "metadata"
            for col in cols if isinstance(cols, list) else [cols]:
                name = _clean_column_name(col)
                if _ok(name):
                    mapping[name] = role
    if not mapping:
        fields = schema.get("fields")
        if isinstance(fields, dict):
            for raw in fields:
                name = _clean_column_name(raw)
                if _ok(name):
                    mapping[name] = "metadata"
    return mapping


def build_fallback_card(spine: dict[str, Any]) -> dict[str, Any]:
    """Deterministic fallback Dataset Capability Card from the spine alone (no LLM).

    Used when the semantic LLM pass is unavailable or its output fails validation.
    Marked ``_fallback`` so consumers can tell it was reconstructed.
    """
    schema = _as_obj(spine.get("schema"))
    hygiene = _as_obj(spine.get("hygiene"))
    label_space = _as_obj(spine.get("label_space"))
    volume = _as_obj(spine.get("volume_and_tokens"))

    # Restricting to columns that really appear stops a stray role entry inventing one.
    allowed = {c for c in _provenance_columns(spine) if c}
    if not allowed:
        allowed = {str(c) for c in (_as_obj(spine.get("distribution")).get("columns") or {})}
    io_mapping = _io_mapping_from_schema(schema, allowed or None)

    reference_cols = [c for c, role in io_mapping.items() if role == "reference"]
    label_cols = [c for c, role in io_mapping.items() if role == "label"]
    reference: dict[str, Any] = {}
    if reference_cols:
        reference = {
            "columns": reference_cols,
            "description": (
                "Supervised reference field(s) reconstructed from the schema roles "
                "(exact semantics not declared)."
            ),
        }

    def _fm(description: str) -> dict[str, Any]:
        return {"description": description, "detection_pattern": "", "example_row_ids": []}

    quality_signals: list[dict[str, Any]] = []
    failure_modes: list[dict[str, Any]] = []
    pii_columns = hygiene.get("pii_columns")
    if isinstance(pii_columns, list) and pii_columns:
        names = ", ".join(str(c.get("column")) for c in pii_columns if isinstance(c, dict))
        failure_modes.append(_fm(f"PII present in column(s): {names}"))
    dup = hygiene.get("exact_duplicate_rows")
    if isinstance(dup, int) and dup > 0:
        failure_modes.append(_fm(f"{dup} exact-duplicate row(s) detected"))
    if label_cols and label_space.get("balance") == "skewed":
        failure_modes.append(_fm("label distribution is skewed across classes"))
    if not failure_modes:
        failure_modes.append(_fm("no severe hygiene issues detected by the deterministic layer"))
    quality_signals.append(
        {
            "signal": "rows are well-formed and consistent with the inferred schema",
            "severity": "weighted",
        }
    )

    rows = volume.get("total_rows") or 0
    fmt = _coerce_format(spine.get("format"))
    summary = (
        f"{fmt} dataset with {rows} rows over {len(io_mapping)} mapped column(s) "
        "(card reconstructed deterministically from the analysis spine; "
        "semantic layer not produced by an LLM)."
    )

    return {
        "_fallback": True,
        **{
            k: spine.get(k)
            for k in (
                "format",
                "schema",
                "distribution",
                "label_space",
                "target_stats",
                "volume_and_tokens",
                "slices",
                "hygiene",
            )
        },
        "io_mapping": io_mapping,
        "reference": reference,
        "quality_signals": quality_signals,
        "failure_modes": failure_modes,
        "label_space_assessment": {"valid": False, "reason": "", "proposed_label_column": ""},
        "slice_exemplars": [],
        "contract_conformance": [],
        "summary": summary,
        "provenance": spine.get("provenance") or {},
    }


def card_summary_line(card: dict[str, Any]) -> str:
    """One compact line: format + rows + mapped columns + summary head, for prompts."""
    fmt = (card.get("format") or "?").strip()
    volume = _as_obj(card.get("volume_and_tokens"))
    rows = volume.get("total_rows") or 0
    n_cols = len(card.get("io_mapping") or {})
    summary = (card.get("summary") or "").strip()
    parts = [f"dataset ({fmt}): {rows} rows, {n_cols} mapped column(s)"]
    if summary:
        parts.append(summary[:160])
    return " | ".join(parts)


def _write_json(path: Path, payload: Any) -> None:
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False, default=str),
        encoding="utf-8",
    )


def _schema_of(card: dict[str, Any]) -> dict[str, Any]:
    return _as_obj(card.get("schema"))


def _artifact_entry(
    *, prov: Provenance, kind: str, suffix: str, summary: str, content: Any
) -> dict[str, Any]:
    art = Artifact(
        id=f"dataset:{suffix}",
        kind=kind,
        path=f"{suffix}.json",
        summary=summary,
        provenance=prov,
    )
    out = art.to_manifest_entry()
    out["content"] = content
    return out


def _artifact_entries(*, prov: Provenance, card: dict[str, Any]) -> list[dict[str, Any]]:
    """Typed manifest entries, one per artifact kind, content embedded.

    Content is embedded because the standalone dataset-context tool server has no
    ORM and only the copied ``dataset_manifest.json`` — no bundle files.
    """
    entries: list[dict[str, Any]] = [
        _artifact_entry(
            prov=prov,
            kind="dataset_card",
            suffix="dataset_card",
            summary=card_summary_line(card),
            content=card,
        ),
        _artifact_entry(
            prov=prov,
            kind="schema",
            suffix="schema",
            summary="dataset schema (format + roles + fields + io_mapping)",
            content={
                "format": card.get("format"),
                "schema": _schema_of(card),
                "io_mapping": card.get("io_mapping") or {},
            },
        ),
        _artifact_entry(
            prov=prov,
            kind="distribution",
            suffix="distribution",
            summary="per-column distribution + label_space + target_stats + volume_and_tokens",
            content={
                "distribution": card.get("distribution") or {},
                "label_space": card.get("label_space") or {},
                "target_stats": card.get("target_stats") or {},
                "volume_and_tokens": card.get("volume_and_tokens") or {},
            },
        ),
        _artifact_entry(
            prov=prov,
            kind="slice_index",
            suffix="slices",
            summary="cluster + smell cohorts with representative row ids",
            content={"slices": card.get("slices") or []},
        ),
        _artifact_entry(
            prov=prov,
            kind="hygiene",
            suffix="hygiene",
            summary="PII columns + duplicate counts",
            content=card.get("hygiene") or {},
        ),
        _artifact_entry(
            prov=prov,
            kind="contract_conformance",
            suffix="contract_conformance",
            summary=(
                "per output-contract-field conformance join against the codebase capability "
                "card ([] when no codebase source attached)"
            ),
            content={"contract_conformance": card.get("contract_conformance") or []},
        ),
    ]
    return entries


def write_dataset_bundle(
    dataset_key: str,
    data_version: str,
    card: dict[str, Any],
    *,
    sample_rows: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Write the deterministic bundle to ``MEDIA_ROOT/dataset/<dataset_key>/<data_version>/``.

    Returns ``{"dir", "files", "manifest"}``.
    """
    card = normalize_dataset_card(card)
    bundle_dir = dataset_bundle_dir(dataset_key, data_version)
    bundle_dir.mkdir(parents=True, exist_ok=True)

    files: dict[str, str] = {}

    card_path = bundle_dir / DATASET_CARD_FILE
    _write_json(card_path, card)
    files["dataset_card"] = str(card_path)

    schema_path = bundle_dir / SCHEMA_FILE
    _write_json(schema_path, {"format": card.get("format"), "schema": _schema_of(card)})
    files["schema"] = str(schema_path)

    dist_path = bundle_dir / DISTRIBUTION_FILE
    _write_json(
        dist_path,
        {
            "distribution": card.get("distribution") or {},
            "label_space": card.get("label_space") or {},
            "target_stats": card.get("target_stats") or {},
            "volume_and_tokens": card.get("volume_and_tokens") or {},
        },
    )
    files["distribution"] = str(dist_path)

    slices_path = bundle_dir / SLICES_FILE
    _write_json(slices_path, {"slices": card.get("slices") or []})
    files["slices"] = str(slices_path)

    hygiene_path = bundle_dir / HYGIENE_FILE
    _write_json(hygiene_path, card.get("hygiene") or {})
    files["hygiene"] = str(hygiene_path)

    if sample_rows:
        sample_path = bundle_dir / SAMPLE_FILE
        sample_path.write_text(
            "".join(
                json.dumps(r, ensure_ascii=False, default=str) + "\n"
                for r in sample_rows
                if isinstance(r, dict)
            ),
            encoding="utf-8",
        )
        files["sample"] = str(sample_path)

    source_id = f"dataset:{dataset_key}:{data_version}"
    prov = Provenance(
        source_id=source_id,
        produced_by="dataset_context",
        columns=tuple(_provenance_columns(card)),
        row_ids=tuple(_provenance_row_ids(card)),
    )
    artifacts = _artifact_entries(prov=prov, card=card)

    manifest = {
        "schema_version": CARD_SCHEMA_VERSION,
        "kind": "dataset_context",
        "dataset_key": str(dataset_key),
        "data_version": str(data_version),
        "dataset_id": card["provenance"].get("dataset_id"),
        "dataset_version": card["provenance"].get("dataset_version"),
        "card": card,
        "artifacts": artifacts,
    }
    manifest_path = bundle_dir / MANIFEST_FILE
    _write_json(manifest_path, manifest)
    files["manifest"] = str(manifest_path)

    _update_versions_index(dataset_key, data_version)

    return {"dir": str(bundle_dir), "files": files, "manifest": manifest}


_VERSIONS_INDEX_FILE = "versions.json"


def _update_versions_index(dataset_key: str, data_version: str) -> None:
    """Maintain ``MEDIA_ROOT/dataset/<key>/versions.json``, most-recent last.

    A cheap pointer only — the filesystem stays authoritative.
    """
    index_path = dataset_versions_dir(dataset_key) / _VERSIONS_INDEX_FILE
    versions: list[str] = []
    if index_path.is_file():
        try:
            data = json.loads(index_path.read_text(encoding="utf-8"))
            versions = [str(v) for v in (data.get("versions") or []) if str(v).strip()]
        except (OSError, ValueError):
            versions = []
    versions = [v for v in versions if v != str(data_version)]
    versions.append(str(data_version))
    index_path.parent.mkdir(parents=True, exist_ok=True)
    _write_json(index_path, {"latest": str(data_version), "versions": versions})


def latest_data_version(dataset_key: str) -> str | None:
    """The most-recently-written ``data_version`` for ``dataset_key``, or ``None``."""
    index_path = dataset_versions_dir(dataset_key) / _VERSIONS_INDEX_FILE
    if index_path.is_file():
        try:
            data = json.loads(index_path.read_text(encoding="utf-8"))
            latest = str(data.get("latest") or "").strip()
            if latest and (dataset_bundle_dir(dataset_key, latest) / MANIFEST_FILE).is_file():
                return latest
        except (OSError, ValueError):
            pass
    parent = dataset_versions_dir(dataset_key)
    if not parent.is_dir():
        return None
    candidates = [p.name for p in parent.iterdir() if p.is_dir() and (p / MANIFEST_FILE).is_file()]
    if not candidates:
        return None
    # By mtime so a manual write with no index entry still resolves.
    candidates.sort(
        key=lambda name: (parent / name / MANIFEST_FILE).stat().st_mtime,
    )
    return candidates[-1]


def load_dataset_bundle(dataset_key: str, data_version: str | None = None) -> dict[str, Any] | None:
    """Load the bundle for ``dataset_key``, latest version when omitted.

    ``None`` when the bundle dir or its manifest is missing/unreadable.
    """
    version = data_version or latest_data_version(dataset_key)
    if not version:
        return None
    bundle_dir = dataset_bundle_dir(dataset_key, version)
    manifest_path = bundle_dir / MANIFEST_FILE
    if not manifest_path.is_file():
        return None
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        logger.warning("dataset bundle manifest unreadable for %s@%s", dataset_key, version)
        return None

    card = manifest.get("card") or {}
    return {
        "dir": str(bundle_dir),
        "dataset_key": str(dataset_key),
        "data_version": str(version),
        "card": card,
        "schema": _schema_of(card),
        "distribution": card.get("distribution") or {},
        "label_space": card.get("label_space") or {},
        "slices": card.get("slices") or [],
        "hygiene": card.get("hygiene") or {},
        "io_mapping": card.get("io_mapping") or {},
        "manifest": manifest,
    }
