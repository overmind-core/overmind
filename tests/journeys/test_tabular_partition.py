import csv
import json

from .datasets import upload
from .stack import drain


def test_tabular_upload_transform_and_temporal_partition_preserve_distinct_observations(
    workshop, cli, sample_agent, worker, tmp_path
):
    path = tmp_path / "measurements.csv"
    records = [
        {
            "state": "active",
            "text": "shared description",
            "measurement": i,
            "period": i // 2,
            "label": i % 2,
        }
        for i in range(8)
    ]
    with path.open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(records[0]))
        writer.writeheader()
        writer.writerows(records)
    dataset = upload(cli, sample_agent, path, "--intent", "train")
    drain(worker)
    source = workshop.call("inspect_dataset", {"dataset": dataset})["active"]
    original = workshop.call("query_dataset", {"dataset": dataset, "sql": "select * from t"})[
        "rows"
    ]
    source_keys = [r["_overmind_provenance"]["source_content_keys"] for r in original]
    assert len({key for keys in source_keys for key in keys}) == 8
    transformed = [
        {
            "source_row": row["source_row"],
            "period": row["period"],
            "decision": {
                "state": json.dumps({k: row[k] for k in ("state", "text", "measurement")}),
                "question": "Is the measurement positive?",
                "kind": "choice",
                "options": ["No", "Yes"],
                "target_probabilities": [1 - row["label"], row["label"]],
                "target_semantics": "categorical_gold",
            },
        }
        for row in original
    ]
    imported = workshop.call(
        "import_dataset_version",
        {
            "dataset": dataset,
            "source_cell": source["id"],
            "source_fingerprint": source["fingerprint"],
            "name": "Declared decision inputs",
            "request_key": "measurement-inputs",
            "imported_rows": transformed,
            "provenance": "Deterministic fixture declares features and the supplied label.",
        },
    )
    drain(worker)
    result = workshop.call("get_job", {"kind": "dataset_pipeline", "id": imported["job"]["id"]})
    assert result["status"] == "completed", result
    prepared = workshop.call("inspect_dataset", {"dataset": dataset})["active"]
    roles = ["train", "development", "calibration", "final"]
    created = workshop.call(
        "create_data_partition",
        {
            "name": "Chronological measurements",
            "source_cell": prepared["id"],
            "request_key": "measurement-periods",
            "recipe": {
                "seed": 42,
                "fractions": dict.fromkeys(roles, 0.25),
                "holdouts": [
                    {"field": "period", "values": [i], "role": role} for i, role in enumerate(roles)
                ],
            },
        },
    )
    drain(worker)
    partition = workshop.call(
        "get_job", {"kind": "data_partition", "id": created["workflow"]["id"]}
    )
    assert partition["status"] == "completed", partition
    assert partition["progress"]["report"]["counts"] == dict.fromkeys(roles, 2)
    retained = workshop.call(
        "query_dataset",
        {
            "dataset": dataset,
            "cell": source["id"],
            "sql": "select * from t",
        },
    )["rows"]
    assert retained == original
