from __future__ import annotations

from datetime import UTC, datetime

import pytest
from factories import auth_client, make_user
from rest_framework.test import APIClient

from overbae.core.llms import call_llm
from overbae.services import model_catalog

pytestmark = pytest.mark.django_db

CATALOG_URL = "/api/models/catalog/"


@pytest.fixture
def upstream(fake_llm):
    fake_llm.catalog_payload = _upstream_payload()["data"]
    return fake_llm


def _is_catalog(request) -> bool:
    return request.url.rstrip("/").endswith("/models")


def _upstream_payload() -> dict:
    return {
        "data": [
            {
                "id": "mistralai/mistral-large",
                "hugging_face_id": "mistralai/Mistral-Large-Instruct-2411",
                "name": "Mistral: Mistral Large",
                "context_length": 128000,
                "top_provider": {"max_completion_tokens": 16000},
                "supported_parameters": ["tools", "response_format"],
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


class TestModelCatalogEndpoint:
    def test_returns_trimmed_models(self, upstream):
        res = auth_client(make_user()).get(CATALOG_URL)

        assert res.status_code == 200
        body = res.json()
        assert body["upstream_available"] is True
        assert len(upstream.catalog_reads) == 1

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
        assert mistral["prompt_price"] == pytest.approx(2.0)
        assert mistral["completion_price"] == pytest.approx(6.0)
        assert mistral["curated"] is False

        gpt = models[3]
        assert gpt["curated"] is True
        assert gpt["prompt_price"] == pytest.approx(0.25)

    def test_upstream_failure_returns_empty_list_not_500(self, upstream):
        upstream.fail(_is_catalog, 503)
        res = auth_client(make_user()).get(CATALOG_URL)

        assert res.status_code == 200
        body = res.json()
        assert body["models"] == [] and body["upstream_available"] is False
        assert body["defaults"]["judge_model"] == body["defaults"]["judge_models"][0]

    def test_catalog_is_cached(self, upstream):
        client = auth_client(make_user())
        first = client.get(CATALOG_URL)
        second = client.get(CATALOG_URL)

        assert first.status_code == second.status_code == 200
        assert len(upstream.catalog_reads) == 1
        assert first.json() == second.json()

    def test_cached_catalog_retains_output_limits_schema_support_and_checkpoint(self, upstream):
        models, available = model_catalog.fetch_model_catalog()
        assert model_catalog.fetch_model_catalog() == (models, available)
        assert available
        assert len(upstream.catalog_reads) == 1
        mistral = next(row for row in models if row["id"] == "mistralai/mistral-large")
        assert mistral["max_completion_tokens"] == 16000
        assert mistral["supported_parameters"] == ["tools", "response_format"]
        assert mistral["hugging_face_id"] == "mistralai/Mistral-Large-Instruct-2411"

    def test_failure_is_cached_briefly_before_recovery(self, upstream, time_machine):
        time_machine.move_to(datetime.now(UTC), tick=False)
        reads = []
        upstream.fail(lambda r: _is_catalog(r) and reads.append(r) is None and len(reads) == 1)
        client = auth_client(make_user())

        failed = client.get(CATALOG_URL)
        unavailable = client.get(CATALOG_URL)
        time_machine.shift(61)
        recovered = client.get(CATALOG_URL)

        assert failed.json()["upstream_available"] is False
        assert unavailable.json()["upstream_available"] is False
        assert recovered.json()["upstream_available"] is True
        assert len(reads) == 2

    def test_sends_api_key_header_when_present(self, upstream, monkeypatch):
        monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
        auth_client(make_user()).get(CATALOG_URL)
        assert upstream.catalog_reads[0]["authorization"] == "Bearer sk-or-test"

    def test_requires_authentication(self):
        res = APIClient().get(CATALOG_URL)
        assert res.status_code == 401


class TestCatalogSlugRouting:
    def test_slash_slug_routes_through_openrouter(self, fake_llm):
        fake_llm.on(lambda r: True, "ok")
        content, _ = call_llm("hi", model="mistralai/mistral-large")
        assert content == "ok"
        assert fake_llm.requests[-1].model == "mistralai/mistral-large"

    def test_bare_unknown_model_still_rejected(self):
        with pytest.raises(RuntimeError, match="Unsupported model"):
            call_llm("hi", model="totally-unknown-model")


TRAINING_ENTRIES = [
    {"id": "qwen/qwen3.5-9b:free", "hugging_face_id": "Qwen/Qwen3.5-9B"},
    {"id": "qwen/qwen3.5-9b", "hugging_face_id": "Qwen/Qwen3.5-9B"},
    {
        "id": "meta-llama/llama-3.1-8b-instruct",
        "hugging_face_id": "meta-llama/Meta-Llama-3.1-8B-Instruct",
    },
    {"id": "mistralai/mistral-large", "hugging_face_id": "mistralai/Mistral-Large-Instruct-2411"},
]


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
def test_training_route_requires_an_exact_available_model(fake_llm, model, expected):
    fake_llm.catalog_payload = TRAINING_ENTRIES
    assert model_catalog.resolve_training_openrouter_slug(model) == expected


def test_training_route_requires_a_configured_key(fake_llm, monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    fake_llm.catalog_payload = TRAINING_ENTRIES
    assert model_catalog.resolve_training_openrouter_slug("Qwen/Qwen3.5-9B") is None
    assert fake_llm.catalog_reads == []


def test_training_route_does_not_guess_when_catalog_is_unavailable(fake_llm):
    fake_llm.fail(_is_catalog, 503)
    assert model_catalog.resolve_training_openrouter_slug("Qwen/Qwen3.5-9B") is None


class TestCachedTokenPricing:
    """Cache reads bill at the provider's cache rate, not as fresh input."""

    @pytest.fixture(autouse=True)
    def _priced(self, fake_llm):
        fake_llm.prices["moonshotai/kimi-k2.5"] = {
            "prompt": "0.00000045",
            "completion": "0.00000225",
            "input_cache_read": "0.00000007",
        }
        fake_llm.prices["vendor/no-cache-rate"] = {"prompt": "0.000001", "completion": "0.000002"}

    def test_cached_share_is_cheaper_than_fresh_input(self):
        fresh = model_catalog.estimate_cost("moonshotai/kimi-k2.5", 10_000, 0)
        cached = model_catalog.estimate_cost(
            "moonshotai/kimi-k2.5", 10_000, 0, cached_tokens=10_000
        )
        assert cached < fresh
        assert cached == pytest.approx(10_000 * 0.07 / 1_000_000)

    def test_only_the_cached_share_gets_the_cache_rate(self):
        cost = model_catalog.estimate_cost("moonshotai/kimi-k2.5", 10_000, 100, cached_tokens=7_000)
        expected = (3_000 * 0.45 + 7_000 * 0.07 + 100 * 2.25) / 1_000_000
        assert cost == pytest.approx(expected)

    def test_cached_tokens_cannot_exceed_the_prompt(self):
        assert model_catalog.estimate_cost(
            "moonshotai/kimi-k2.5", 100, 0, cached_tokens=999
        ) == model_catalog.estimate_cost("moonshotai/kimi-k2.5", 100, 0, cached_tokens=100)

    def test_a_model_with_no_cache_rate_bills_cached_tokens_as_fresh(self):
        with_cache = model_catalog.estimate_cost(
            "vendor/no-cache-rate", 1_000, 0, cached_tokens=1_000
        )
        without = model_catalog.estimate_cost("vendor/no-cache-rate", 1_000, 0)
        assert with_cache == without
