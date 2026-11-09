import argparse
import hashlib
import json
import re
import time
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("directory", type=Path)
args = parser.parse_args()
source = Path("output/workshop-scale/orca-50000/orca.jsonl")
directory = args.directory
evidence = json.loads((directory / "assignment-evidence.json").read_text())
assert evidence["source_order_verified"]
with (directory / "assignments.jsonl").open("rb") as handle:
    assert hashlib.file_digest(handle, "sha256").hexdigest() == evidence["assignments_sha256"]
assignments = [
    json.loads(line) for line in (directory / "assignments.jsonl").read_text().splitlines()
]
roles = [record["role"] for record in assignments]
assert [record["row"] for record in assignments] == list(range(len(roles)))
started = time.monotonic()
views = {"lexical": [], "numeric_substrings": []}
with source.open() as handle:
    for line in handle:
        question = unicodedata.normalize("NFKC", json.loads(line)["question"]).casefold()
        masked = re.sub(r"\d+(?:[.,]\d+)*", " NUM ", question)
        views["lexical"].append(set(re.findall(r"\w+", question)))
        views["numeric_substrings"].append(set(re.findall(r"\w+", masked)))
results = {}
for name, sets in views.items():
    assert len(sets) == len(roles)
    frequency = Counter(token for tokens in sets for token in tokens)
    index = defaultdict(list)
    edges = crossings = candidates = 0
    examples = []
    for row in sorted(range(len(sets)), key=lambda i: (len(sets[i]), i)):
        tokens = sets[row]
        size = len(tokens)
        minimum_size = (4 * size + 4) // 5
        prefix = sorted(tokens, key=lambda token: (frequency[token], token))[
            : size - minimum_size + 1
        ]
        possible = set()
        for token in prefix:
            possible.update(other for other in index[token] if len(sets[other]) >= minimum_size)
        candidates += len(possible)
        for other in possible:
            intersection = len(tokens & sets[other])
            union = size + len(sets[other]) - intersection
            if 5 * intersection >= 4 * union:
                edges += 1
                if roles[row] != roles[other]:
                    crossings += 1
                    if len(examples) < 10:
                        examples.append([row, other])
        for token in prefix:
            index[token].append(row)
    results[name] = {
        "candidates": candidates,
        "edges": edges,
        "cross_role_edges": crossings,
        "examples": examples,
    }
result = {
    "records": len(roles),
    "definition": "Prior run's lexical and regex numeric masking, inclusive token-set Jaccard >= 0.80",
    "views": results,
    "cross_role_edges": sum(view["cross_role_edges"] for view in results.values()),
    "seconds": time.monotonic() - started,
}
(directory / "prior-overlap-audit.json").write_text(json.dumps(result, indent=2) + "\n")
print(json.dumps(result))
assert results["lexical"]["edges"] == 27947
assert results["numeric_substrings"]["edges"] == 73495
assert result["cross_role_edges"] == 0, (
    "Prior-definition near matches cross the new split boundaries"
)
