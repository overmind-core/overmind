"""Tests for overmind.client — Client, Models, ChatCompletions, parsers.

All HTTP is mocked via unittest.mock; no real network traffic is made.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest
import requests

from overmind.client import (
    Client,
    OvermindInferenceError,
    _iter_sse_chunks,
    _raise_for_status,
)


def _make_client(base_url: str = "https://api.example.com") -> Client:
    return Client(api_key="om-test-key", base_url=base_url)


def _mock_response(
    *,
    ok: bool = True,
    status_code: int = 200,
    json_data: dict | None = None,
    text: str = "",
) -> MagicMock:
    resp = MagicMock(spec=requests.Response)
    resp.ok = ok
    resp.status_code = status_code
    resp.text = text
    resp.json.return_value = json_data or {}
    return resp


def _sse_response(lines: list[str]) -> MagicMock:
    """Build a mock streaming response whose iter_lines() yields encoded lines."""
    resp = MagicMock(spec=requests.Response)
    resp.ok = True
    resp.status_code = 200
    resp.iter_lines.return_value = [line.encode() for line in lines]
    return resp


class TestClientInit:
    def test_raises_without_api_key(self, monkeypatch):
        monkeypatch.delenv("OVERMIND_API_KEY", raising=False)
        with pytest.raises(ValueError, match="Missing API key"):
            Client()

    def test_reads_api_key_from_env(self, monkeypatch):
        monkeypatch.setenv("OVERMIND_API_KEY", "env-key")
        monkeypatch.delenv("OVERMIND_API_URL", raising=False)
        c = Client()
        assert c._session.headers.get("X-Api-Key") == "env-key"

    def test_explicit_key_overrides_env(self, monkeypatch):
        monkeypatch.setenv("OVERMIND_API_KEY", "env-key")
        c = Client(api_key="explicit-key")
        assert c._session.headers.get("X-Api-Key") == "explicit-key"

    def test_base_url_strips_trailing_slash(self):
        c = Client(api_key="k", base_url="https://api.example.com/")
        assert not c._base_url.endswith("/")

    def test_base_url_from_env(self, monkeypatch):
        monkeypatch.setenv("OVERMIND_API_URL", "https://custom.example.com")
        c = Client(api_key="k")
        assert c._base_url == "https://custom.example.com"

    def test_explicit_base_url_overrides_env(self, monkeypatch):
        monkeypatch.setenv("OVERMIND_API_URL", "https://env.example.com")
        c = Client(api_key="k", base_url="https://explicit.example.com")
        assert c._base_url == "https://explicit.example.com"

    def test_default_base_url_when_no_env(self, monkeypatch):
        monkeypatch.delenv("OVERMIND_API_URL", raising=False)
        c = Client(api_key="k")
        assert c._base_url == "https://api.overmindlab.ai"

    def test_content_type_header_set(self):
        c = _make_client()
        assert c._session.headers.get("Content-Type") == "application/json"


class TestRaiseForStatus:
    def test_error_falls_back_to_text_when_no_json(self):
        resp = _mock_response(ok=False, status_code=500, text="Internal Server Error")
        resp.json.side_effect = ValueError("no JSON")
        with pytest.raises(OvermindInferenceError, match="Internal Server Error"):
            _raise_for_status(resp)

    def test_error_uses_detail_field(self):
        resp = _mock_response(
            ok=False,
            status_code=403,
            json_data={"detail": "Authentication credentials were not provided."},
        )
        with pytest.raises(OvermindInferenceError, match="Authentication credentials"):
            _raise_for_status(resp)


class TestIterSseChunks:
    def _resp(self, lines: list[str]) -> MagicMock:
        resp = MagicMock(spec=requests.Response)
        resp.iter_lines.return_value = [line.encode() for line in lines]
        return resp

    def test_stops_at_done(self):
        payload = json.dumps({"id": "x", "object": "o", "model": "m", "choices": []})
        chunks = list(
            _iter_sse_chunks(
                self._resp([
                    f"data: {payload}",
                    "data: [DONE]",
                    f"data: {payload}",  # should never be reached
                ])
            )
        )
        assert len(chunks) == 1

    def test_skips_non_data_lines(self):
        payload = json.dumps({"id": "x", "object": "o", "model": "m", "choices": []})
        chunks = list(
            _iter_sse_chunks(
                self._resp([
                    "event: ping",
                    ": comment",
                    f"data: {payload}",
                    "data: [DONE]",
                ])
            )
        )
        assert len(chunks) == 1

    def test_skips_invalid_json(self):
        payload = json.dumps({"id": "x", "object": "o", "model": "m", "choices": []})
        chunks = list(
            _iter_sse_chunks(
                self._resp([
                    "data: {not valid json}",
                    f"data: {payload}",
                    "data: [DONE]",
                ])
            )
        )
        assert len(chunks) == 1

    def test_raises_on_string_error_in_stream(self):
        error_payload = json.dumps({"error": "The inference server could not be reached."})
        with pytest.raises(OvermindInferenceError, match="could not be reached"):
            list(_iter_sse_chunks(self._resp([f"data: {error_payload}"])))


class TestChatCompletionsNonStream:
    _MESSAGES = [{"role": "user", "content": "Hi"}]
    _RESPONSE_DATA = {
        "id": "cmpl-1",
        "object": "chat.completion",
        "model": "ft-test",
        "choices": [{"index": 0, "message": {"role": "assistant", "content": "Hello!"}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 2, "completion_tokens": 3, "total_tokens": 5},
    }

    def test_http_error_raises_inference_error(self):
        c = _make_client()
        c._session.post = MagicMock(
            return_value=_mock_response(ok=False, status_code=503, json_data={"error": {"message": "not ready"}})
        )
        with pytest.raises(OvermindInferenceError, match="not ready"):
            c.chat.completions.create(model="m", messages=self._MESSAGES)

    def test_timeout_raises_inference_error(self):
        c = _make_client()
        c._session.post = MagicMock(side_effect=requests.exceptions.Timeout())
        with pytest.raises(OvermindInferenceError, match="timed out"):
            c.chat.completions.create(model="m", messages=self._MESSAGES)

    def test_connection_error_raises_inference_error(self):
        c = _make_client()
        c._session.post = MagicMock(side_effect=requests.exceptions.ConnectionError("refused"))
        with pytest.raises(OvermindInferenceError, match="Request failed"):
            c.chat.completions.create(model="m", messages=self._MESSAGES)


class TestChatCompletionsStream:
    _MESSAGES = [{"role": "user", "content": "Stream test"}]

    def _chunk_line(self, content: str, model: str = "ft-test", chunk_id: str = "c1") -> str:
        data = {
            "id": chunk_id,
            "object": "chat.completion.chunk",
            "model": model,
            "choices": [{"index": 0, "delta": {"content": content}, "finish_reason": None}],
        }
        return f"data: {json.dumps(data)}"

    def test_stream_error_chunk_raises(self):
        c = _make_client()
        error_line = f"data: {json.dumps({'error': {'message': 'GPU OOM'}})}"
        c._session.post = MagicMock(return_value=_sse_response([error_line]))
        with pytest.raises(OvermindInferenceError, match="GPU OOM"):
            list(c.chat.completions.create(model="m", messages=self._MESSAGES, stream=True))


class TestModelsList:
    _FT_MODEL = {
        "id": "ft-test-abc",
        "object": "model",
        "created": 1700000000,
        "owned_by": "overmind",
        "finetuned": True,
        "status": "ready",
        "base_model": "meta-llama/llama-3.1-8b-instruct",
    }
    _FRONTIER = {
        "id": "anthropic/claude-sonnet-5",
        "object": "model",
        "created": 0,
        "owned_by": "anthropic",
        "finetuned": False,
        "status": "",
        "base_model": "",
    }

    def test_http_error_raises(self):
        c = _make_client()
        c._session.get = MagicMock(
            return_value=_mock_response(ok=False, status_code=401, json_data={"detail": "Unauthorized"})
        )
        with pytest.raises(OvermindInferenceError, match="401"):
            c.models.list()

    def test_network_error_raises(self):
        c = _make_client()
        c._session.get = MagicMock(side_effect=requests.exceptions.ConnectionError("refused"))
        with pytest.raises(OvermindInferenceError, match="Failed to list models"):
            c.models.list()


class TestModelsGet:
    _MODEL_DATA = {
        "id": "ft-test-abc",
        "object": "model",
        "created": 1700000000,
        "owned_by": "overmind",
        "finetuned": True,
        "status": "ready",
        "base_model": "meta-llama/llama-3.1-8b-instruct",
    }

    def test_404_raises_inference_error(self):
        c = _make_client()
        c._session.get = MagicMock(
            return_value=_mock_response(
                ok=False,
                status_code=404,
                json_data={"error": {"message": "Model not found"}},
            )
        )
        with pytest.raises(OvermindInferenceError, match="404"):
            c.models.get("ft-nonexistent")

    def test_network_error_raises(self):
        c = _make_client()
        c._session.get = MagicMock(side_effect=requests.exceptions.Timeout())
        with pytest.raises(OvermindInferenceError, match="Failed to retrieve model"):
            c.models.get("ft-test")


class TestModelsDelete:
    _DELETE_RESPONSE = {"id": "ft-test-abc", "object": "model", "deleted": True}

    def test_400_raises_inference_error(self):
        c = _make_client()
        c._session.delete = MagicMock(
            return_value=_mock_response(
                ok=False,
                status_code=400,
                json_data={"error": {"message": "not a fine-tuned model"}},
            )
        )
        with pytest.raises(OvermindInferenceError, match="not a fine-tuned model"):
            c.models.delete("anthropic/claude-sonnet-5")

    def test_409_raises_inference_error(self):
        c = _make_client()
        c._session.delete = MagicMock(
            return_value=_mock_response(
                ok=False,
                status_code=409,
                json_data={"error": {"message": "already being deleted"}},
            )
        )
        with pytest.raises(OvermindInferenceError, match="409"):
            c.models.delete("ft-being-deleted")

    def test_network_error_raises(self):
        c = _make_client()
        c._session.delete = MagicMock(side_effect=requests.exceptions.ConnectionError("refused"))
        with pytest.raises(OvermindInferenceError, match="Failed to delete model"):
            c.models.delete("ft-test")
