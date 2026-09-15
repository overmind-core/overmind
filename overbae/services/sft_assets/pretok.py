"""Pretok assistant-only labels for Unsloth SFT.

Unsloth's patched SFTTrainer rejects conversational `messages` without
`formatting_func`, `train_on_responses_only` leaks tool/ipython spans on many
families, and Unsloth pins trl<=0.24 (no `assistant_only_loss`), so labels are
built here instead. Feeding Dataset[{input_ids, attention_mask, labels}] makes
SFTTrainer skip _prepare_dataset / formatting_func entirely.

Two paths, tried in order (run.sh refers to them by these names):
  Path A — TRL get_training_chat_template + return_assistant_tokens_mask
  Path B — native render + multi-role header masker (+ tool-result force-masks)
Path A needs TRL_SRC pointing at a trl>=1.4 checkout that ships
trl/chat_template_utils.py — the Unsloth image's pinned trl does not have it.
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from typing import Any

from catalog import family_key
from training_chat_template import patch_known_training_template

# Optional TRL>=1.4 training-template helpers.
_TRL_IMPORT_ERROR: Exception | None = None
get_training_chat_template = None  # type: ignore
has_generation_markers = None  # type: ignore


def _bootstrap_trl_src() -> None:
    global get_training_chat_template, has_generation_markers, _TRL_IMPORT_ERROR
    here = Path(__file__).resolve().parent
    candidates = [
        os.environ.get("TRL_SRC"),
        str(here / "trl_src"),
        "/tmp/trl-check",
    ]
    for cand in candidates:
        if not cand:
            continue
        if not (Path(cand) / "trl" / "chat_template_utils.py").exists():
            continue
        for k in list(sys.modules):
            if k == "trl" or k.startswith("trl."):
                del sys.modules[k]
        sys.path.insert(0, cand)
        break
    try:
        from trl.chat_template_utils import (  # type: ignore
            get_training_chat_template as _g,
        )
        from trl.chat_template_utils import (
            has_generation_markers as _h,
        )

        get_training_chat_template = _g
        has_generation_markers = _h
        _TRL_IMPORT_ERROR = None
    except Exception as e:  # pragma: no cover
        _TRL_IMPORT_ERROR = e


_bootstrap_trl_src()

from catalog import _ensure_modelfam  # noqa: E402

_ensure_modelfam()
from modal_shared.modelfam import all_force_mask_pairs, all_pretok_headers  # noqa: E402

_HEADER_RES = [re.compile(p) for p in all_pretok_headers()]

_ROLE_NORMALIZE = {
    "user": "user",
    "User": "user",
    "system": "system",
    "System": "system",
    "assistant": "assistant",
    "Assistant": "assistant",
    "model": "assistant",
    "tool": "tool",
    "ipython": "ipython",
    "developer": "system",
}


def _normalize_role(raw_role: str) -> str:
    if raw_role in _ROLE_NORMALIZE:
        return _ROLE_NORMALIZE[raw_role]
    if raw_role.startswith("functions."):
        return "tool"
    return raw_role.lower()


# Tool-result spans that sit inside assistant turns without a role header.
_FORCE_MASK_PAIRS = list(all_force_mask_pairs())


def ensure_tool_param_descriptions(tools: list[dict] | None) -> list[dict] | None:
    """gpt-oss Harmony Jinja requires param_spec.description on every property."""
    if not tools:
        return tools
    out = json.loads(json.dumps(tools))  # deep copy
    for t in out:
        fn = t.get("function") if isinstance(t, dict) else None
        target = fn if isinstance(fn, dict) else t
        if not isinstance(target, dict):
            continue
        if not target.get("description"):
            target["description"] = target.get("name") or "tool"
        params = target.get("parameters") or {}
        props = params.get("properties") or {}
        for pname, pspec in props.items():
            if isinstance(pspec, dict) and not pspec.get("description"):
                pspec["description"] = str(pname)
    return out


def _parse_call_arguments(call: dict) -> dict:
    fn = call.get("function")
    target = fn if isinstance(fn, dict) else call
    args = target.get("arguments")
    if not isinstance(args, str):
        return call
    try:
        parsed = json.loads(args)
    except ValueError:
        return call
    if not isinstance(parsed, dict):
        return call
    if isinstance(fn, dict):
        return {**call, "function": {**fn, "arguments": parsed}}
    return {**call, "arguments": parsed}


def normalize_openai_wire(messages: list[dict]) -> list[dict]:
    """Reshape OpenAI wire format into what chat templates actually index.

    Two mismatches, both of which silently destroy tool rows:
    `content: null` on a tool-call-only assistant turn — Qwen3 templates evaluate
    `'</think>' in message.content` unguarded and raise; and `arguments` as a JSON
    string — templates index it as a mapping, so Qwen3.5 emits an empty
    `<function=...>` block and newer HF templates raise on `|items`.
    """
    out: list[dict] = []
    for m in messages:
        if m.get("content") is None:
            m = {**m, "content": ""}
        calls = m.get("tool_calls")
        if calls:
            m = {**m, "tool_calls": [_parse_call_arguments(c) for c in calls]}
        out.append(m)
    return out


def normalize_messages_for_model(
    model_id: str,
    messages: list[dict],
    tools: list[dict] | None = None,
) -> tuple[list[dict], list[dict] | None]:
    """Family-specific message/tool shaping before apply_chat_template."""
    tools = ensure_tool_param_descriptions(tools)
    messages = normalize_openai_wire(messages)
    fam = family_key(model_id)

    if fam == "phi4" and tools is not None:
        # The Phi-4 template ignores tool_calls and instead expects
        # system.tools = JSON string, assistant.content = JSON {name, arguments},
        # and a tool role whose content is the result JSON.
        msgs: list[dict] = []
        tools_json = json.dumps(tools)
        has_system = False
        for m in messages:
            m = dict(m)
            role = m.get("role")
            if role == "system":
                has_system = True
                m["tools"] = tools_json
                msgs.append(m)
                continue
            if role == "assistant" and m.get("tool_calls"):
                tc = m["tool_calls"][0]
                fn = tc.get("function") or tc
                args = fn.get("arguments", {})
                msgs.append(
                    {
                        "role": "assistant",
                        "content": json.dumps({"name": fn.get("name"), "arguments": args}),
                    }
                )
                continue
            if role == "tool":
                msgs.append({"role": "tool", "content": m.get("content", "")})
                continue
            msgs.append({k: v for k, v in m.items() if k != "tool_calls"})
        if not has_system:
            msgs.insert(
                0,
                {"role": "system", "content": "You are a helpful assistant.", "tools": tools_json},
            )
        # Phi embeds tools on the system message — don't also pass tools= kwarg.
        return msgs, None

    if fam in ("qwen_mm", "qwen35", "qwen38"):
        # The template's bare-string `content` branch runs everything through the
        # vision image loader; typed text parts take the branch that only
        # image-loads dict parts tagged "image"/"video".
        msgs = []
        for m in messages:
            content = m.get("content")
            if isinstance(content, str):
                m = {**m, "content": [{"type": "text", "text": content}]}
            msgs.append(m)
        return msgs, tools

    return messages, tools


def _unwrap_tokenizer(tok):
    """Unwrap the inner tokenizer when Unsloth loads a text-only model as a
    Processor (Gemma4, Qwen3.5/3.6). The outer Processor's __call__ is built for
    multimodal assembly and doesn't reliably forward `return_offsets_mapping`."""
    return getattr(tok, "tokenizer", tok)


