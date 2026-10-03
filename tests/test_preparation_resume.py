import gzip
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from unittest.mock import Mock

import pytest

from modal_shared.preparation import processor_fingerprint
from modal_shared.training_data import file_digest
from overbae.services.sft_assets import preprocess


def fixture(tmp_path, rows=9):
    source = tmp_path / "rows.jsonl.gz"
    with gzip.open(source, "wt") as stream:
        for i in range(rows):
            stream.write(
                json.dumps({"messages": [{"role": "assistant", "content": str(i)}]}) + "\n"
            )
    request = tmp_path / "request.json"
    request.write_text(
        json.dumps(
            {
                "processor": processor_fingerprint(Path(preprocess.__file__).parent),
                "tokenizer_model": "fixture",
                "model": "fixture",
                "context_length": 128,
                "rows_path": str(source),
                "rows_sha256": file_digest(source),
            }
        )
    )
    tokenizer = Mock(
        eos_token="</s>",
        pad_token="</s>",
        chat_template="template",
        init_kwargs={"_commit_hash": "fixed"},
    )
    tokenizer.get_vocab.return_value = {"a": 1}
    tokenizer.decode.return_value = "a"
    return request, Mock(return_value=tokenizer)


def test_interrupted_preparation_reuses_verified_shards_with_identical_output(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(preprocess, "SHARD_ROWS", 3, raising=False)
    request, loader = fixture(tmp_path)
    seen = []

    def tokenize(tokenizer, model, messages, tools):
        row = int(messages[0]["content"])
        seen.append(row)
        return {"input_ids": [1, row + 2], "labels": [-100, row + 2]}

    expected = tmp_path / "expected"
    preprocess.run(request, expected, loader, tokenize)
    seen.clear()

    def interrupted(*args):
        if args[2][0]["content"] == "4":
            raise KeyboardInterrupt("provider preemption")
        return tokenize(*args)

    resumed = tmp_path / "resumed"
    with pytest.raises(KeyboardInterrupt):
        preprocess.run(request, resumed, loader, interrupted)
    seen.clear()
    preprocess.run(request, resumed, loader, tokenize)
    assert seen == [3, 4, 5, 6, 7, 8]
    assert (resumed / "tokens.jsonl").read_bytes() == (expected / "tokens.jsonl").read_bytes()
    assert json.loads((resumed / "report.json").read_text()) == json.loads(
        (expected / "report.json").read_text()
    )
    progress = json.loads((resumed / "progress.json").read_text())
    assert progress["reused_rows"] == 3 and progress["completed_rows"] == 9


def test_corrupted_committed_shard_is_rejected(tmp_path, monkeypatch):
    monkeypatch.setattr(preprocess, "SHARD_ROWS", 3, raising=False)
    request, loader = fixture(tmp_path)
    tokenize = Mock(return_value={"input_ids": [1, 2], "labels": [-100, 2]})
    output = tmp_path / "output"
    preprocess.run(request, output, loader, tokenize)
    shards = list((output / "shards").glob("*.jsonl"))
    assert shards
    shards[0].write_text("corrupt")
    with pytest.raises(ValueError, match="shard"):
        preprocess.run(request, output, loader, tokenize)


def test_resume_does_not_reuse_a_different_request(tmp_path, monkeypatch):
    monkeypatch.setattr(preprocess, "SHARD_ROWS", 3, raising=False)
    request, loader = fixture(tmp_path)
    tokenize = Mock(return_value={"input_ids": [1, 2], "labels": [-100, 2]})
    output = tmp_path / "output"
    preprocess.run(request, output, loader, tokenize)
    body = json.loads(request.read_text())
    body["context_length"] = 256
    request.write_text(json.dumps(body))
    with pytest.raises(ValueError, match="identity"):
        preprocess.run(request, output, loader, tokenize)


def test_process_kill_resumes_only_uncommitted_shard(tmp_path, monkeypatch):
    monkeypatch.setattr(preprocess, "SHARD_ROWS", 3)
    request, loader = fixture(tmp_path)
    expected = tmp_path / "expected"

    def tokenize(_tokenizer, _model, messages, _tools):
        row = int(messages[0]["content"])
        return {"input_ids": [1, row + 2], "labels": [-100, row + 2]}

    preprocess.run(request, expected, loader, tokenize)
    driver = tmp_path / "driver.py"
    driver.write_text("""import json, sys, time
from pathlib import Path
from types import SimpleNamespace
from overbae.services.sft_assets import preprocess
preprocess.SHARD_ROWS = 3
root = Path(sys.argv[1])
tokenizer = SimpleNamespace(eos_token="</s>", pad_token="</s>", chat_template="template", init_kwargs={"_commit_hash":"fixed"}, get_vocab=lambda:{"a":1}, decode=lambda _:"a", save_pretrained=lambda path:None)
def tokenize(_tokenizer, _model, messages, _tools):
    row = int(messages[0]["content"])
    if row == 4 and len(sys.argv) > 2:
        (root / "paused").touch()
        time.sleep(60)
    with (root / "seen").open("a") as out:
        out.write(str(row) + "\\n")
    return {"input_ids":[1,row+2],"labels":[-100,row+2]}
preprocess.run(root / "request.json", root / "resumed", lambda *args, **kwargs:tokenizer, tokenize)
""")
    env = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1])}
    with (tmp_path / "child.log").open("w") as output:
        child = subprocess.Popen(
            [sys.executable, str(driver), str(tmp_path), "interrupt"],
            env=env,
            stdout=output,
            stderr=output,
        )
        try:
            deadline = time.monotonic() + 15
            while (
                not (tmp_path / "paused").exists()
                and child.poll() is None
                and time.monotonic() < deadline
            ):
                time.sleep(0.02)
            assert (tmp_path / "paused").exists(), (tmp_path / "child.log").read_text()
            os.kill(child.pid, signal.SIGKILL)
            assert child.wait(timeout=5) == -signal.SIGKILL
        finally:
            if child.poll() is None:
                child.kill()
                child.wait()
        (tmp_path / "seen").write_text("")
        subprocess.run(
            [sys.executable, str(driver), str(tmp_path)],
            env=env,
            stdout=output,
            stderr=output,
            check=True,
            timeout=15,
        )
    assert (tmp_path / "seen").read_text().splitlines() == [str(i) for i in range(3, 9)]
    for name in ("tokens.jsonl", "report.json"):
        assert (tmp_path / "resumed" / name).read_bytes() == (expected / name).read_bytes()
