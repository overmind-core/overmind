import json
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from factories import make_project, make_user

from overbae.models import APIToken, Dataset
from overbae.services.datasets import land
from tests.journeys.surfaces import McpSurface
from tests.load.processes import ProcessAPI

pytestmark = pytest.mark.django_db(transaction=True)


def test_cpu_heavy_queries_cannot_take_another_projects_sql_capacity(settings, tmp_path):
    settings.DATASET_QUERY_TIMEOUT_SECONDS = 10
    settings.DATASET_QUERY_MAX_IN_FLIGHT = 3
    user = make_user()
    identities = []
    for name in ("query-noisy", "query-quiet", "query-overflow"):
        project = make_project(name=name, member=user)
        dataset = Dataset.objects.create(project=project, name=name, intent="explore")
        land.land_rows(dataset, [{"value": 1}], user=user)
        dataset.refresh_from_db()
        key, _ = APIToken.create_for_user(user, project=project)
        identities.append((dataset, key))

    with ProcessAPI(settings, tmp_path) as api:
        noisy, quiet, overflow = [McpSurface(api.url, key) for _, key in identities]
        declared = noisy.read("overmind://interface/current")["dataset_transformations"][
            "query_capacity"
        ]
        assert declared["global_concurrency"] == 3 and declared["project_concurrency"] == 2
        dataset = identities[0][0]
        args = {"dataset": str(dataset.pk), "cell": str(dataset.active_cell.pk)}
        expensive = {**args, "sql": "SELECT sum(sin(i)) FROM range(20000000000) AS r(i)"}
        inspection = {**args, "sql": "SELECT count(*) AS count FROM t"}
        noisy.call("query_dataset", inspection)

        def run_expensive(surface, arguments):
            deadline = time.monotonic() + 8
            while True:
                result = surface.call_result("query_dataset", arguments)
                if (
                    not result.isError
                    or result.structuredContent["error"]["code"] != "query_capacity_busy"
                ):
                    return result
                error = result.structuredContent["error"]
                assert error["retryable"] and error["fields"]["cell"] == arguments["cell"]
                assert time.monotonic() < deadline, (
                    "A heavy query could not acquire its available slot"
                )
                time.sleep(float(error["fields"]["retry_after_seconds"]))

        with ThreadPoolExecutor(max_workers=3) as executor:
            futures = [executor.submit(run_expensive, noisy, expensive) for _ in range(2)]
            deadline = time.monotonic() + 8
            while time.monotonic() < deadline:
                result = noisy.call_result("query_dataset", inspection)
                if result.isError:
                    busy = result.structuredContent["error"]
                    break
                time.sleep(0.02)
            else:
                raise AssertionError("The heavy queries never occupied the declared project slots")
            assert busy["code"] == "query_capacity_busy" and busy["retryable"]
            assert busy["fields"]["cell"] == args["cell"]
            assert busy["fields"]["retry_after_seconds"] == "1"
            assert not any(future.done() for future in futures)
            protected = identities[1][0]
            started = time.monotonic()
            answer = quiet.call(
                "query_dataset",
                {
                    "dataset": str(protected.pk),
                    "cell": str(protected.active_cell.pk),
                    "sql": "SELECT count(*) AS count FROM t",
                },
            )
            elapsed = time.monotonic() - started
            assert answer["rows"] == [{"count": 1}]
            # The heavy queries hold their slots until the 10 s timeout, so waiting for one
            # would take seconds; the bound leaves headroom for shared CI runners.
            assert elapsed < 2
            assert not any(future.done() for future in futures)
            futures.append(
                executor.submit(
                    run_expensive,
                    quiet,
                    {
                        "dataset": str(protected.pk),
                        "cell": str(protected.active_cell.pk),
                        "sql": expensive["sql"],
                    },
                )
            )
            third = identities[2][0]
            deadline = time.monotonic() + 4
            while time.monotonic() < deadline:
                result = overflow.call_result(
                    "query_dataset",
                    {
                        "dataset": str(third.pk),
                        "cell": str(third.active_cell.pk),
                        "sql": inspection["sql"],
                    },
                )
                if result.isError:
                    global_busy = result.structuredContent["error"]
                    break
                time.sleep(0.1)
            else:
                raise AssertionError("The declared global query budget was not enforced")
            assert global_busy["code"] == "query_capacity_busy" and global_busy["retryable"]
            assert not any(future.done() for future in futures)
            outcomes = [future.result(timeout=20).structuredContent["error"] for future in futures]
            assert all(error["code"] == "query_timeout" for error in outcomes)
        assert noisy.call("query_dataset", inspection)["rows"] == [{"count": 1}]
        assert all(source.cells.count() == 1 for source, _ in identities)
        Path("/tmp/overmind-query-capacity.json").write_text(
            json.dumps(
                {
                    "actual_cpu_heavy_queries": 3,
                    "expected_timeouts": 3,
                    "extra_noisy_query": busy,
                    "extra_project_at_global_capacity": global_busy,
                    "protected_query_seconds": elapsed,
                    "capacity_released_after_timeout": True,
                    "source_cells_unchanged": True,
                },
                indent=2,
            )
        )
