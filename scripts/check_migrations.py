"""Numbering rules for migrations a branch adds on top of a base ref.

Usage: python scripts/check_migrations.py [BASE_REF]   (default: origin/main)

Django checks the migration graph. New migrations must have fresh numbers;
independent committed histories keep their identities when branches are merged.
"""

from __future__ import annotations

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


def merged_histories(base: str) -> dict[str, set[bytes]]:
    parents = [
        line.split()[1:]
        for line in git("rev-list", "--parents", "--min-parents=2", f"{base}..HEAD")
    ]
    pending = subprocess.run(
        ["git", "rev-parse", "--verify", "MERGE_HEAD"], capture_output=True, text=True
    )
    if pending.returncode == 0:
        parents.append(["HEAD", pending.stdout.strip()])
    established: dict[str, set[bytes]] = {}
    for group in parents:
        trees = {
            parent: {
                Path(path).name for path in git("ls-tree", "--name-only", parent, f"{MIGRATIONS}/")
            }
            for parent in group
        }
        for parent, names in trees.items():
            for other, other_names in trees.items():
                if parent == other:
                    continue
                for name in names - other_names:
                    n = number(name)
                    if n is None or not any(number(twin) == n for twin in other_names - names):
                        continue
                    raw = subprocess.run(
                        ["git", "show", f"{parent}:{MIGRATIONS}/{name}"],
                        check=True,
                        capture_output=True,
                    ).stdout
                    established.setdefault(name, set()).add(raw)
    return established


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
    historical = merged_histories(base)

    errors: list[str] = []
    for name in sorted(added):
        n = number(name)
        assert n is not None
        if name in historical:
            if (MIGRATIONS / name).read_bytes() not in historical[name]:
                errors.append(
                    f"{name}: independently committed migration was modified during the merge"
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
        f"Checked {len(added)} migrations; retained {len(set(added) & historical.keys())} independently committed identities."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "origin/main"))
