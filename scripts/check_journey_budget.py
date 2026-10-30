"""Fail when the journeys run exceeds its CI budget or one journey takes over a third of it."""

from __future__ import annotations

import sys
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tests.coverage_map import JOURNEY_BUDGET_SECONDS  # noqa: E402

LONGEST = JOURNEY_BUDGET_SECONDS / 3


def main(junit: str) -> int:
    root = ET.parse(junit).getroot()
    suites = [root] if root.tag == "testsuite" else list(root.iter("testsuite"))
    wall = max(float(suite.get("time") or 0) for suite in suites)
    failures = []
    if wall > JOURNEY_BUDGET_SECONDS:
        failures.append(f"journeys took {wall:.0f} s; the budget is {JOURNEY_BUDGET_SECONDS} s")
    for case in root.iter("testcase"):
        seconds = float(case.get("time") or 0)
        if seconds > LONGEST:
            failures.append(
                f"{case.get('classname')}::{case.get('name')} took {seconds:.0f} s; "
                f"one test may take {LONGEST:.0f} s"
            )
    for failure in failures:
        print(failure)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
