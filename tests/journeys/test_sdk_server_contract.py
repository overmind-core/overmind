from overbae.api import overmind_attrs
from overbae.models.traces import USAGE_ATTR_KEYS
from overmind import attrs


def _constants(module) -> dict[str, str]:
    return {k: v for k, v in vars(module).items() if k.isupper() and isinstance(v, str)}


# The SDK's CONVERSATION_ID is a context-variable key; it emits server CONVERSATION_ID.
CONTEXT_ONLY = {"CONVERSATION_ID"}


def test_every_attribute_the_sdk_and_server_both_name_has_one_value():
    sdk, server = _constants(attrs), _constants(overmind_attrs)
    shared = (sdk.keys() & server.keys()) - CONTEXT_ONLY
    assert len(shared) >= 20
    assert {k: (sdk[k], server[k]) for k in shared if sdk[k] != server[k]} == {}


def test_the_usage_keys_the_sdk_emits_are_the_ones_ingest_rolls_up():
    emitted = {
        attrs.LLM_MODEL,
        attrs.LLM_PROMPT_TOKENS,
        attrs.LLM_COMPLETION_TOKENS,
        attrs.LLM_TOTAL_TOKENS,
        attrs.LLM_COST,
        attrs.OTEL_LLM_REQUEST_MODEL,
        attrs.OTEL_LLM_USAGE_TOTAL_TOKENS,
    }
    assert emitted - set(USAGE_ATTR_KEYS) == set()
