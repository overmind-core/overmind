"""CPU preprocessing and the artifact consumed by the training engine."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from modal_shared.training_data import row_key


def preprocess_rows(rows, tokenizer, model_id, context_length, tokenize):
    artifacts = []
    issues = []
    previews = []
    total_tokens = supervised_tokens = longest = incompatible = 0
    for index, row in enumerate(rows):
        identity = row.get("source_row", index)
        try:
            result = tokenize(tokenizer, model_id, row.get("messages"), row.get("tools"))
            ids, labels = result["input_ids"], result["labels"]
            supervised = sum(label != -100 for label in labels[1:])
            total_tokens += len(ids)
            supervised_tokens += supervised
            longest = max(longest, len(ids))
            if len(ids) != len(labels):
                raise ValueError("Token and label lengths differ")
            if not supervised:
                raise ValueError("No supervised next-token targets")
            if len(ids) > context_length:
                raise ValueError(f"{len(ids)} tokens exceeds context length {context_length}")
            artifacts.append({"key": row_key(row), "input_ids": ids, "labels": labels})
            if len(previews) < 3:
                spans = []
                current = []
                for label in labels[1:]:
                    if label == -100:
                        if current:
                            spans.append(tokenizer.decode(current))
                            current = []
                    else:
                        current.append(label)
                if current:
                    spans.append(tokenizer.decode(current))
                previews.append(
                    {
                        "row": identity,
                        "cell": row.get("cell"),
                        "tokens": len(ids),
                        "supervised_tokens": supervised,
                        "supervised_content": "\n[…masked…]\n".join(spans)[:2000],
                    }
                )
        except Exception as exc:
            incompatible += 1
            if len(issues) < 100:
                issues.append({"row": identity, "cell": row.get("cell"), "reason": str(exc)[:500]})
    report = {
        "ready": bool(artifacts) and incompatible == 0,
        "rows": len(rows),
        "tokens": total_tokens,
        "supervised_tokens": supervised_tokens,
        "max_tokens": longest,
        "incompatible_rows": incompatible,
        "issues": issues,
        "previews": previews,
    }
    return artifacts, report


def run(request_path: Path, output_dir: Path, load_tokenizer, tokenize):
    request = json.loads(request_path.read_text())
    digest = hashlib.sha256()
    for path in sorted(
        p for p in Path(__file__).parent.rglob("*") if p.suffix in {".py", ".jinja"}
    ):
        digest.update(str(path.relative_to(Path(__file__).parent)).encode())
        digest.update(path.read_bytes())
    if digest.hexdigest() != request["processor"]:
        raise ValueError(
            "The preprocessing worker is out of date. Deploy the current SFT worker before training."
        )
    tokenizer = load_tokenizer(request["tokenizer_model"], trust_remote_code=True)
    if not tokenizer.eos_token or tokenizer.eos_token == "<EOS_TOKEN>":
        for token in ("<|im_end|>", "<|eot_id|>", "</s>", "<|endoftext|>", "<|return|>"):
            value = tokenizer.convert_tokens_to_ids(token)
            if value is not None and value != tokenizer.unk_token_id:
                tokenizer.eos_token = token
                break
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token
    serving_template = tokenizer.chat_template
    artifacts, report = preprocess_rows(
        request["rows"], tokenizer, request["tokenizer_model"], request["context_length"], tokenize
    )
    report["vocab_fingerprint"] = hashlib.sha256(
        json.dumps(tokenizer.get_vocab(), sort_keys=True).encode()
    ).hexdigest()
    report["model"] = request["model"]
    report["chat_template_sha256"] = hashlib.sha256(str(serving_template).encode()).hexdigest()
    report["tokenizer_revision"] = tokenizer.init_kwargs.get("_commit_hash")
    report["context_length"] = request["context_length"]
    output_dir.mkdir(parents=True, exist_ok=True)
    if report["ready"]:
        with (output_dir / "tokens.jsonl").open("w") as target:
            for artifact in artifacts:
                target.write(json.dumps(artifact) + "\n")
        report["artifact_sha256"] = hashlib.sha256(
            (output_dir / "tokens.jsonl").read_bytes()
        ).hexdigest()
        tokenizer.chat_template = serving_template
        tokenizer.save_pretrained(output_dir / "tokenizer")
    (output_dir / "report.json").write_text(json.dumps(report))
