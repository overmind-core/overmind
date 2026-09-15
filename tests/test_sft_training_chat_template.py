from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "overbae" / "services" / "sft_assets"))

from training_chat_template import (  # noqa: E402
    KNOWN_TEMPLATE_PATCHES,
    patch_known_training_template,
)

_ASSETS = Path(__file__).resolve().parents[1] / "overbae" / "services" / "sft_assets"


class _Tok:
    def __init__(self, template: str):
        self.chat_template = template


def test_every_patch_pair_exists_and_training_has_markers() -> None:
    for base_rel, training_rel in KNOWN_TEMPLATE_PATCHES:
        base = (_ASSETS / base_rel).read_text(encoding="utf-8")
        training = (_ASSETS / training_rel).read_text(encoding="utf-8")
        assert "{%- generation" not in base
        assert "{% generation %}" not in base
        assert "{%- generation" in training or "{% generation %}" in training
        assert "{%- endgeneration" in training or "{% endgeneration" in training


def test_patch_swaps_known_llama32_template() -> None:
    live = (_ASSETS / "llama_templates/llama3_2.jinja").read_text(encoding="utf-8")
    tok = _Tok(live)
    assert patch_known_training_template(tok) is True
    expected = (_ASSETS / "llama_templates/llama3_2_training.jinja").read_text(encoding="utf-8")
    assert tok.chat_template == expected


def test_patch_swaps_unsloth_gemma4_e2b() -> None:
    live = (_ASSETS / "gemma_templates/gemma4_e2b.jinja").read_text(encoding="utf-8")
    tok = _Tok(live)
    assert patch_known_training_template(tok) is True
    assert "{%- generation -%}" in tok.chat_template


def test_patch_swaps_unsloth_qwen38() -> None:
    live = (_ASSETS / "qwen_templates/qwen38_unsloth.jinja").read_text(encoding="utf-8")
    tok = _Tok(live)
    assert patch_known_training_template(tok) is True
    assert "{%- generation %}" in tok.chat_template
    expected = (_ASSETS / "qwen_templates/qwen38_unsloth_training.jinja").read_text(
        encoding="utf-8"
    )
    assert tok.chat_template == expected


def _generation_env():
    from jinja2 import nodes
    from jinja2.ext import Extension
    from jinja2.sandbox import ImmutableSandboxedEnvironment

    class _Generation(Extension):
        tags = {"generation"}

        def parse(self, parser):
            lineno = next(parser.stream).lineno
            body = parser.parse_statements(["name:endgeneration"], drop_needle=True)
            return nodes.Scope(body).set_lineno(lineno)

    def _raise(msg):
        raise ValueError(msg)

    env = ImmutableSandboxedEnvironment(
        extensions=[_Generation],
        trim_blocks=True,
        lstrip_blocks=True,
    )
    env.globals["raise_exception"] = _raise
    return env


def test_every_training_template_compiles() -> None:
    env = _generation_env()
    for _base_rel, training_rel in KNOWN_TEMPLATE_PATCHES:
        src = (_ASSETS / training_rel).read_text(encoding="utf-8")
        env.from_string(src)


def test_qwen38_training_template_compiles_and_matches_base() -> None:
    env = _generation_env()
    base = (_ASSETS / "qwen_templates/qwen38_unsloth.jinja").read_text(encoding="utf-8")
    training = (_ASSETS / "qwen_templates/qwen38_unsloth_training.jinja").read_text(
        encoding="utf-8"
    )
    messages = [
        {"role": "system", "content": "You are a helpful assistant."},
        {"role": "user", "content": "What is 2+2?"},
        {"role": "assistant", "content": "4"},
    ]
    kwargs = {"messages": messages, "tools": None, "add_generation_prompt": False}
    assert env.from_string(base).render(**kwargs) == env.from_string(training).render(**kwargs)


def test_gemma4_e2b_training_matches_base() -> None:
    env = _generation_env()
    base = (_ASSETS / "gemma_templates/gemma4_e2b.jinja").read_text(encoding="utf-8")
    training = (_ASSETS / "gemma_templates/gemma4_e2b_training.jinja").read_text(encoding="utf-8")
    messages = [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "hello"},
    ]
    kwargs = {
        "messages": messages,
        "bos_token": "<bos>",
        "tools": None,
        "add_generation_prompt": False,
        "enable_thinking": False,
        "preserve_thinking": False,
    }
    assert env.from_string(base).render(**kwargs) == env.from_string(training).render(**kwargs)


def test_unknown_template_is_left_alone() -> None:
    tok = _Tok("{{ messages }}")
    assert patch_known_training_template(tok) is False
    assert tok.chat_template == "{{ messages }}"
