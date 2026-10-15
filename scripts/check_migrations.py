"""Numbering rules for migrations a branch adds on top of a base ref.

Usage: python scripts/check_migrations.py [BASE_REF]   (default: origin/main)

Django checks the migration graph. New migrations must have fresh numbers;
only the checksum-pinned applied histories retain their original identities.
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

MIGRATIONS = Path("overbae/migrations")
NUMBER = re.compile(r"^(\d{4})_")


def git(*args: str) -> list[str]:
    result = subprocess.run(["git", *args], check=True, capture_output=True, text=True)
    return [line for line in result.stdout.splitlines() if line]


def number(name: str) -> int | None:
    match = NUMBER.match(name)
    return int(match.group(1)) if match else None


def applied_histories():
    # These exact histories were applied before integration; renumbering would replay them.
    return json.loads(Path(__file__).with_name("applied-migration-history.json").read_text())


def main(base: str) -> int:
    added = [
        Path(path).name
        for path in git(
            "diff", "--name-only", "--diff-filter=A", base, "HEAD", "--", str(MIGRATIONS)
        )
        if path.endswith(".py") and number(Path(path).name) is not None
    ]
    if not added:
        print(f"No migrations added on top of {base}.")
        return 0

    base_names = [Path(p).name for p in git("ls-tree", "--name-only", base, f"{MIGRATIONS}/")]
    base_max = max((number(n) or 0 for n in base_names), default=0)
    on_disk = [p.name for p in MIGRATIONS.glob("*.py")]
    historical = applied_histories()

    errors: list[str] = []
    for name in sorted(added):
        n = number(name)
        assert n is not None
        if name in historical:
            if hashlib.sha256((MIGRATIONS / name).read_bytes()).hexdigest() not in historical[name]:
                errors.append(
                    f"{name}: applied migration differs from its pinned historical checksum"
                )
            continue
        if "_merge_" in name:
            errors.append(f"{name}: merge migration — rebase on {base} and renumber instead")
        if n <= base_max:
            errors.append(f"{name}: {n:04d} is not above the highest on {base} ({base_max:04d})")
        twins = [other for other in on_disk if other != name and number(other) == n]
        if twins:
            errors.append(f"{name}: {n:04d} is also used by {', '.join(sorted(twins))}")

    if errors:
        print("\n".join(errors))
        return 1
    print(
        f"Checked {len(added)} migrations; retained {len(set(added) & historical.keys())} checksum-pinned applied identities."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "origin/main"))
