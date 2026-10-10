"""Write chat-format JSONL datasets of a target size for load runs.

Rows look like the SFT uploads seen in production: a system prompt, a user question and
an assistant answer. Content is deterministic per seed, so two runs upload the same bytes.
"""

from __future__ import annotations

import argparse
import json
import random
from pathlib import Path

TOPICS = ["refund", "shipping", "password reset", "invoice", "plan upgrade", "data export"]


def row(rng: random.Random, index: int) -> str:
    topic = rng.choice(TOPICS)
    filler = " ".join(rng.choice(TOPICS) for _ in range(rng.randint(20, 120)))
    return json.dumps(
        {
            "messages": [
                {"role": "system", "content": "You are a support assistant for Acme."},
                {"role": "user", "content": f"Ticket {index}: question about {topic}. {filler}"},
                {"role": "assistant", "content": f"Here is how {topic} works. {filler}"},
            ]
        }
    )


def write(path: Path, megabytes: float, seed: int = 0) -> Path:
    rng = random.Random(seed)
    target = int(megabytes * 1024 * 1024)
    written = 0
    index = 0
    with path.open("w") as out:
        while written < target:
            line = row(rng, index) + "\n"
            out.write(line)
            written += len(line.encode())
            index += 1
    return path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--sizes", default="1,10,100,500", help="megabytes, comma separated")
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    for size in args.sizes.split(","):
        path = write(args.out / f"chat_{size}mb.jsonl", float(size))
        print(path, path.stat().st_size)


if __name__ == "__main__":
    main()
