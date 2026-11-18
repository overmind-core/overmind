"""What a package script reads: the frame rows the platform hands it, as JSONL. A change
here changes the sandbox runtime identity."""

import json
import math
from pathlib import Path

# The column kinds the platform stores with every frame.
MANIFEST_KEY = b"overmind.columns"


def _decode(value, kind):
    # JSON has no NaN or infinity: a missing number reaches the script as null.
    if value is None or (isinstance(value, float) and not math.isfinite(value)):
        return None
    if kind == "json":
        try:
            return json.loads(value, parse_constant=lambda _: None)
        except ValueError:
            return value
    if kind == "datetime":
        return value.isoformat()
    return value


def write_jsonl(batch, kinds: dict, destination: Path) -> None:
    """A batch of frame rows as one JSON object per line, nested values decoded."""
    names = batch.schema.names
    columns = [
        [_decode(v, kinds.get(name)) for v in batch.column(name).to_pylist()] for name in names
    ]
    with destination.open("w", encoding="utf-8") as output:
        for values in zip(*columns, strict=True):
            output.write(
                json.dumps(
                    dict(zip(names, values, strict=True)), ensure_ascii=False, allow_nan=False
                )
                + "\n"
            )
