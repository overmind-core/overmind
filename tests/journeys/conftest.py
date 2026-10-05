from __future__ import annotations

import json
import os
import re
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import pytest

SDK = Path(__file__).resolve().parents[2] / "overmind"
sys.path.insert(0, str(SDK))
os.environ["OVERMIND_ANALYTICS_ENABLED"] = "false"

from factories import make_user  # noqa: E402

import overmind  # noqa: E402
from overbae.models import APIToken  # noqa: E402

from .stack import LiveAPI, celery_worker, drain  # noqa: E402
from .surfaces import CliSurface, McpSurface, RestSurface, SampleAgent  # noqa: E402

RUNS = Path(__file__).parent / ".runs" / datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")


def pytest_configure(config):
    config.addinivalue_line("markers", "journey: end-to-end journey on the live stack")
    if Path(overmind.__file__).resolve().parents[1] != SDK:
        raise pytest.UsageError(f"Journeys must use the in-repo SDK, got {overmind.__file__}.")


def pytest_collection_modifyitems(items):
    for item in items:
        item.add_marker(pytest.mark.journey)


@pytest.fixture(autouse=True)
def _inline_dataset_tasks():
    yield


@pytest.fixture(autouse=True)
def _rubric_compiler():
    yield


@pytest.fixture(autouse=True)
def _journey_db(transactional_db):
    yield


@pytest.fixture(autouse=True)
def _only_fake_providers(monkeypatch):
    from overbae.core.model_registry import WORKSHOP_KEY_ENVS

    for env in WORKSHOP_KEY_ENVS:
        monkeypatch.delenv(env, raising=False)
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-fake")


@pytest.fixture(scope="session")
def live_api():
    api = LiveAPI().start()
    yield api
    api.stop()


@pytest.fixture(scope="session")
def _worker_ledger():
    import redis

    redis.from_url(os.environ["TEST_REDIS_URL"]).flushdb()
    with celery_worker() as ledger:
        yield ledger


@pytest.fixture
def worker(_worker_ledger):
    failed_before = set(_worker_ledger.failed())
    yield _worker_ledger
    drain(_worker_ledger)
    failed = {k: v for k, v in _worker_ledger.failed().items() if k not in failed_before}
    assert not failed, f"Background tasks failed: {failed}"


@pytest.fixture
def sample_agent(tmp_path) -> SampleAgent:
    return SampleAgent.copy("support_desk", tmp_path)


@pytest.fixture
def account():
    user = make_user()
    raw, _ = APIToken.create_for_user(user)
    return user, raw


@pytest.fixture
def account_key(account) -> str:
    return account[1]


@pytest.fixture
def cli(live_api, account_key) -> CliSurface:
    return CliSurface(live_api.url, account_key)


@pytest.fixture
def mcp_for(live_api):
    return lambda key: McpSurface(live_api.url, key)


@pytest.fixture
def rest_for(live_api):
    return lambda key: RestSurface(live_api.url, key)


@pytest.fixture
def llm_url(fake_llm):
    with fake_llm.serve() as url:
        yield url


def answer_turn(request) -> dict:
    if not request.body.get("tools") or any(m.get("role") == "tool" for m in request.messages):
        if "Quote the order id" in request.system:
            return {"content": GOOD_REPLY}
        return {"content": "Your refund is on its way. The order was delivered."}
    return {
        "content": None,
        "tool_calls": [
            {
                "id": f"call_{len(request.messages)}",
                "type": "function",
                "function": {"name": "lookup_order", "arguments": '{"order_id": "42"}'},
            }
        ],
    }


GOOD_REPLY = "Order 42 was delivered. Your refund is on its way."


def _judged_output(request) -> str:
    return request.text.split("\noutput:\n", 1)[-1]


@pytest.fixture
def rubric_judges(fake_llm):
    fake_llm.on_json(
        lambda r: r.schema_name == "JudgeResult",
        lambda r: {"score": 1.0 if GOOD_REPLY in r.text else 0.0, "reasoning": "fake judge"},
    )
    fake_llm.on_json(
        lambda r: r.schema_name == "ChecklistResult",
        lambda r: {
            "items": [
                {
                    "id": item,
                    "reasoning": "fake judge",
                    "not_applicable": False,
                    "verdict": GOOD_REPLY in _judged_output(r),
                }
                for item in re.findall(r"^- \(([^)]+)\)", r.text, re.M)
            ],
            "reasoning": "fake judge",
        },
    )


@pytest.fixture
def support_desk_llm(fake_llm, llm_url):
    fake_llm.on("You triage customer support tickets.", "refund")
    fake_llm.on("You are a support agent.", answer_turn)
    return llm_url


@pytest.hookimpl(wrapper=True)
def pytest_runtest_makereport(item, call):
    report = yield
    if report.when in ("setup", "call") and (report.failed or report.when == "call"):
        item.journey_report = report
    if report.when == "teardown" and hasattr(item, "journey_report"):
        failed = report.failed or item.journey_report.failed
        report_for_record = report if report.failed else item.journey_report
        llm = item.funcargs.get("fake_llm")
        ledger = item.funcargs.get("worker")
        record = {
            "node": item.nodeid,
            "command": " ".join(sys.argv),
            "outcome": "failed" if failed else "passed",
            "seconds": round(item.journey_report.duration, 3),
            "finished_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "env": {
                "database": os.environ.get("TEST_POSTGRES_HOST", "localhost"),
                "redis": os.environ.get("TEST_REDIS_URL"),
                "sdk": overmind.__version__ if hasattr(overmind, "__version__") else str(SDK),
            },
            "fixtures": {
                name: _digest(Path(__file__).parent / "agents" / name)
                for name in ("support_desk",)
                if "sample_agent" in item.funcargs
            },
            "llm": {
                "requests": [{"model": r.model, "url": r.url} for r in llm.requests] if llm else [],
                "unscripted": len(llm.unscripted) if llm else 0,
                "refused": list(llm.network.refused) if llm else [],
            },
            "tasks": {
                "failed": ledger.failed() if ledger else {},
                "scheduled": dict(ledger.scheduled) if ledger else {},
            },
            "error": str(report_for_record.longrepr)[-4000:] if failed else None,
        }
        RUNS.mkdir(parents=True, exist_ok=True)
        name = re.sub(r"[^\w.-]+", "_", item.nodeid)
        (RUNS / f"{name}.json").write_text(json.dumps(record, indent=2, default=str))
    return report


def _digest(path: Path) -> str:
    import hashlib

    digest = hashlib.sha256()
    for file in sorted(p for p in path.rglob("*") if p.is_file() and "__pycache__" not in p.parts):
        digest.update(file.relative_to(path).as_posix().encode())
        digest.update(file.read_bytes())
    return digest.hexdigest()[:16]


@pytest.fixture
def workshop(cli, mcp_for, sample_agent, worker, fake_llm, rubric_judges):
    fake_llm.extra_models.append("fake/candidate")
    fake_llm.on(lambda r: r.model == "fake/candidate", GOOD_REPLY)
    cli.scan(sample_agent)
    cli.sync(sample_agent)
    return mcp_for(cli.project_key(sample_agent))


@pytest.fixture
def clock():
    import time_machine

    traveller = time_machine.travel(datetime.now(UTC), tick=True)
    frozen = traveller.start()
    yield frozen
    traveller.stop()


@pytest.fixture
def beat(worker, clock):
    from overbae.celery import app

    def tick(*tasks: str) -> None:
        clock.shift(16)
        for task in tasks:
            app.send_task(task)
        drain(worker)

    return tick
