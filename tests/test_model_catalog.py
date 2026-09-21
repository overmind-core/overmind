from __future__ import annotations

import uuid
from unittest import mock

import pytest
import requests
from django.core.cache import cache
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from overbae.models import User
from overbae.services import model_catalog

pytestmark = pytest.mark.django_db

CATALOG_URL = "/api/models/catalog/"


def _auth_client() -> APIClient:
    user = User.objects.create_user(
        email=f"u-{uuid.uuid4().hex[:6]}@example.com",
        password="test-pass-123",
        clerk_user_id=f"clerk_{uuid.uuid4().hex}",
    )
    client = APIClient()
    token = RefreshToken.for_user(user)
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {token.access_token}")
    return client


@pytest.fixture(autouse=True)
def _clear_catalog_cache():
    cache.delete(model_catalog._CACHE_KEY)
    yield
    cache.delete(model_catalog._CACHE_KEY)


def _upstream_payload() -> dict:
    return {
        "data": [
            {
                "id": "mistralai/mistral-large",
                "hugging_face_id": "mistralai/Mistral-Large-Instruct-2411",
                "name": "Mistral: Mistral Large",
                "context_length": 128000,
                "pricing": {"prompt": "0.000002", "completion": "0.000006"},
            },
            {
                "id": "openai/gpt-5-mini",
                "name": "OpenAI: GPT-5 Mini",
                "context_length": 400000,
                "pricing": {"prompt": "0.00000025", "completion": "0.000002"},
            },
            # Vision-capable LLM — image input, text output — must pass.
            {
                "id": "openai/gpt-5-vision",
                "name": "OpenAI: GPT-5 Vision",
                "architecture": {"modality": {"input": ["text", "image"], "output": ["text"]}},
            },
            # Legacy shape: `architecture.modality` as a string — must pass.
            {
                "id": "meta/muse-spark-1.2",
                "name": "Meta: Muse Spark",
                "architecture": {"modality": "text+image"},
            },
            # Current shape: arrow-format string — text-only chat must pass.
            {
                "id": "deepseek/deepseek-v4-flash",
                "name": "DeepSeek: DeepSeek V4 Flash",
                "architecture": {"modality": "text->text"},
            },
            # Arrow-format image output must be skipped.
            {
                "id": "black-forest-labs/flux-2-image",
                "name": "Black Forest Labs: Flux 2 Image",
                "architecture": {"modality": "text->image"},
            },
            # Arrow-format image generator must be skipped.
            {
                "id": "black-forest-labs/flux-2-arrow",
                "name": "Black Forest Labs: Flux 2",
                "architecture": {"modality": "image->image"},
            },
            # Non-chat models the trimmer must skip.
            {
                "id": "black-forest-labs/flux-1.1-pro",
                "name": "Black Forest Labs: Flux",
                "modality": "image",
            },
            {
                "id": "kling-video/kling-v1",
                "name": "Kling: Kling Video",
                "modality": "video",
            },
            {
                "id": "openai/text-embedding-3-large",
                "name": "OpenAI: Embedding",
                "architecture": {"modality": {"input": ["text"], "output": ["embedding"]}},
            },
            # Degenerate entries the trimmer must skip.
            {"id": "", "name": "nameless"},
            {"id": "no-slash-slug"},
        ]
    }


def _mock_response(payload: dict, status: int = 200) -> mock.Mock:
    response = mock.Mock()
    response.status_code = status
    response.json.return_value = payload
    response.raise_for_status = mock.Mock()
    return response


