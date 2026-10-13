"""Fixture rows, an independent oracle and the retained package for Workshop reproducibility runs.

Shared by the real-container tests and the live-stack driver; it imports nothing from overbae.
"""

import hashlib
import io
import json
import zipfile

WORDS = ["refund", "login", "invoice", "shipping"]
THRESHOLD = 60


def fixture_rows(count=1000):
    rows = []
    for index in range(count):
        # Every fiftieth row repeats the previous observation; duplicates must survive.
        base = index - 1 if index % 50 == 49 else index
        prefix = "\t" if base % 3 == 0 else "  "
        rows.append(
            {
                "text": f"{prefix}ticket {base:04d} {WORDS[base % 4]}  ",
                "priority": "" if base % 37 == 0 else ("low", "med", "high")[base % 3],
                "amount": (base * 37) % 101,
                "note": None if base % 11 == 0 else f"n{base % 13}",
            }
        )
    return rows


def fixture_sha256(rows):
    return hashlib.sha256(json.dumps(rows, sort_keys=True).encode()).hexdigest()


def oracle(rows, threshold):
    """Expected rows per step, computed from the fixture alone, never from pipeline code."""
    cleaned = [
        {**row, "text": row["text"].strip(), "source_row": index} for index, row in enumerate(rows)
    ]
    keep = [row for row in cleaned if row["amount"] >= threshold]
    rest = [row for row in cleaned if row["amount"] < threshold]
    derive = [{**row, "band": "high" if row["amount"] >= 80 else "mid"} for row in keep]
    label_rest = [{**row, "band": "low"} for row in rest]
    return {
        "clean": cleaned,
        "keep": keep,
        "rest": rest,
        "derive": derive,
        "label_rest": label_rest,
        "final": label_rest + derive,
    }


def script(transform):
    return (
        "import json, sys\n"
        "parameters = json.load(open(sys.argv[3]))\n"
        "with open(sys.argv[1]) as source, open(sys.argv[2], 'w') as output:\n"
        "    for line in source:\n"
        "        row = json.loads(line)\n"
        f"{transform}"
    )


SCRIPTS = {
    "clean.py": script(
        "        row['text'] = row['text'].strip()\n        output.write(json.dumps(row) + '\\n')\n"
    ),
    "keep.py": script(
        "        if row['amount'] >= parameters['threshold']:\n            output.write(line)\n"
    ),
    "rest.py": script(
        "        if row['amount'] < parameters['threshold']:\n            output.write(line)\n"
    ),
    "derive.py": script(
        "        row['band'] = 'high' if row['amount'] >= 80 else 'mid'\n"
        "        output.write(json.dumps(row) + '\\n')\n"
    ),
    "label_rest.py": script(
        "        row['band'] = 'low'\n        output.write(json.dumps(row) + '\\n')\n"
    ),
    "final.py": script("        output.write(line)\n"),
}
COLUMNS = {
    "text": "string",
    "priority": "string",
    "amount": "integer",
    "note": "any",
    "source_row": "integer",
}
BANDED = {**COLUMNS, "band": "string"}


def condition_line(file, needle):
    return next(
        number for number, text in enumerate(SCRIPTS[file].splitlines(), 1) if needle in text
    )


def manifest(runtime):
    def step(identity, entrypoint, parents, output=COLUMNS, **extra):
        return {
            "id": identity,
            **({"inputs": parents} if len(parents) > 1 else {"input": parents[0]}),
            "name": identity,
            "entrypoint": entrypoint,
            "input_schema": COLUMNS if output is COLUMNS or identity != "final" else BANDED,
            "output_schema": output,
            **extra,
        }

    return {
        "version": 1,
        "runtime": runtime,
        "parameters": {"threshold": "integer"},
        "limits": {"seconds": 60},
        "steps": [
            step("clean", "clean.py", ["source"], checks={"preserve_rows": True}),
            step(
                "keep",
                "keep.py",
                ["clean"],
                condition={
                    "expression": "amount >= threshold",
                    "line": condition_line("keep.py", ">= parameters"),
                },
            ),
            step(
                "rest",
                "rest.py",
                ["clean"],
                condition={
                    "expression": "amount < threshold",
                    "line": condition_line("rest.py", "< parameters"),
                },
            ),
            step("derive", "derive.py", ["keep"], BANDED),
            step("label_rest", "label_rest.py", ["rest"], BANDED),
            step(
                "final",
                "final.py",
                ["label_rest", "derive"],
                BANDED,
                checks={"preserve_rows": True},
            ),
        ],
    }


def archive(files):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as bundle:
        for name, content in files.items():
            bundle.writestr(name, content)
    return stream.getvalue()
