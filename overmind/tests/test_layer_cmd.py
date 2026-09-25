from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest
from typer.testing import CliRunner

from overmind.__main__ import app
from overmind.config import Capability, Config
from overmind.layer_cmd import (
    Client,
    LayerError,
    parse_models,
    resolve_capability,
    run_backtest,
    run_finetune,
)


class Response:
    def __init__(self, payload, status_code=200):
        self.payload = payload
        self.status_code = status_code
        self.ok = status_code < 400
        self.text = json.dumps(payload)

    def json(self):
        return self.payload


class Session:
    def __init__(self, responses):
        self.headers = {}
        self.calls = []
        self._responses = list(responses)

    def get(self, url, timeout=None):
        self.calls.append(("GET", url, None))
        return self._responses.pop(0)

    def post(self, url, json=None, timeout=None):
        self.calls.append(("POST", url, json))
        return self._responses.pop(0)


def _client(*responses, clock=None):
    now = {"t": 0.0}

    def tick():
        return now["t"]

    def sleep(_seconds):
        now["t"] += 1

    return Client(Session(responses), "https://api.example", sleep=sleep, clock=clock or tick)


def _dataset(dataset_id, *, rows=4):
    return {
        "id": dataset_id,
        "state": "idle",
        "rows": rows,
        "active": "cell-1",
        "source_spec": {"kept": rows, "skipped": 1},
        "cells": [{"id": "cell-1"}],
    }


def test_parse_models_rejects_duplicates_and_the_cap():
    assert parse_models(["openai/a, anthropic/b"], cap=5) == ["openai/a", "anthropic/b"]
    with pytest.raises(LayerError):
        parse_models(["openai/a", "openai/a"], cap=5)
    with pytest.raises(LayerError):
        parse_models(["a,b,c,d,e,f"], cap=5)
    with pytest.raises(LayerError):
        parse_models(None, cap=4)


def test_capability_defaults_to_the_only_toml_entry():
    only = Capability(slug="support", name="Support", id="11111111-1111-1111-1111-111111111111")
    config = Config(capabilities={"support": only})
    assert resolve_capability(config, "")[0] == only.id
    other = Capability(slug="other", name="Other", id="22222222-2222-2222-2222-222222222222")
    crowded = Config(capabilities={"support": only, "other": other})
    with pytest.raises(LayerError, match="Pass --capability"):
        resolve_capability(crowded, "")
    raw = "33333333-3333-3333-3333-333333333333"
    assert resolve_capability(Config(), raw)[0] == raw


def test_backtest_scores_each_model_and_exits_on_regression():
    session_client = _client(
        Response({
            "upstream_available": True,
            "models": [{"id": "openai/gpt-5-mini"}, {"id": "anthropic/claude-sonnet-4.5"}],
        }),
        Response({"active_eval_set": "set-1"}),
        Response({"id": "ds-1"}),
        Response(_dataset("ds-1")),
        Response({"id": "run-1", "run_evaluators": [{"id": "judge"}]}),
        Response({
            "id": "run-1",
            "status": "completed",
            "summary": {
                "baseline_comparison": {
                    "variants": [
                        {
                            "label": "openai/gpt-5-mini",
                            "overall": {
                                "status": "regressed",
                                "delta": -0.2,
                                "current": {"primary": 0.4},
                            },
                        },
                        {
                            "label": "anthropic/claude-sonnet-4.5",
                            "overall": {
                                "status": "improved",
                                "delta": 0.1,
                                "current": {"primary": 0.9},
                            },
                        },
                    ]
                }
            },
        }),
    )
    code = run_backtest(
        session_client,
        project_id="project",
        capability_id="cap",
        capability_slug="support",
        models=["openai/gpt-5-mini", "anthropic/claude-sonnet-4.5"],
        since=datetime(2026, 1, 1, tzinfo=UTC),
        until=None,
        limit=200,
        from_model="",
        timeout_s=30,
    )
    assert code == 1
    created = next(body for _method, url, body in session_client.session.calls if url.endswith("/api/eval-runs/"))
    labels = [variant["label"] for variant in created["variants_input"]]
    assert labels == ["recorded", "openai/gpt-5-mini", "anthropic/claude-sonnet-4.5"]
    assert created["variants_input"][1]["params"] == {"generation_strategy": "single_completion"}
    assert created["variants_input"][0]["mode"] == "existing"
    assert created["variants_input"][0]["is_baseline"] is True


