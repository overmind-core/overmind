import json
from pathlib import Path
from unittest.mock import Mock

import pytest

from modal_shared.preparation import (
    preparation_failure,
    processor_fingerprint,
    run_preparation_process,
    validate_preparation_report,
)
from overbae.services.finetuning_runner import sanitize_job_error
from overbae.services.sft_assets import preprocess


def test_stale_worker_reports_deployment_mismatch_without_loading_tokenizer(tmp_path):
    request = tmp_path / "request.json"
    request.write_text(json.dumps({"processor": "different-code"}))
    output = tmp_path / "output"
    load_tokenizer, tokenize = Mock(), Mock()
    preprocess.run(request, output, load_tokenizer, tokenize)
    report = json.loads((output / "report.json").read_text())
    assert report == preparation_failure("worker_out_of_date")
    load_tokenizer.assert_not_called()
    tokenize.assert_not_called()
    assert not (output / "tokens.jsonl").exists()
    assert sanitize_job_error(report["error"]) == report["error"]


def test_current_worker_writes_exact_tokens_and_tokenizer(tmp_path):
    request = tmp_path / "request.json"
    request.write_text(
        json.dumps(
            {
                "processor": processor_fingerprint(Path(preprocess.__file__).parent),
                "tokenizer_model": "test-tokenizer",
                "model": "test-model",
                "context_length": 128,
                "rows": [{"messages": [{"role": "assistant", "content": "answer"}]}],
            }
        )
    )
    tokenizer = Mock(
        eos_token="</s>",
        pad_token=None,
        chat_template="template",
        init_kwargs={"_commit_hash": "r1"},
    )
    tokenizer.get_vocab.return_value = {"answer": 2}
    tokenizer.decode.return_value = "answer"
    load_tokenizer = Mock(return_value=tokenizer)
    tokenize = Mock(return_value={"input_ids": [1, 2], "labels": [-100, 2]})
    output = tmp_path / "output"
    preprocess.run(request, output, load_tokenizer, tokenize)
    report = json.loads((output / "report.json").read_text())
    assert report["ready"] and report["incompatible_rows"] == 0
    assert report["rows"] == 1 and report["supervised_tokens"] == 1
    assert report["tokenizer_revision"] == "r1" and report["artifact_sha256"]
    assert json.loads((output / "tokens.jsonl").read_text())["labels"] == [-100, 2]
    assert tokenizer.pad_token == "</s>"
    tokenizer.save_pretrained.assert_called_once_with(output / "tokenizer")
    load_tokenizer.assert_called_once_with("test-tokenizer", trust_remote_code=True)


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        (
            'report.write_text(json.dumps({"ready": True, "tokens": 20}))',
            {"ready": True, "tokens": 20},
        ),
        (
            'report.write_text(json.dumps({"ready": False, "incompatible_rows": 1}))',
            {"ready": False, "incompatible_rows": 1},
        ),
        (
            'report.write_text(json.dumps({"ready": False, "error_code": "worker_out_of_date"}))',
            preparation_failure("worker_out_of_date"),
        ),
        ('report.write_text("not json")', preparation_failure("invalid_report")),
        ("pass", preparation_failure("invalid_report")),
        (
            'report.write_text(json.dumps({"ready": True})); sys.exit(1)',
            preparation_failure("process_failed"),
        ),
        ('raise RuntimeError("private diagnostic")', preparation_failure("process_failed")),
    ],
)
def test_subprocess_retains_logs_and_never_reuses_stale_success(tmp_path, body, expected):
    script = tmp_path / "prepare_training.py"
    script.write_text(
        "import json, sys\nfrom pathlib import Path\n"
        'report = Path(sys.argv[2]) / "report.json"\n'
        'print("stdout diagnostic")\nprint("stderr diagnostic", file=sys.stderr)\n' + body + "\n"
    )
    request = tmp_path / "request.json"
    request.write_text("{}")
    destination = tmp_path / "output"
    destination.mkdir()
    (destination / "report.json").write_text('{"ready": true, "tokens": 999}')
    (destination / "preprocess_stdout.log").write_text("previous attempt\n")

    report = run_preparation_process(tmp_path, request, destination)

    assert report == expected
    assert json.loads((destination / "report.json").read_text()) == expected
    log = (destination / "preprocess_stdout.log").read_text()
    assert "previous attempt" in log and "stdout diagnostic" in log and "stderr diagnostic" in log
    if "private diagnostic" in body:
        assert "private diagnostic" in log and "private diagnostic" not in json.dumps(report)


@pytest.mark.parametrize("report", [None, [], {}, {"ready": "yes"}, {"ready": 1}])
def test_malformed_reports_are_worker_failures_not_data_incompatibility(report):
    assert validate_preparation_report(report) == preparation_failure("invalid_report")


def test_unknown_worker_error_does_not_expose_internal_diagnostics():
    report = validate_preparation_report(
        {"ready": True, "error_code": "unexpected", "error": "/root/private diagnostic"}
    )
    assert report == preparation_failure("process_failed")


@pytest.mark.parametrize("code", ["worker_out_of_date", "process_failed", "invalid_report"])
def test_actionable_preprocessing_errors_survive_training_error_sanitization(code):
    error = preparation_failure(code)["error"]
    assert sanitize_job_error(error) == error
