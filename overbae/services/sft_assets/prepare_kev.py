"""CPU preparation for Kev. Counts state tokens with the base tokenizer."""

import json
from dataclasses import dataclass

from kev_engine import admit_kev_row
from preprocess import run
from transformers import AutoTokenizer


@dataclass
class _Encoded:
    input_ids: list[int]
    state: str


def _encode(tokenizer, record, max_length):
    if max_length < 1:
        raise ValueError("max_length")
    state = record["state"]
    ids = tokenizer(state, add_special_tokens=False)["input_ids"]
    return _Encoded(input_ids=list(ids), state=state)


def prepare(request, output):
    run(
        request,
        output,
        AutoTokenizer.from_pretrained,
        None,
        decision_encode=_encode,
        decision_admit=admit_kev_row,
    )
    report_path = output / "report.json"
    report = json.loads(report_path.read_text())
    if report.get("ready"):
        report["renderer"] = "kev_pointer"
        report_path.write_text(json.dumps(report, sort_keys=True))
