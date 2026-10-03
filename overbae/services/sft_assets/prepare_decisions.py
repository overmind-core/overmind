import hashlib
import json
import sys
from pathlib import Path

from transformers import AutoTokenizer

from modal_shared.decision_artifact import read_artifact
from modal_shared.decision_inference import prepare_requests
from modal_shared.serving.artifacts import atomic_json, digest_file


def main(directory, model_directory):
    request = json.loads((directory / "request.json").read_text())
    artifact = read_artifact(model_directory / "final")
    if artifact["identity"] != request["artifact_identity"]:
        raise ValueError("Prediction model identity changed")
    if digest_file(directory / "inputs.jsonl") != request["input_sha256"]:
        raise ValueError("Prediction input file changed")
    prepared = json.loads((model_directory / "preparation.json").read_text())
    tokenizer = AutoTokenizer.from_pretrained(model_directory / "final", local_files_only=True)
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
            artifact["codebook"],
            prepared["context_length"],
        )
    atomic_json(
        directory / "preparation.json",
        {
            **report,
            **request,
            "context_length": prepared["context_length"],
            "tokens_sha256": digest_file(directory / "tokens.jsonl"),
            "failures_sha256": digest_file(directory / "failures.jsonl"),
        },
    )


if __name__ == "__main__":
    main(Path(sys.argv[1]), Path(sys.argv[2]))
