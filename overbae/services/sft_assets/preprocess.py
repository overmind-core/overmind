"""CPU preprocessing and the artifact consumed by the training engine."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from modal_shared.decisions import DECISION_OBJECTIVE, RENDERER, DecisionTokenizer, codebook
from modal_shared.preparation import preparation_failure, processor_fingerprint
from modal_shared.training_data import file_digest, row_key


def preprocess_rows(
    rows, tokenizer, model_id, context_length, tokenize, *, objective=None, output=None
):
    artifacts = []
    issues = []
    previews = []
    book = codebook(tokenizer) if objective == DECISION_OBJECTIVE else None
    encoder = DecisionTokenizer(tokenizer, book) if book is not None else None
    total_tokens = supervised_tokens = longest = incompatible = 0
    count = accepted = 0

    def emit(artifact):
        if output is None:
            artifacts.append(artifact)
        else:
            output.write(json.dumps(artifact) + "\n")

    for index, row in enumerate(rows):
        count += 1
        identity = row.get("source_row", index)
        try:
            if book is not None:
                result = encoder.training_row(row)
                ids = result["input_ids"]
                total_tokens += len(ids)
                supervised_tokens += 1
                longest = max(longest, len(ids))
                if len(ids) > context_length:
                    raise ValueError(f"{len(ids)} tokens exceeds context length {context_length}")
                emit({"key": row_key(row), **result})
                accepted += 1
                if len(previews) < 3:
                    previews.append(
                        {
                            "row": identity,
                            "cell": row.get("cell"),
                            "tokens": len(ids),
                            "decision_options": len(result["option_token_ids"]),
                            "target_probabilities": result["target_probabilities"],
                        }
                    )
                continue
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
            emit({"key": row_key(row), "input_ids": ids, "labels": labels})
            accepted += 1
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
        "ready": bool(accepted) and incompatible == 0,
        "rows": count,
        "tokens": total_tokens,
        "supervised_tokens": supervised_tokens,
        "max_tokens": longest,
        "incompatible_rows": incompatible,
        "issues": issues,
        "previews": previews,
    }
    if book is not None:
        report.update(objective=DECISION_OBJECTIVE, renderer=RENDERER, codebook=book)
    return artifacts, report


def run(request_path: Path, output_dir: Path, load_tokenizer, tokenize):
    request = json.loads(request_path.read_text())
    output_dir.mkdir(parents=True, exist_ok=True)
    if processor_fingerprint(Path(__file__).parent) != request["processor"]:
        report = preparation_failure("worker_out_of_date")
        (output_dir / "report.json").write_text(json.dumps(report))
        return
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
    tokens_path = output_dir / "tokens.jsonl"
    source_path = request["rows_path"]
    if file_digest(source_path) != request["rows_sha256"]:
        raise ValueError("The preparation input changed.")
    with tokens_path.open("w") as target, Path(source_path).open() as source:
        _, report = preprocess_rows(
            (json.loads(line) for line in source),
            tokenizer,
            request["tokenizer_model"],
            request["context_length"],
            tokenize,
            objective=request.get("objective"),
            output=target,
        )
    report["vocab_fingerprint"] = hashlib.sha256(
        json.dumps(tokenizer.get_vocab(), sort_keys=True).encode()
    ).hexdigest()
    report["model"] = request["model"]
    report["chat_template_sha256"] = hashlib.sha256(str(serving_template).encode()).hexdigest()
    report["tokenizer_revision"] = tokenizer.init_kwargs.get("_commit_hash")
    report["context_length"] = request["context_length"]
    if report["ready"]:
        report["artifact_sha256"] = file_digest(tokens_path)
        tokenizer.chat_template = serving_template
        tokenizer.save_pretrained(output_dir / "tokenizer")
    else:
        tokens_path.unlink(missing_ok=True)
    (output_dir / "report.json").write_text(json.dumps(report))
