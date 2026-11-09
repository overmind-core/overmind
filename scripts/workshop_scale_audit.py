import csv
import hashlib
import io
import json
import os
import sqlite3
import tempfile
import unicodedata
from collections import Counter
from pathlib import Path

from django.core.files.storage import default_storage

from modal_shared.decisions import decision_reference
from overbae.models import Cell, WorkshopTask
from overbae.services.datasets import paths, store
from overbae.services.datasets.contract import training_line
from overbae.services.datasets.partition import contamination_keys


def digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()


def audit(key, task_id):
    case = json.loads(os.environ["SCALE_MANIFEST"])[key]
    task = WorkshopTask.objects.get(pk=task_id)
    assert task.state == "completed"
    preparation = task.actions.get(sequence=0).result
    publication = task.actions.get(name="publish_workshop", state="completed").result
    source = preparation["input"]["cell"]
    source_cell = Cell.objects.get(pk=source)
    output_cell = Cell.objects.get(pk=publication["cell"])
    with tempfile.TemporaryDirectory(prefix="workshop-scale-audit-") as directory:
        db = sqlite3.connect(Path(directory) / "audit.sqlite")
        db.execute(
            "CREATE TABLE records (cell TEXT, row INTEGER, payload TEXT, PRIMARY KEY(cell,row))"
        )
        db.execute(
            "CREATE TABLE outputs (row INTEGER PRIMARY KEY, origin INTEGER UNIQUE, checksum TEXT)"
        )
        db.execute("CREATE TABLE membership (row INTEGER PRIMARY KEY, role TEXT)")
        db.execute("CREATE TABLE groups (key TEXT PRIMARY KEY, role TEXT)")
        loaded = set()

        def load(cell):
            ident = str(cell.pk)
            if ident not in loaded:
                db.executemany(
                    "INSERT INTO records VALUES (?,?,?)",
                    (
                        (ident, row["source_row"], json.dumps(row))
                        for row in store.iter_rows(paths.cell_path(cell.dataset_id, cell.pk))
                    ),
                )
                loaded.add(ident)

        def record(cell_id, row_id):
            if cell_id not in loaded:
                load(Cell.objects.get(pk=cell_id))
            result = db.execute(
                "SELECT payload FROM records WHERE cell=? AND row=?", (cell_id, row_id)
            ).fetchone()
            assert result is not None
            return json.loads(result[0])

        fingerprints = {}

        def ancestors(row):
            result = set()
            for parent in row["_overmind_provenance"]["parents"]:
                cell_id = parent["cell"]
                if cell_id not in fingerprints:
                    fingerprints[cell_id] = Cell.objects.get(pk=cell_id).fingerprint
                assert parent["fingerprint"] == fingerprints[cell_id]
                if cell_id == source:
                    result.add(parent["row"])
                else:
                    result.update(ancestors(record(cell_id, parent["row"])))
            return result

        load(source_cell)
        originals = preparation["input"]["originals"]
        assert len(originals) == 1 and originals[0]["source"] == case["sha256"]
        with default_storage.open(
            paths.source_path(task.dataset_id, case["sha256"]), "rb"
        ) as handle:
            assert hashlib.file_digest(handle, "sha256").hexdigest() == case["sha256"]
        with default_storage.open(
            paths.source_path(task.dataset_id, case["sha256"]), "rb"
        ) as handle:
            text = io.TextIOWrapper(handle, encoding="utf-8")
            supplied = (
                csv.DictReader(text)
                if case["family"] == "covertype"
                else (json.loads(line) for line in text)
            )
            count = 0
            for count, expected in enumerate(supplied, 1):
                landed = record(source, count - 1)
                provenance = landed["_overmind_provenance"]["file"]
                assert provenance["row"] == count - 1 and provenance["sha256"] == case["sha256"]
                if case["family"] == "covertype":
                    expected = {k: int(v) for k, v in expected.items()}
                assert all(landed[k] == value for k, value in expected.items())
            assert count == case["rows"]
        features, labels = set(), Counter()
        aliases = {
            "spruce/fir": 1,
            "spruce fir": 1,
            "lodgepole pine": 2,
            "ponderosa pine": 3,
            "cottonwood/willow": 4,
            "cottonwood willow": 4,
            "aspen": 5,
            "douglas-fir": 6,
            "douglas fir": 6,
            "krummholz": 7,
        }
        for row in store.iter_rows(paths.cell_path(output_cell.dataset_id, output_cell.pk)):
            lineage = ancestors(row)
            assert len(lineage) == 1
            origin = next(iter(lineage))
            original = record(source, origin)
            line = training_line(row)
            if case["family"] == "orca":
                messages = line["messages"]
                assert [m["role"] for m in messages] in (
                    ["user", "assistant"],
                    ["system", "user", "assistant"],
                )
                assert [m["content"] for m in messages if m["role"] == "user"] == [
                    original["question"]
                ]
                assert [m["content"] for m in messages if m["role"] == "assistant"] == [
                    original["answer"]
                ]
            else:
                decision = row["decision"]
                request = {k: decision[k] for k in ("state", "question", "kind", "options")}
                decision_reference(request, {"probabilities": decision["target_probabilities"]})
                state = json.loads(decision["state"])
                names = {k.lower(): k for k in original}
                expected_features = {
                    name.lower()
                    for name in original
                    if name not in {"Cover_Type", "source_row"}
                    and not name.startswith("_overmind_")
                }
                assert {k.lower() for k in state} == expected_features
                for name, value in state.items():
                    assert value == original[names[name.lower()]], (name, value)
                features.update(state)
                targets = []
                for option in decision["options"]:
                    option = str(option).lower()
                    targets.append(int(option) if option.isdigit() else aliases[option])
                assert set(targets) == set(range(1, 8))
                assert decision["target_probabilities"] == [
                    int(v == original["Cover_Type"]) for v in targets
                ]
                labels[original["Cover_Type"]] += 1
            db.execute(
                "INSERT INTO outputs VALUES (?,?,?)", (row["source_row"], origin, digest(line))
            )
        output_count = db.execute("SELECT COUNT(*) FROM outputs").fetchone()[0]
        assert output_count == case["rows"]
        splits = {}
        partition = publication.get("partition")
        for member in partition["members"] if partition else []:
            recipe = partition["recipe"]
            role = member["role"]
            cell = Cell.objects.get(pk=member["cell"]["id"])
            size = 0
            for row in store.iter_rows(paths.cell_path(cell.dataset_id, cell.pk)):
                provenance = row["_overmind_provenance"]["partition"]
                assert (
                    provenance["plan"] == publication["partition"]["id"]
                    and provenance["role"] == role
                )
                assert provenance["source_cell"] == publication["cell"]
                index = provenance["source_row"]
                checksum = db.execute(
                    "SELECT checksum FROM outputs WHERE row=?", (index,)
                ).fetchone()
                assert checksum == (digest(training_line(row)),)
                db.execute("INSERT INTO membership VALUES (?,?)", (index, role))
                groups = contamination_keys(row, recipe["group_by"])
                if case["family"] == "orca":
                    question = next(m["content"] for m in row["messages"] if m["role"] == "user")
                    normalized = " ".join(
                        unicodedata.normalize("NFKC", question).casefold().split()
                    )
                    groups.add(("independent_normalized_question", normalized))
                for group in groups:
                    group_id = digest(group)
                    previous = db.execute(
                        "SELECT role FROM groups WHERE key=?", (group_id,)
                    ).fetchone()
                    assert previous is None or previous == (role,), ("split overlap", group)
                    db.execute("INSERT OR IGNORE INTO groups VALUES (?,?)", (group_id, role))
                size += 1
            assert size > 0
            splits[role] = size
        if partition:
            assert {"train", "development", "final"} <= splits.keys()
            assert sum(splits.values()) == output_count
        db.close()
    return {
        "task": task_id,
        "case": key,
        "passed": True,
        "source_rows": case["rows"],
        "output_rows": output_count,
        "splits": splits,
        "partition_status": "verified" if partition else "not_produced",
        "features": sorted(features),
        "labels": labels,
        "source_bytes_and_values_verified": True,
        "full_lineage_verified": True,
        "all_targets_and_inputs_verified": True,
        "consumer_reader_passed": True,
        "disjoint_complete_partitions": True if partition else None,
        "declared_group_and_exact_input_overlap": 0 if partition else None,
        "normalized_question_overlap": 0 if partition and case["family"] == "orca" else None,
        "semantic_truth_and_near_duplicate_recall": "Not established by this mechanical audit; inspect retained analysis and grouping method.",
    }


if __name__ == "__main__":
    for item in os.environ["SCALE_CASES"].split(","):
        key, task_id = item.split(":")
        print(json.dumps(audit(key, task_id)))