class TestModelCatalogEndpoint:
    def test_returns_trimmed_models(self):
        client = _auth_client()
        with mock.patch.object(
            model_catalog.requests, "get", return_value=_mock_response(_upstream_payload())
        ) as get:
            res = client.get(CATALOG_URL)

        assert res.status_code == 200
        body = res.json()
        assert body["upstream_available"] is True
        assert get.call_count == 1

        models = body["models"]
        assert [m["id"] for m in models] == [
            "deepseek/deepseek-v4-flash",
            "meta/muse-spark-1.2",
            "mistralai/mistral-large",
            "openai/gpt-5-mini",
            "openai/gpt-5-vision",
        ]

        mistral = models[2]
        assert mistral["name"] == "Mistral: Mistral Large"
        assert mistral["provider"] == "mistralai"
        assert mistral["context_length"] == 128000
        # $-per-token strings normalized to $/1M tokens.
        assert mistral["prompt_price"] == pytest.approx(2.0)
        assert mistral["completion_price"] == pytest.approx(6.0)
        assert mistral["curated"] is False

        gpt = models[3]
        assert gpt["curated"] is True  # maps onto curated gpt-5-mini
        assert gpt["prompt_price"] == pytest.approx(0.25)

    def test_upstream_failure_returns_empty_list_not_500(self):
        client = _auth_client()
        with mock.patch.object(
            model_catalog.requests,
            "get",
            side_effect=requests.ConnectionError("upstream down"),
        ):
            res = client.get(CATALOG_URL)

        assert res.status_code == 200
        body = res.json()
        assert body["models"] == [] and body["upstream_available"] is False
        assert body["defaults"]["judge_model"] == body["defaults"]["judge_models"][0]

    def test_catalog_is_cached(self):
        client = _auth_client()
        with mock.patch.object(
            model_catalog.requests, "get", return_value=_mock_response(_upstream_payload())
        ) as get:
            first = client.get(CATALOG_URL)
            second = client.get(CATALOG_URL)

        assert first.status_code == second.status_code == 200
        assert get.call_count == 1
        assert first.json() == second.json()

    def test_failure_is_not_cached(self):
        client = _auth_client()
        with mock.patch.object(
            model_catalog.requests,
            "get",
            side_effect=[
                requests.ConnectionError("blip"),
                _mock_response(_upstream_payload()),
            ],
        ) as get:
            failed = client.get(CATALOG_URL)
            recovered = client.get(CATALOG_URL)

        assert get.call_count == 2
        assert failed.json()["upstream_available"] is False
        assert recovered.json()["upstream_available"] is True

    def test_sends_api_key_header_when_present(self):
        client = _auth_client()
        with (
            mock.patch.dict("os.environ", {"OPENROUTER_API_KEY": "sk-or-test"}),
            mock.patch.object(
                model_catalog.requests, "get", return_value=_mock_response(_upstream_payload())
            ) as get,
        ):
            client.get(CATALOG_URL)

        headers = get.call_args.kwargs["headers"]
        assert headers["Authorization"] == "Bearer sk-or-test"

    def test_requires_authentication(self):
        res = APIClient().get(CATALOG_URL)
        assert res.status_code == 401


class TestCatalogSlugRouting:
    """call_llm must accept the exact `id` strings the catalog returns."""

    def test_slash_slug_routes_through_openrouter(self):
        from overbae.core import llms

        response = mock.Mock()
        message = mock.Mock(content="ok", tool_calls=None)
        message.model_extra = {}
        response.choices = [mock.Mock(message=message, logprobs=None)]
        response.usage = mock.Mock(prompt_tokens=1, completion_tokens=1)
        response.usage.model_extra = {}
        response._response_ms = 5
        client = mock.Mock()
        client.chat.completions.create.return_value = response

        with (
            mock.patch.dict("os.environ", {"OPENROUTER_API_KEY": "sk-or-test"}),
            mock.patch.object(llms, "_openrouter_client", return_value=client),
        ):
            content, _ = llms.call_llm("hi", model="mistralai/mistral-large")

        assert content == "ok"
        assert client.chat.completions.create.call_args.kwargs["model"] == "mistralai/mistral-large"

    def test_slash_slug_without_openrouter_key_fails_fast(self):
        from overbae.core import llms

        # The client is lru_cached, so a test that already built one would hand
        # this call a live client and the key check would never run.
        llms._provider_client.cache_clear()
        with (
            mock.patch.dict("os.environ", {}, clear=False),
            mock.patch.object(llms.os.environ, "get", return_value=None),
            pytest.raises(RuntimeError, match="OPENROUTER_API_KEY"),
        ):
            llms.call_llm("hi", model="mistralai/mistral-large")

    def test_bare_unknown_model_still_rejected(self):
        from overbae.core import llms

        with pytest.raises(RuntimeError, match="Unsupported model"):
            llms.call_llm("hi", model="totally-unknown-model")


def test_catalog_retains_checkpoint_identity_in_its_cache():
    with mock.patch.object(
        model_catalog.requests, "get", return_value=_mock_response(_upstream_payload())
    ):
        entries, available = model_catalog.fetch_model_catalog()
    assert available
    entry = next(row for row in entries if row["id"] == "mistralai/mistral-large")
    assert entry["hugging_face_id"] == "mistralai/Mistral-Large-Instruct-2411"