def _token_seq(tok, s: str) -> list[int]:
    # `text=` MUST stay keyword: Unsloth patches some tokenizers' __call__ into a
    # processor signature `(images=None, text=None, ...)`, so a positional arg
    # binds to `images` and leaves `text=None`.
    return _unwrap_tokenizer(tok)(text=s, add_special_tokens=False).input_ids


def _find_subseq(hay: list[int], needle: list[int], start: int = 0) -> int:
    n = len(needle)
    if n == 0 or n > len(hay) - start:
        return -1
    for i in range(start, len(hay) - n + 1):
        if hay[i : i + n] == needle:
            return i
    return -1


def _headers_spans_in_text(text: str) -> list[tuple[str, int, int]]:
    """Chat-template role headers as (role, start_char, end_char) spans.

    Character offsets, because tokenizing a header alone can merge/split
    differently than it does in-context, so a token-subsequence search misses it.
    """
    found: list[tuple[str, int, int]] = []
    for cre in _HEADER_RES:
        for m in cre.finditer(text):
            if cre.pattern == r"\[INST\]":
                role = "user"
            elif cre.pattern == r"\[/INST\]":
                role = "assistant"
            else:
                role = _normalize_role(m.group(1))
            found.append((role, m.start(), m.end()))
    seen: set[tuple[int, int]] = set()
    out: list[tuple[str, int, int]] = []
    for role, start, end in sorted(found, key=lambda x: x[1]):
        if (start, end) in seen:
            continue
        seen.add((start, end))
        out.append((role, start, end))
    return out


