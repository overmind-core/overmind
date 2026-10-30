"""Every coverage-map entry has at least one owning test."""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tests.coverage_map import COVERAGE_MAP  # noqa: E402

COVERS = re.compile(r"""pytest\.mark\.covers\(\s*["']([\w-]+)["']""")
# Vitest has no markers; every Console unit test owns console-logic.
VITEST_ENTRY = "console-logic"


def main() -> int:
    owned = {
        entry
        for path in (ROOT / "tests").rglob("test_*.py")
        for entry in COVERS.findall(path.read_text())
    }
    if any((ROOT / "frontend/src").rglob("*.test.ts*")):
        owned.add(VITEST_ENTRY)
    missing = sorted(set(COVERAGE_MAP) - owned)
    for entry in missing:
        print(f"{entry}: no test covers this coverage-map entry")
    return 1 if missing else 0


if __name__ == "__main__":
    sys.exit(main())
