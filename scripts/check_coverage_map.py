"""Every test file names one coverage-map entry, and every entry has an owning test.

Pytest collection checks backend markers; Console test files carry a
``// covers: <entry>`` line that the vitest setup and this script check.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tests.coverage_map import COVERAGE_MAP, Kind  # noqa: E402

COVERS = re.compile(r"""pytest\.mark\.covers\(\s*["']([\w-]+)["']""")
VITEST_COVERS = re.compile(r"^// covers: (\S+)$", re.M)


def main() -> int:
    problems = []
    owned = {
        entry
        for path in (ROOT / "tests").rglob("test_*.py")
        for entry in COVERS.findall(path.read_text())
    }
    for path in sorted((ROOT / "frontend/src").rglob("*.test.ts*")):
        name = path.relative_to(ROOT)
        covers = VITEST_COVERS.findall(path.read_text())
        if len(covers) != 1 or covers[0] not in COVERAGE_MAP:
            problems.append(f"{name}: add one '// covers: <entry>' line naming a map entry")
        elif COVERAGE_MAP[covers[0]].kind is Kind.PROMISE:
            problems.append(f"{name}: a promise is covered only by its journey")
        else:
            owned.add(covers[0])
    problems += [
        f"{entry}: no test covers this coverage-map entry"
        for entry in sorted(set(COVERAGE_MAP) - owned)
    ]
    for problem in problems:
        print(problem)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