def _char_to_token_idx(offsets: list[tuple[int, int]], char_pos: int) -> int:
    """First token index whose span starts at-or-after `char_pos`."""
    for i, (start, _end) in enumerate(offsets):
        if start >= char_pos:
            return i
    return len(offsets)


_OFFSET_DIAG_PRINTED = False


def multi_header_labels(tok, text: str) -> tuple[list[int], list[int], dict]:
    spans = _headers_spans_in_text(text)
    meta_headers = [{"role": r, "raw": text[s:e]} for r, s, e in spans]

    # Preferred: locate headers via the tokenizer's own offset mapping, which is
    # robust to a header tokenizing differently in isolation than in-context —
    # that silently dropped every row for Gemma 4's "<|turn>role\n" headers.
    try:
        enc = _unwrap_tokenizer(tok)(
            text=text, add_special_tokens=False, return_offsets_mapping=True
        )
        ids = enc.input_ids
        offsets = enc.offset_mapping
        if offsets and len(offsets) == len(ids):
            resp_spans = [(s, e) for role, s, e in spans if role == "assistant"]
            instr_spans = [(s, e) for role, s, e in spans if role != "assistant"]
            meta = {
                "headers": meta_headers,
                "n_resp": len(resp_spans),
                "n_instr": len(instr_spans),
                "strategy": "offset_mapping",
            }
            if not resp_spans:
                return ids, [-100] * len(ids), {**meta, "error": "no_response_header"}

            labels = [-100] * len(ids)
            events = [
                (_char_to_token_idx(offsets, s), "R", _char_to_token_idx(offsets, e))
                for s, e in resp_spans
            ]
            events += [
                (_char_to_token_idx(offsets, s), "I", _char_to_token_idx(offsets, e))
                for s, e in instr_spans
            ]
            events.sort(key=lambda x: (x[0], 0 if x[1] == "I" else 1))

            for start_tok, kind, end_tok in events:
                if kind != "R":
                    continue
                next_i = next((s2 for s2, k2, _ in events if s2 > start_tok and k2 == "I"), None)
                stop = next_i if next_i is not None else len(ids)
                for j in range(end_tok, stop):
                    labels[j] = ids[j]
            ids, labels = _apply_force_mask(tok, ids, labels, text)
            return ids, labels, meta
    except Exception as e:  # noqa: BLE001 — fall through to the legacy strategy below
        global _OFFSET_DIAG_PRINTED
        if not _OFFSET_DIAG_PRINTED:
            print(
                f"diag: offset_mapping tokenization failed, using legacy fallback: {e!r}",
                flush=True,
            )
            _OFFSET_DIAG_PRINTED = True

    return _multi_header_labels_legacy(tok, text, spans, meta_headers)


def _multi_header_labels_legacy(
    tok, text: str, spans: list[tuple[str, int, int]], meta_headers: list[dict]
) -> tuple[list[int], list[int], dict]:
    """Fallback for tokenizers without usable offset mappings.

    Retokenizes each header substring in isolation, so it can miss headers that
    tokenize differently in-context.
    """
    ids = _token_seq(tok, text)
    resp_seqs = [_token_seq(tok, text[s:e]) for role, s, e in spans if role == "assistant"]
    instr_seqs = [_token_seq(tok, text[s:e]) for role, s, e in spans if role != "assistant"]
    meta = {
        "headers": meta_headers,
        "n_resp": len(resp_seqs),
        "n_instr": len(instr_seqs),
        "strategy": "legacy_subsequence",
    }
    if not resp_seqs:
        return ids, [-100] * len(ids), {**meta, "error": "no_response_header"}

    labels = [-100] * len(ids)
    events: list[tuple[int, str, int]] = []
    for seq in resp_seqs:
        pos = 0
        while True:
            i = _find_subseq(ids, seq, pos)
            if i < 0:
                break
            events.append((i, "R", len(seq)))
            pos = i + max(len(seq), 1)
    for seq in instr_seqs:
        pos = 0
        while True:
            i = _find_subseq(ids, seq, pos)
            if i < 0:
                break
            events.append((i, "I", len(seq)))
            pos = i + max(len(seq), 1)
    events.sort(key=lambda x: (x[0], 0 if x[1] == "I" else 1))

    for pos, kind, length in events:
        if kind != "R":
            continue
        start = pos + length
        next_i = next((p2 for p2, k2, _ in events if p2 > pos and k2 == "I"), None)
        end = next_i if next_i is not None else len(ids)
        for j in range(start, end):
            labels[j] = ids[j]

    ids, labels = _apply_force_mask(tok, ids, labels, text)
    return ids, labels, meta


