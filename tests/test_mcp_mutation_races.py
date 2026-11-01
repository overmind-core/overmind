from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from mcp_fixtures import asgi_db
from starlette.testclient import TestClient
from test_mcp_account_access import account, rpc

from overbae.models import EvalMatrix, MCPMutation, ProjectMembership
from overbae.services.mcp.server import create_mcp_application

pytestmark = [asgi_db, pytest.mark.covers("work-once")]


def test_concurrent_retries_create_once_and_revoked_membership_cannot_replay():
    user, projects, key = account()
    arguments = {
        "project_id": str(projects[0].pk),
        "name": "Concurrent check",
        "type": "llm",
        "source": "Answer accurately.",
        "request_key": "concurrent-create",
    }
    gate = Barrier(2)
    with TestClient(create_mcp_application()) as client:

        def submit():
            gate.wait(timeout=10)
            return rpc(
                client,
                key,
                "tools/call",
                {"name": "create_eval_matrix_row", "arguments": arguments},
            )["result"]

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: submit(), range(2)))
        successes = [result for result in results if not result.get("isError")]
        assert successes
        assert all(
            result["structuredContent"]["error"]["code"] == "request_in_progress"
            for result in results
            if result.get("isError")
        )
        assert EvalMatrix.objects.filter(project=projects[0]).count() == 1
        assert MCPMutation.objects.filter(project=projects[0]).count() == 1
        replay = rpc(
            client, key, "tools/call", {"name": "create_eval_matrix_row", "arguments": arguments}
        )["result"]
        assert replay == successes[0]
        ProjectMembership.objects.filter(project=projects[0], user=user).delete()
        denied = rpc(
            client, key, "tools/call", {"name": "create_eval_matrix_row", "arguments": arguments}
        )["result"]
        assert denied["isError"]
        assert denied["structuredContent"]["error"]["code"] == "project_required"
