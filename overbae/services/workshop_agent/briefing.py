import hashlib
import json
import tempfile
import time
from collections import Counter
from pathlib import Path

from overbae.services.datasets import paths, rows, store
from workshop_runtime.data import replay_inputs
from workshop_runtime.observations import compact


def digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()


def measure(path):
    columns, fingerprints = {}, set()
    count = duplicates = 0
    for record in store.iter_rows(path):
        count += 1
        ordinary = replay_inputs([record])[0]
        ordinary.pop("source_row", None)
        fingerprint = digest(ordinary)
        duplicates += fingerprint in fingerprints
        fingerprints.add(fingerprint)
        for name, value in ordinary.items():
            column = columns.setdefault(
                name, {"types": Counter(), "null": 0, "blank": 0, "examples": []}
            )
            column["types"][type(value).__name__] += 1
            column["null"] += value is None
            column["blank"] += isinstance(value, str) and not value.strip()
            if len(column["examples"]) < 2:
                sample = compact(value, text_limit=160, items=3)
                if sample not in column["examples"]:
                    column["examples"].append(sample)
    return {
        "rows_measured": count,
        "columns": columns,
        "exact_duplicate_records_excluding_identity": duplicates,
        "scope": "Full pinned source; examples are bounded. No target inference, leakage or semantic suitability assessment has been performed.",
        "replay_input": "source_row and ordinary columns only; _overmind_* metadata is removed except _overmind_parent_rows. workshop_runtime.data.stage creates this exact input shape. Retain needed audit metadata explicitly in the package.",
    }


def prepare(cell, *, project_id, runtime):
    started = time.monotonic()
    if str(cell.dataset.project_id) != str(project_id):
        raise ValueError("Briefing source belongs to another project.")
    rows.verify(cell)
    binding = {
        "project": str(project_id),
        "dataset": str(cell.dataset_id),
        "cell": str(cell.pk),
        "source_sha256": cell.fingerprint,
        "runtime": runtime,
        "profiler": hashlib.sha256(
            Path(__file__).read_bytes() + Path(store.__file__).read_bytes()
        ).hexdigest(),
    }
    path = paths.dataset_dir(cell.dataset_id) / "briefings" / (digest(binding) + ".json")
    cached = None
    try:
        cached = json.loads(path.read_text())
        if cached["binding"] != binding or cached["sha256"] != digest(cached["facts"]):
            cached = None
    except (OSError, ValueError, KeyError, TypeError):
        cached = None
    hit = cached is not None
    if not hit:
        facts = measure(paths.cell_path(cell.dataset_id, cell.pk))
        rows.verify(cell)
        cached = {"binding": binding, "sha256": digest(facts), "facts": facts}
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=path.parent, mode="w", delete=False) as temporary:
            json.dump(cached, temporary, ensure_ascii=False)
        Path(temporary.name).replace(path)
    return cached, {"cache_hit": hit, "seconds": time.monotonic() - started}


def initial_context(input_manifest, context, platform, measured):
    return {
        "input.json": input_manifest,
        "context.json": {
            **context,
            "source": {key: value for key, value in context["source"].items() if key != "stats"},
            "workshop": {
                key: value for key, value in context["workshop"].items() if key != "profiles"
            },
            "full_path": "context.json",
        },
        "platform.json": {
            **{
                key: value
                for key, value in platform.items()
                if key not in {"sha256", "consumer_taxonomy", "examples", "consumer_validation"}
            },
            "full_document_sha256": platform["sha256"],
            "full_path": "platform.json",
        },
        "briefing.json": {
            **measured,
            "facts": compact(measured["facts"], text_limit=160, items=3),
            "full_path": "briefing.json",
        },
    }
