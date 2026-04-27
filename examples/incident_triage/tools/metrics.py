"""Mock Datadog-style metrics tool backed by local fixtures."""

from __future__ import annotations

import json
from pathlib import Path

from langchain_core.tools import tool

_FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "datadog"


@tool
def query_metrics(service: str, metric: str) -> str:
    """Get metric data for a service."""
    path = _FIXTURES / f"{service}.json"
    if not path.exists():
        return json.dumps({"error": f"no metrics for service {service}"})
    all_metrics = json.loads(path.read_text())
    if metric not in all_metrics:
        return json.dumps(
            {
                "error": f"metric {metric} not found",
                "available": list(all_metrics.keys()),
            }
        )
    return json.dumps({"service": service, "metric": metric, **all_metrics[metric]})