@pytest.mark.parametrize(
    ("model", "expected"),
    [
        ("Qwen/Qwen3.5-9B", "qwen/qwen3.5-9b"),
        ("qwen/qwen3.5-9b", "qwen/qwen3.5-9b"),
        ("meta-llama/Meta-Llama-3.1-8B-Instruct", "meta-llama/llama-3.1-8b-instruct"),
        ("meta-llama/Llama-3.1-8B-Instruct", "meta-llama/llama-3.1-8b-instruct"),
        ("mistralai/Mistral-Large-Instruct-2411", "mistralai/mistral-large"),
        ("Qwen/Qwen3.5-27B", None),
        ("Qwen/Qwen3.5-9B-Base", None),
        ("meta-llama/Meta-Llama-3.1-8B", None),
        ("another-org/Qwen3.5-9B", None),
        ("Qwen3.5-9B", None),
        ("", None),
    ],
)
def test_training_route_requires_an_exact_available_model(model, expected):
    entries = [
        {"id": "qwen/qwen3.5-9b:free", "hugging_face_id": "Qwen/Qwen3.5-9B"},
        {"id": "qwen/qwen3.5-9b", "hugging_face_id": "Qwen/Qwen3.5-9B"},
        {
            "id": "meta-llama/llama-3.1-8b-instruct",
            "hugging_face_id": "meta-llama/Meta-Llama-3.1-8B-Instruct",
        },
        {
            "id": "mistralai/mistral-large",
            "hugging_face_id": "mistralai/Mistral-Large-Instruct-2411",
        },
    ]
    with mock.patch.object(model_catalog, "fetch_model_catalog", return_value=(entries, True)):
        assert model_catalog.resolve_training_openrouter_slug(model) == expected


def test_training_route_requires_a_configured_key(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    with mock.patch.object(model_catalog, "fetch_model_catalog") as fetch:
        assert model_catalog.resolve_training_openrouter_slug("Qwen/Qwen3.5-9B") is None
    fetch.assert_not_called()


def test_training_route_does_not_guess_when_catalog_is_unavailable():
    with mock.patch.object(model_catalog, "fetch_model_catalog", return_value=([], False)):
        assert model_catalog.resolve_training_openrouter_slug("Qwen/Qwen3.5-9B") is None


class TestCachedTokenPricing:
    """Cache reads bill at the provider's cache rate, not as fresh input."""

    CATALOG = [
        {
            "id": "moonshotai/kimi-k2.5",
            "prompt_price": 0.45,
            "completion_price": 2.25,
            "cache_read_price": 0.07,
        },
        {"id": "vendor/no-cache-rate", "prompt_price": 1.0, "completion_price": 2.0},
    ]

    def _estimate(self, *args, **kwargs):
        from overbae.services import model_catalog

        with mock.patch.object(
            model_catalog, "fetch_model_catalog", return_value=(self.CATALOG, True)
        ):
            return model_catalog.estimate_cost(*args, **kwargs)

    def test_cached_share_is_cheaper_than_fresh_input(self):
        fresh = self._estimate("moonshotai/kimi-k2.5", 10_000, 0)
        cached = self._estimate("moonshotai/kimi-k2.5", 10_000, 0, cached_tokens=10_000)
        assert cached < fresh
        assert cached == pytest.approx(10_000 * 0.07 / 1_000_000)

    def test_only_the_cached_share_gets_the_cache_rate(self):
        cost = self._estimate("moonshotai/kimi-k2.5", 10_000, 100, cached_tokens=7_000)
        expected = (3_000 * 0.45 + 7_000 * 0.07 + 100 * 2.25) / 1_000_000
        assert cost == pytest.approx(expected)

    def test_cached_tokens_cannot_exceed_the_prompt(self):
        assert self._estimate("moonshotai/kimi-k2.5", 100, 0, cached_tokens=999) == (
            self._estimate("moonshotai/kimi-k2.5", 100, 0, cached_tokens=100)
        )

    def test_a_model_with_no_cache_rate_bills_cached_tokens_as_fresh(self):
        with_cache = self._estimate("vendor/no-cache-rate", 1_000, 0, cached_tokens=1_000)
        without = self._estimate("vendor/no-cache-rate", 1_000, 0)
        assert with_cache == without
