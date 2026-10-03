"""CPU preprocessing and the artifact consumed by the training engine."""

from __future__ import annotations

import gzip
import hashlib
import json
import shutil
import time
from itertools import batched, islice
from pathlib import Path

from modal_shared.decisions import DECISION_OBJECTIVE, RENDERER, DecisionTokenizer, codebook
from modal_shared.preparation import preparation_failure, processor_fingerprint
from modal_shared.serving.artifacts import atomic_json
from modal_shared.training_data import file_digest, row_key

SHARD_ROWS = 10_000


def preprocess_rows(
    rows,
    tokenizer,
    model_id,
    context_length,
    tokenize,
    *,
    objective=None,
    output=None,
    row_offset=0,
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

    for index, row in enumerate(rows, start=row_offset):
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
        (output_dir / "report.json").write_text(json.dumps(report, sort_keys=True))
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
    report = prepare_shards(request, output_dir, tokenizer, tokenize)
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
    (output_dir / "report.json").write_text(json.dumps(report, sort_keys=True))


def prepare_shards(request, output_dir, tokenizer, tokenize):
    identity = hashlib.sha256(
        json.dumps(
            {
                "request": request,
                "vocab": tokenizer.get_vocab(),
                "template": tokenizer.chat_template,
                "revision": tokenizer.init_kwargs.get("_commit_hash"),
            },
            sort_keys=True,
        ).encode()
    ).hexdigest()
    directory = output_dir / "shards"
    directory.mkdir(exist_ok=True)
    manifest_path = directory / "manifest.json"
    manifest = {"identity": identity, "shards": []}
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text())
        if manifest.get("identity") != identity:
            raise ValueError("Preparation resume identity changed")
    offset = 0
    for shard in manifest["shards"]:
        path = directory / f"{offset:012d}.jsonl"
        if shard["offset"] != offset or file_digest(path) != shard["sha256"]:
            raise ValueError("Preparation shard integrity check failed")
        offset += shard["report"]["rows"]
    reused = offset

    def progress(stage):
        atomic_json(
            output_dir / "progress.json",
            {
                "stage": stage,
                "completed_rows": offset,
                "reused_rows": reused,
                "committed_shards": len(manifest["shards"]),
                "updated_at": time.time(),
            },
        )
        # The parent commits the Volume only after the manifest and shard are closed.
        print(json.dumps({"preparation_shard_committed": True}), flush=True)

    progress("tokenizing")
    with gzip.open(request["rows_path"], "rt", encoding="utf-8") as source:
        rows = (json.loads(line) for line in source)
        for _ in islice(rows, offset):
            pass
        for batch in batched(rows, SHARD_ROWS, strict=False):
            path = directory / f"{offset:012d}.jsonl"
            temporary = path.with_suffix(".partial")
            with temporary.open("w") as output:
                _, report = preprocess_rows(
                    batch,
                    tokenizer,
                    request["tokenizer_model"],
                    request["context_length"],
                    tokenize,
                    objective=request.get("objective"),
                    output=output,
                    row_offset=offset,
                )
            temporary.replace(path)
            manifest["shards"].append(
                {
                    "offset": offset,
                    "sha256": file_digest(path),
                    "report": report,
                }
            )
            atomic_json(manifest_path, manifest)
            offset += len(batch)
            progress("tokenizing")
    reports = [shard["report"] for shard in manifest["shards"]]
    report = {
        key: sum(r[key] for r in reports)
        for key in (
            "rows",
            "tokens",
            "supervised_tokens",
            "incompatible_rows",
        )
    }
    report.update(
        ready=bool(offset) and report["incompatible_rows"] == 0,
        max_tokens=max((r["max_tokens"] for r in reports), default=0),
        issues=[issue for r in reports for issue in r["issues"]][:100],
        previews=[preview for r in reports for preview in r["previews"]][:3],
    )
    if reports and "objective" in reports[0]:
        report.update({key: reports[0][key] for key in ("objective", "renderer", "codebook")})
    progress("materializing")
    temporary = output_dir / "tokens.partial"
    with temporary.open("wb") as target:
        for shard in manifest["shards"]:
            with (directory / f"{shard['offset']:012d}.jsonl").open("rb") as source:
                shutil.copyfileobj(source, target, length=1024 * 1024)
    temporary.replace(output_dir / "tokens.jsonl")
    progress("ready" if report["ready"] else "incompatible")
    return report
