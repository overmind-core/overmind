import hashlib
import json

from modal_shared.decisions import DecisionTokenizer, decision_request


def input_digest(request):
    return hashlib.sha256(
        json.dumps(request, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def prepare_requests(records, output, failures, tokenizer, book, context_length):
    encoder = DecisionTokenizer(tokenizer, book)
    keys = set()
    report = {"ready_decisions": 0, "failed_decisions": 0, "tokens": 0, "max_tokens": 0}
    for record in records:
        if set(record) != {"key", "decision", "input_sha256"}:
            raise ValueError("Benchmark inference accepts only input records, without references")
        key = record["key"]
        if not isinstance(key, str) or not key or key in keys:
            raise ValueError("Benchmark decision keys must be nonempty and unique")
        keys.add(key)
        request = decision_request(record["decision"])
        digest = input_digest(request)
        if digest != record["input_sha256"]:
            raise ValueError("Benchmark input checksum differs")
        identity = {"key": key, "input_sha256": digest}
        try:
            prepared = encoder.request(request)
        except ValueError as exc:
            failures.write(
                json.dumps({**identity, "code": "tokenization", "error": str(exc)}) + "\n"
            )
            report["failed_decisions"] += 1
            continue
        length = len(prepared["input_ids"])
        report["max_tokens"] = max(report["max_tokens"], length)
        if length > context_length:
            failures.write(
                json.dumps({**identity, "code": "context_overflow", "tokens": length}) + "\n"
            )
            report["failed_decisions"] += 1
            continue
        report["ready_decisions"] += 1
        report["tokens"] += length
        output.write(json.dumps({**identity, **prepared}) + "\n")
    return report
