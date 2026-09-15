"""OpenAI-wire tool rows must survive the reshape that precedes apply_chat_template."""

from __future__ import annotations

import sys
from pathlib import Path

# sft_assets is uploaded to Modal as a flat directory, so its modules import each
# other by bare name and only resolve with the directory itself on sys.path.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "overbae" / "services" / "sft_assets"))

from pretok import normalize_openai_wire  # noqa: E402

CALL = {
    "id": "call_1",
    "type": "function",
    "function": {
        "name": "send_email",
        "arguments": '{"recipient": "boss@company.com", "subject": "Agenda"}',
    },
}


def _assistant_turn() -> dict:
    return {"role": "assistant", "content": None, "tool_calls": [CALL]}


def test_null_content_becomes_empty_string():
    """Qwen3 templates evaluate `'</think>' in message.content` and raise on None."""
    out = normalize_openai_wire([_assistant_turn()])
    assert out[0]["content"] == ""


def test_json_string_arguments_become_a_mapping():
    """Templates index arguments as a mapping; Qwen3.5 drops every parameter otherwise."""
    out = normalize_openai_wire([_assistant_turn()])
    assert out[0]["tool_calls"][0]["function"]["arguments"] == {
        "recipient": "boss@company.com",
        "subject": "Agenda",
    }


def test_arguments_parsed_on_flattened_calls():
    call = {"name": "send_email", "arguments": '{"recipient": "a@b.com"}'}
    out = normalize_openai_wire([{"role": "assistant", "content": None, "tool_calls": [call]}])
    assert out[0]["tool_calls"][0]["arguments"] == {"recipient": "a@b.com"}


def test_mapping_arguments_pass_through():
    call = {"function": {"name": "f", "arguments": {"a": 1}}}
    out = normalize_openai_wire([{"role": "assistant", "content": "", "tool_calls": [call]}])
    assert out[0]["tool_calls"][0]["function"]["arguments"] == {"a": 1}


def test_unparseable_arguments_are_left_alone():
    call = {"function": {"name": "f", "arguments": "not json"}}
    out = normalize_openai_wire([{"role": "assistant", "content": "", "tool_calls": [call]}])
    assert out[0]["tool_calls"][0]["function"]["arguments"] == "not json"


def test_non_object_json_arguments_are_left_alone():
    """A bare JSON scalar is not a kwargs mapping — templates would mis-render it."""
    call = {"function": {"name": "f", "arguments": '"hello"'}}
    out = normalize_openai_wire([{"role": "assistant", "content": "", "tool_calls": [call]}])
    assert out[0]["tool_calls"][0]["function"]["arguments"] == '"hello"'


def test_input_messages_are_not_mutated():
    messages = [_assistant_turn()]
    normalize_openai_wire(messages)
    assert messages[0]["content"] is None
    assert isinstance(messages[0]["tool_calls"][0]["function"]["arguments"], str)


def test_plain_turns_are_untouched():
    messages = [{"role": "user", "content": "hi"}, {"role": "tool", "content": "{}"}]
    assert normalize_openai_wire(messages) == messages
