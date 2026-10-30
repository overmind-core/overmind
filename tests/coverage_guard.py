"""Collection fails unless every test covers exactly one coverage-map entry."""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path

import pytest

from .coverage_map import COVERAGE_MAP, Kind

JOURNEYS = Path(__file__).parent / "journeys"


def pytest_configure(config):
    config.addinivalue_line("markers", "covers(entry): the coverage-map entry this test owns")


@pytest.hookimpl(trylast=True)
def pytest_collection_modifyitems(config, items):
    problems = []
    journeys = defaultdict(set)
    for item in items:
        marker = item.get_closest_marker("covers")
        if marker is None or len(marker.args) != 1:
            problems.append(f"{item.nodeid}: add pytest.mark.covers(<entry>)")
            continue
        entry = COVERAGE_MAP.get(marker.args[0])
        if entry is None:
            problems.append(f"{item.nodeid}: {marker.args[0]!r} is not in tests/coverage_map.py")
            continue
        if entry.kind is not Kind.PROMISE:
            continue
        if JOURNEYS not in Path(item.path).parents:
            problems.append(f"{item.nodeid}: a promise is covered only by its journey")
        journeys[marker.args[0]].add(item.originalname)
        variant = item.callspec.id if hasattr(item, "callspec") else None
        if variant not in (entry.variants or (None,)):
            problems.append(
                f"{item.nodeid}: declare variant {variant!r} for {marker.args[0]!r} "
                "in tests/coverage_map.py"
            )
    for promise, names in journeys.items():
        if len(names) > 1:
            problems.append(f"{promise}: one journey per promise, found {sorted(names)}")
    if problems:
        raise pytest.UsageError("Coverage map:\n  " + "\n  ".join(problems))
