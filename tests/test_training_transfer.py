import copy
import json
import shutil
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import modal
import pytest
from conftest import frozen_dataset

from modal_shared.training_data import file_digest, row_key
from overbae.modal import modal_sft_worker as worker
from overbae.models import FinetuningJob, Project
from overbae.services import finetuning_runner, training_submission
from overbae.services.training_preparation import request_preparation


@pytest.mark.django_db
@pytest.mark.parametrize("objective", ["assistant_cross_entropy", "decision_cross_entropy"])
def test_runner_transfers_only_bound_selections_and_worker_restores_exact_tokens(
    tmp_path, monkeypatch, settings, objective
):
    settings.FINETUNING_BACKEND = "modal"
    if objective == "decision_cross_entropy":
        rows = [
            {
                "decision": {
                    "state": "",
                    "question": "选择?",
                    "kind": "choice",
                    "options": ["yes", "no"],
                    "target_probabilities": probabilities,
                    "weight": weight,
                }
            }
            for probabilities, weight in [([0.2, 0.8], 2), ([0.7, 0.3], 1)]
        ]
        artifacts = [
            {
                "key": row_key(row),
                "input_ids": [1, 2],
                "option_token_ids": [3, 4],
                "target_probabilities": row["decision"]["target_probabilities"],
                "weight": row["decision"]["weight"],
            }
            for row in rows
        ]
    else:
        rows = [
            {
                "messages": [
                    {"role": "user", "content": "证据 " * 100},
                    {"role": "assistant", "content": answer},
                ],
                "tools": [
                    {
                        "type": "function",
                        "function": {
                            "name": "lookup",
                            "parameters": {"type": "object", "properties": {}},
                        },
                    }
                ],
            }
            for answer in ["first", "second"]
        ]
        artifacts = [
            {"key": row_key(row), "input_ids": [1, i + 2], "labels": [-100, i + 2]}
            for i, row in enumerate(rows)
        ]

    project = Project.objects.create(name="Transfer", slug="transfer")
    dataset = frozen_dataset(project, [rows[1], rows[0], rows[1]], contract="train")
    validation = frozen_dataset(project, [rows[0]], contract="train")
    job = FinetuningJob.objects.create(
        project=project,
        dataset=dataset,
        cell=dataset.active_cell,
        validation_dataset=validation,
        validation_cell=validation.active_cell,
        validation_enabled=True,
        provider="modal",
        base_model="Qwen/Qwen3-8B",
        hyperparameters={
            "n_epochs": 1,
            "context_length": 4096,
            "batch_size": 8,
            "training_type": {"type": "Lora"},
            "objective": objective,
        },
    )
    prep = request_preparation(job.cell, job.base_model, 4096, validation_cell=job.validation_cell)
    job.requested_configuration = {"runtime": prep.config["runtime"]}
    job.save(update_fields=["requested_configuration"])
    volume_root = tmp_path / "volume"
    prep_dir = volume_root / "preparations" / str(prep.id)
    prep_dir.mkdir(parents=True)
    (prep_dir / "tokenizer").mkdir()
    (prep_dir / "tokenizer" / "tokenizer.json").write_text("{}")
    encoded = [json.dumps(row).encode() + b"\n" for row in artifacts]
    (prep_dir / "tokens.jsonl").write_bytes(b"".join(encoded))
    report = {"ready": True, "artifact_sha256": file_digest(prep_dir / "tokens.jsonl")}
    (prep_dir / "report.json").write_text(json.dumps(report))
    prep.state, prep.report = "ready", report
    prep.save(update_fields=["state", "report"])

    train, val = tmp_path / "train.jsonl", tmp_path / "val.jsonl"
    train.write_text("".join(json.dumps(rows[i]) + "\n" for i in [1, 0, 1]))
    val.write_text(json.dumps(rows[0]) + "\n")
    uploaded = []

    class Batch:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def put_file(self, local, remote):
            payload = Path(local).read_bytes()
            uploaded.append((remote, payload))
            target = volume_root / remote.lstrip("/")
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(local, target)

    monkeypatch.setattr(worker, "DATA_MOUNT", str(volume_root))
    monkeypatch.setattr(worker, "RUNS_DIR", str(volume_root / "runs"))
    monkeypatch.setattr(
        worker, "sft_vol", SimpleNamespace(reload=lambda: None, commit=lambda: None)
    )
    monkeypatch.setattr(
        modal.Volume, "from_name", lambda *args, **kw: SimpleNamespace(batch_upload=Batch)
    )
    materializations = []

    def upload(**kwargs):
        materializations.append(copy.deepcopy(kwargs))
        return worker.upload_dataset.local(**kwargs)

    spawn = Mock(return_value=SimpleNamespace(object_id="fc-offline-transfer"))
    train_function = SimpleNamespace(with_options=lambda **kw: SimpleNamespace(spawn=spawn))

    def lookup(app, name, **kwargs):
        if name == "upload_dataset":
            return SimpleNamespace(remote=upload)
        if name == "fetch_base_model":
            return SimpleNamespace(remote=lambda **kwargs: None)
        return train_function

    monkeypatch.setattr(modal.Function, "from_name", lookup)
    training_submission.claim(job)
    result = finetuning_runner.ModalRunner(release=prep.config["runtime"]).submit(
        job, str(train), 3, str(val)
    )
    run_id = result.remote_id.split(":")[0]
    run_dir = volume_root / "runs" / run_id
    assert (run_dir / "data.jsonl").read_bytes() == encoded[1] + encoded[0] + encoded[1]
    assert (run_dir / "val.jsonl").read_bytes() == encoded[0]
    assert (run_dir / "tokenizer" / "tokenizer.json").read_text() == "{}"
    assert sum(len(payload) for _, payload in uploaded) == 4 * 32
    assert all(name.endswith(".keys") for name, _ in uploaded)
    assert materializations[0]["artifact_sha256"] == report["artifact_sha256"]
    spawn.assert_called_once()

    before = (run_dir / "data.jsonl").read_bytes()
    mismatched = {**materializations[0], "artifact_sha256": "0" * 64}
    with pytest.raises(ValueError, match="artifact"):
        worker.upload_dataset.local(**mismatched)
    assert (run_dir / "data.jsonl").read_bytes() == before
