from contextlib import ExitStack

import httpx
from django.db import connection

from .datasets import tickets, upload
from .stack import drain


def _client_connections() -> int:
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT count(*) FROM pg_stat_activity WHERE datname = current_database() "
            "AND backend_type = 'client backend' AND pid <> pg_backend_pid()"
        )
        return cursor.fetchone()[0]


def _open(stack: ExitStack, url: str, key: str, **headers) -> httpx.Response:
    response = stack.enter_context(
        httpx.stream("GET", url, headers={"X-Api-Key": key, **headers}, timeout=30)
    )
    assert response.status_code == 200
    return response


def test_open_dataset_streams_hold_no_database_connection(
    workshop, cli, sample_agent, live_api, worker, tmp_path
):
    dataset = upload(cli, sample_agent, tickets(tmp_path), "--intent", "eval")
    drain(worker)
    url = f"{live_api.url}/api/datasets/{dataset}/events/"
    key = cli.project_key(sample_agent)

    before = _client_connections()
    with ExitStack() as stack:
        for _ in range(5):
            lines = _open(stack, url, key).iter_lines()
            next(line for line in lines if "replay.done" in line)
        assert _client_connections() <= before