def test_unknown_model_does_not_create_a_dataset():
    session_client = _client(Response({"upstream_available": False, "models": []}))
    with pytest.raises(LayerError, match="catalog is unavailable"):
        run_backtest(
            session_client,
            project_id="project",
            capability_id="cap",
            capability_slug="support",
            models=["openai/gpt-5-mini"],
            since=datetime(2026, 1, 1, tzinfo=UTC),
            until=None,
            limit=10,
            from_model="",
            timeout_s=30,
        )
    assert session_client.session.calls == [("GET", "https://api.example/api/models/catalog/", None)]


def test_finetune_starts_one_job_per_model_in_one_group():
    session_client = _client(
        Response({
            "models": {
                "small": [
                    {"id": "Qwen/Qwen2.5-7B-Instruct"},
                    {"id": "meta-llama/Llama-3.1-8B-Instruct"},
                ]
            }
        }),
        Response({"active_eval_set": "set-1"}),
        Response({"train": {"id": "train-1"}, "eval": {"id": "eval-1"}}),
        Response(_dataset("train-1")),
        Response(_dataset("eval-1")),
        Response({"id": "job-1"}),
        Response({"id": "job-2"}),
    )
    code = run_finetune(
        session_client,
        project_id="project",
        capability_id="cap",
        capability_slug="support",
        models=["Qwen/Qwen2.5-7B-Instruct", "meta-llama/Llama-3.1-8B-Instruct"],
        since=datetime(2026, 1, 1, tzinfo=UTC),
        until=None,
        limit=200,
        from_model="",
        eval_percent=20,
        timeout_s=30,
    )
    assert code == 0
    jobs = [body for method, url, body in session_client.session.calls if url.endswith("/api/finetuning-jobs/")]
    assert [job["base_model"] for job in jobs] == [
        "Qwen/Qwen2.5-7B-Instruct",
        "meta-llama/Llama-3.1-8B-Instruct",
    ]
    assert jobs[0]["group_id"] == jobs[1]["group_id"]
    assert jobs[0]["dataset"] == "train-1"
    assert jobs[0]["eval_dataset"] == "eval-1"
    split = next(body for method, url, body in session_client.session.calls if url.endswith("/split/"))
    assert split["position"] == "hash"


def test_a_later_finetune_failure_reports_the_jobs_already_queued():
    session_client = _client(
        Response({
            "models": {"small": [{"id": "Qwen/Qwen2.5-7B-Instruct"}, {"id": "meta-llama/Llama-3.1-8B-Instruct"}]}
        }),
        Response({"active_eval_set": "set-1"}),
        Response({"train": {"id": "train-1"}, "eval": {"id": "eval-1"}}),
        Response(_dataset("train-1")),
        Response(_dataset("eval-1")),
        Response({"id": "job-1"}),
        Response({"detail": "rejected"}, status_code=400),
    )
    with pytest.raises(LayerError, match="Queued: 1"):
        run_finetune(
            session_client,
            project_id="project",
            capability_id="cap",
            capability_slug="support",
            models=["Qwen/Qwen2.5-7B-Instruct", "meta-llama/Llama-3.1-8B-Instruct"],
            since=datetime(2026, 1, 1, tzinfo=UTC),
            until=None,
            limit=20,
            from_model="",
            eval_percent=20,
            timeout_s=30,
        )


def test_backtest_times_out_while_the_run_is_still_going():
    session_client = _client(
        Response({"upstream_available": True, "models": [{"id": "openai/gpt-5-mini"}]}),
        Response({"active_eval_set": "set-1"}),
        Response({"id": "ds-1"}),
        Response(_dataset("ds-1")),
        Response({"id": "run-1", "run_evaluators": [{"id": "judge"}]}),
        Response({"id": "run-1", "status": "running"}),
        clock=lambda: 100.0,
    )
    with pytest.raises(LayerError, match="Timed out") as exc:
        run_backtest(
            session_client,
            project_id="project",
            capability_id="cap",
            capability_slug="support",
            models=["openai/gpt-5-mini"],
            since=datetime(2026, 1, 1, tzinfo=UTC),
            until=None,
            limit=10,
            from_model="",
            timeout_s=0,
        )
    assert exc.value.code == 2


def test_cli_requires_models():
    result = CliRunner().invoke(app, ["backtest"])
    assert result.exit_code == 1
    assert "models" in result.output.lower()
