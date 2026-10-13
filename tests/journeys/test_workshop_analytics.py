import json

from .datasets import upload
from .stack import drain


def test_workshop_publishes_version_bound_analytics_to_mcp_and_rest(
    workshop, cli, sample_agent, worker, tmp_path, rest_for
):
    source_file = tmp_path / "imbalanced.jsonl"
    source_file.write_text(
        "".join(json.dumps({"amount": i, "fraud": int(i == 1000)}) + "\n" for i in range(1001))
    )
    dataset = upload(cli, sample_agent, source_file, "--intent", "train")
    drain(worker)
    source = workshop.call("inspect_dataset", {"dataset": dataset})["active"]
    result = workshop.call(
        "import_dataset_version",
        {
            "dataset": dataset,
            "source_cell": source["id"],
            "source_fingerprint": source["fingerprint"],
            "name": "Fraud decisions",
            "request_key": "analytics-transform",
            "provenance": "Test fixture encodes the supplied fraud label without changing rows.",
            "imported_rows": [
                {
                    "source_row": i,
                    "decision": {
                        "state": json.dumps({"amount": i}),
                        "question": "Fraud?",
                        "kind": "choice",
                        "options": ["No", "Yes"],
                        "target_probabilities": [int(i != 1000), int(i == 1000)],
                        "target_semantics": "categorical_gold",
                    },
                }
                for i in range(1001)
            ],
        },
    )
    drain(worker)
    receipt = workshop.call("get_job", {"kind": "dataset_pipeline", "id": result["job"]["id"]})
    assert receipt["status"] == "completed", receipt
    detail = workshop.call("inspect_dataset", {"dataset": dataset})
    cell = next(cell for cell in detail["cells"] if cell["id"] == detail["active"]["id"])
    report = cell["analytics"]
    assert report["fingerprint"] == cell["fingerprint"]
    assert report["cell"] == cell["id"]
    assert report["families"][0]["counts"] == [1000, 1]
    assert report["rows_scanned"] == 1001
    assert (
        next(c for c in report["checks"] if c["id"] == "targets.class_balance")["status"]
        == "warning"
    )
    assert receipt["progress"]["analytics"]["fingerprint"] == report["fingerprint"]
    rest = rest_for(cli.project_key(sample_agent)).request("GET", f"/api/datasets/{dataset}/")
    rest_cell = next(c for c in rest["cells"] if c["id"] == cell["id"])
    assert rest_cell["analytics"] == report
    exploration = workshop.call(
        "explore_dataset",
        {
            "source_cell": cell["id"],
            "name": "Refresh analytics",
            "request_key": "refresh-analytics",
        },
    )
    drain(worker)
    refreshed = workshop.call(
        "get_job", {"kind": "data_exploration", "id": exploration["workflow"]["id"]}
    )
    assert refreshed["status"] == "completed", refreshed
    after = workshop.call("inspect_dataset", {"dataset": dataset})
    assert after["active"]["fingerprint"] == cell["fingerprint"]