def _apply_force_mask(
    tok, ids: list[int], labels: list[int], text: str
) -> tuple[list[int], list[int]]:
    for start_s, end_s in _FORCE_MASK_PAIRS:
        if start_s not in text:
            continue
        start_seq = _token_seq(tok, start_s)
        end_seq = _token_seq(tok, end_s)
        pos = 0
        while True:
            i = _find_subseq(ids, start_seq, pos)
            if i < 0:
                break
            j = _find_subseq(ids, end_seq, i + len(start_seq))
            stop = (j + len(end_seq)) if j >= 0 else len(ids)
            for k in range(i, stop):
                labels[k] = -100
            pos = max(stop, i + 1)
    return ids, labels


_TRL_DIAG_PRINTED = False


def pretok_trl(
    tok, messages: list[dict], tools: list[dict] | None
) -> tuple[list[int], list[int], str] | None:
    global _TRL_DIAG_PRINTED
    if get_training_chat_template is None:
        return None
    patch_known_training_template(tok)
    try:
        tmpl = get_training_chat_template(tok)
    except Exception as e:
        if not _TRL_DIAG_PRINTED:
            # Expected for families TRL cannot auto-patch (Muse, some Harmony
            # templates) — pretok_row falls through to multi_header.
            print(
                f"diag: pretok_trl unsupported ({e!r}); using multi_header fallback",
                flush=True,
            )
            _TRL_DIAG_PRINTED = True
        return None
    kwargs: dict[str, Any] = {
        "conversation": messages,
        "tokenize": True,
        "return_dict": True,
        "return_assistant_tokens_mask": True,
        "add_generation_prompt": False,
    }
    if tmpl is not None:
        kwargs["chat_template"] = tmpl
        if has_generation_markers and not has_generation_markers(tmpl):
            return None
    elif (
        tok.chat_template
        and has_generation_markers
        and not has_generation_markers(tok.chat_template)
    ):
        return None
    if tools is not None:
        kwargs["tools"] = tools
    try:
        out = tok.apply_chat_template(**kwargs)
    except Exception as e:
        if not _TRL_DIAG_PRINTED:
            print(f"diag: pretok_trl apply_chat_template failed: {e!r}", flush=True)
            _TRL_DIAG_PRINTED = True
        return None
    ids = out["input_ids"]
    masks = out.get("assistant_masks")
    if masks is None:
        if not _TRL_DIAG_PRINTED:
            print("diag: pretok_trl assistant_masks is None", flush=True)
            _TRL_DIAG_PRINTED = True
        return None
    if ids and isinstance(ids[0], list):
        ids, masks = ids[0], masks[0]
    if 1 not in masks:
        return None
    labels = [tid if m == 1 else -100 for tid, m in zip(ids, masks, strict=False)]
    note = "trl_training_template" if tmpl is not None else "native_generation_markers"
    return ids, labels, note


_MULTI_DIAG_PRINTED = False


def pretok_multi(
    tok, messages: list[dict], tools: list[dict] | None
) -> tuple[list[int], list[int], str] | None:
    global _MULTI_DIAG_PRINTED
    kwargs: dict[str, Any] = {"tokenize": False, "add_generation_prompt": False}
    if tools is not None:
        kwargs["tools"] = tools
    try:
        text = tok.apply_chat_template(messages, **kwargs)
    except Exception as e:
        if not _MULTI_DIAG_PRINTED:
            print(f"diag: pretok_multi apply_chat_template failed: {e!r}", flush=True)
            _MULTI_DIAG_PRINTED = True
        return None
    ids, labels, meta = multi_header_labels(tok, text)
    if meta.get("error") or not any(lbl != -100 for lbl in labels):
        if not _MULTI_DIAG_PRINTED:
            print(f"diag: pretok_multi failed meta={meta!r} text={text[:500]!r}", flush=True)
            _MULTI_DIAG_PRINTED = True
        return None
    return ids, labels, "multi_header_fallback"


def pretok_row(
    tok,
    model_id: str,
    messages: list[dict],
    tools: list[dict] | None = None,
) -> dict[str, Any]:
    """Return one Unsloth-ready example: input_ids / attention_mask / labels + path."""
    messages, tools = normalize_messages_for_model(model_id, messages, tools)
    for fn in (pretok_trl, pretok_multi):
        got = fn(tok, messages, tools)
        if got is not None:
            ids, labels, path = got
            return {
                "input_ids": ids,
                "attention_mask": [1] * len(ids),
                "labels": labels,
                "path": path,
            }
    raise RuntimeError(f"pretok failed for {model_id} (trl_err={_TRL_IMPORT_ERROR})")
