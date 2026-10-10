import hashlib
import json
import sys
from pathlib import Path

from decision_tokenizer import encode_record
from transformers import AutoTokenizer

from modal_shared.decision_artifact import read_artifact
from modal_shared.decision_encoding import RENDERER
from modal_shared.decision_inference import prepare_requests
from modal_shared.decisions import DECISION_OBJECTIVE
from modal_shared.serving.artifacts import atomic_json, digest_file, read_base_manifest


def main(directory, model_directory):
    request = json.loads((directory / "request.json").read_text())
    foundation = request.get("foundation")
    if foundation:
        base = read_base_manifest(Path(foundation["base_path"]))
        if (
            base["identity"] != foundation["base_identity"]
            or base["repo"] != foundation["hf_model"]
        ):
            raise ValueError("Foundation base identity changed")
        tokenizer = AutoTokenizer.from_pretrained(
            foundation["base_path"], local_files_only=True, trust_remote_code=True
        )
        if tokenizer.pad_token is None:
            tokenizer.pad_token = tokenizer.eos_token
        artifact = {
            "identity": "base:" + base["identity"],
            "vocab_fingerprint": hashlib.sha256(
                json.dumps(tokenizer.get_vocab(), sort_keys=True).encode()
            ).hexdigest(),
        }
        prepared = {"context_length": request["context_length"]}
        tokenizer.save_pretrained(directory / "tokenizer")
    else:
        artifact = read_artifact(model_directory / "final")
        prepared = json.loads((model_directory / "preparation.json").read_text())
        tokenizer = AutoTokenizer.from_pretrained(model_directory / "final", local_files_only=True)
    if artifact["identity"] != request["artifact_identity"]:
        raise ValueError("Prediction model identity changed")
    if digest_file(directory / "inputs.jsonl") != request["input_sha256"]:
        raise ValueError("Prediction input file changed")
    vocab = hashlib.sha256(json.dumps(tokenizer.get_vocab(), sort_keys=True).encode()).hexdigest()
    if vocab != artifact["vocab_fingerprint"]:
        raise ValueError("Prediction tokenizer differs from the trained artifact")
    with (
        (directory / "inputs.jsonl").open() as source,
        (directory / "tokens.jsonl").open("w") as output,
        (directory / "failures.jsonl").open("w") as failures,
    ):
        report = prepare_requests(
            (json.loads(line) for line in source),
            output,
            failures,
            tokenizer,
            prepared["context_length"],
            encode_record,
        )
    atomic_json(
        directory / "preparation.json",
        {
            **report,
            **request,
            "objective": DECISION_OBJECTIVE,
            "renderer": RENDERER,
            "vocab_fingerprint": vocab,
            "chat_template_sha256": hashlib.sha256(
                str(tokenizer.chat_template).encode()
            ).hexdigest(),
            "tokenizer_revision": tokenizer.init_kwargs.get("_commit_hash"),
            "context_length": prepared["context_length"],
            "tokens_sha256": digest_file(directory / "tokens.jsonl"),
            "failures_sha256": digest_file(directory / "failures.jsonl"),
        },
    )


if __name__ == "__main__":
    main(Path(sys.argv[1]), Path(sys.argv[2]) if len(sys.argv) > 2 else None)
