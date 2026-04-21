"""Shadow-execution bootstrap injected into the agent's subprocess.

This module generates a small Python snippet that, when prepended to the
agent wrapper, transparently intercepts external-world calls and redirects
them through an OverClaw cassette.  It lets us *run the user's code* — with
their real imports, control flow, and LLM calls — while sandboxing the parts
that make optimisation unreliable (browser, live HTTP, subprocesses, long
sleeps).

Three modes of operation per intercepted call:

1. **Replay from cassette** — if a matching request was recorded on a prior
   successful run, return the recorded result.
2. **Simulate** — for calls without a cassette hit, return a safe canned
   stub (empty string, empty dict, or a short "SIMULATED" string) so the
   rest of the agent can continue.
3. **Passthrough** — LLM calls (``litellm``, ``openai`` SDK) are always
   passed through to the real model.  Optimisation is fundamentally about
   prompts, so real LLM signal is what makes shadow mode useful in the first
   place.  LLM responses are recorded into the cassette on the way back so
   subsequent replays are deterministic.

The snippet is deliberately self-contained: it does not import any OverClaw
modules beyond :mod:`overclaw.optimize.cassette` (which is stdlib-only) and
:mod:`overclaw.optimize.provenance`.  It runs *inside the child subprocess*
where the user's environment is in charge of imports — so any extra
dependency risks breaking shadow mode on a working agent.

The bootstrap writes provenance tags for every intercepted call to a
sidecar file ``OVERCLAW_PROVENANCE_FILE`` (one JSON object per line).  The
main OverClaw process reads this file alongside the trace file to decorate
``ParsedTrace`` with per-call sources.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path


# The bootstrap is the *string* we prepend to ``_PYTHON_WRAPPER``.  It must
# be valid Python all on its own (no ``.format`` placeholders — those would
# confuse f-string semantics).  Any configuration flows in through env vars:
#
#   OVERCLAW_CASSETTE_FILE       path to the cassette JSONL
#   OVERCLAW_PROVENANCE_FILE     path to sidecar JSONL of source tags
#   OVERCLAW_SHADOW_MODE         "1" to enable interception
#   OVERCLAW_SIMULATE_BROWSER    "1" to stub browser_use / playwright
#   OVERCLAW_SIMULATE_NETWORK    "1" to stub requests / httpx / urllib


_BOOTSTRAP = r"""
# -- OverClaw shadow bootstrap (auto-generated; do not edit) -----------------
import json as _ocl_json
import os as _ocl_os
import sys as _ocl_sys
import time as _ocl_time
import hashlib as _ocl_hashlib

_ocl_sys.argv = [_ocl_sys.argv[0] if _ocl_sys.argv else "_overclaw_agent"]

_ocl_cassette_path = _ocl_os.environ.get("OVERCLAW_CASSETTE_FILE")
_ocl_prov_path = _ocl_os.environ.get("OVERCLAW_PROVENANCE_FILE")
_ocl_shadow = _ocl_os.environ.get("OVERCLAW_SHADOW_MODE") == "1"
_ocl_sim_browser = _ocl_os.environ.get("OVERCLAW_SIMULATE_BROWSER") == "1"
_ocl_sim_network = _ocl_os.environ.get("OVERCLAW_SIMULATE_NETWORK") == "1"


def _ocl_canonical(obj):
    try:
        return _ocl_json.dumps(obj, sort_keys=True, default=repr, separators=(",", ":"))
    except Exception:
        return repr(obj)


def _ocl_key(kind, identifier, payload):
    blob = "|".join([kind, identifier, _ocl_canonical(payload)])
    return _ocl_hashlib.sha256(blob.encode("utf-8")).hexdigest()


_OCL_CASSETTE_INDEX = {}


def _ocl_load_cassette():
    if not _ocl_cassette_path:
        return
    try:
        with open(_ocl_cassette_path, "r", encoding="utf-8") as _fh:
            for _line in _fh:
                _line = _line.strip()
                if not _line:
                    continue
                try:
                    _entry = _ocl_json.loads(_line)
                except Exception:
                    continue
                _k = _entry.get("key")
                if _k:
                    _OCL_CASSETTE_INDEX[_k] = _entry
    except OSError:
        pass


def _ocl_replay(kind, identifier, payload):
    return _OCL_CASSETTE_INDEX.get(_ocl_key(kind, identifier, payload))


def _ocl_record(kind, identifier, payload, result, metadata=None):
    if not _ocl_cassette_path:
        return
    try:
        _entry = {
            "kind": kind,
            "identifier": identifier,
            "key": _ocl_key(kind, identifier, payload),
            "payload": payload,
            "result": result,
            "metadata": metadata or {},
            "version": 1,
        }
        _dir = _ocl_os.path.dirname(_ocl_cassette_path)
        if _dir:
            _ocl_os.makedirs(_dir, exist_ok=True)
        with open(_ocl_cassette_path, "a", encoding="utf-8") as _fh:
            _fh.write(_ocl_json.dumps(_entry, default=repr) + "\n")
        _OCL_CASSETTE_INDEX[_entry["key"]] = _entry
    except Exception:
        pass


def _ocl_tag(name, source, reason=""):
    if not _ocl_prov_path:
        return
    try:
        _dir = _ocl_os.path.dirname(_ocl_prov_path)
        if _dir:
            _ocl_os.makedirs(_dir, exist_ok=True)
        _entry = {
            "name": name,
            "source": source,
            "reason": reason,
            "ts": _ocl_time.time(),
        }
        with open(_ocl_prov_path, "a", encoding="utf-8") as _fh:
            _fh.write(_ocl_json.dumps(_entry, default=repr) + "\n")
    except Exception:
        pass


_ocl_load_cassette()


# Dual-access response box used by the LLM intercept.  Defined at module
# scope (not inside a function) so libraries like DSPy that cloudpickle the
# response into a disk cache can round-trip the class.  Subclasses dict so
# ``results["choices"]`` and ``results.choices`` both work — litellm's real
# ``ModelResponse`` offers the same duality.
class _OverclawBox(dict):
    def __getattr__(self, name):
        try:
            return self[name]
        except KeyError as _k:
            raise AttributeError(name) from _k
    def __setattr__(self, name, value):
        self[name] = value
    def __delattr__(self, name):
        try:
            del self[name]
        except KeyError as _k:
            raise AttributeError(name) from _k


def _overclaw_box_response(raw, model):
    choices_data = raw.get("choices") or [
        {"message": {"content": "", "tool_calls": None}, "finish_reason": "stop"}
    ]
    choices = []
    for _c in choices_data:
        _msg_raw = _c.get("message", {}) if isinstance(_c, dict) else {}
        msg = _OverclawBox(
            content=_msg_raw.get("content", ""),
            role=_msg_raw.get("role", "assistant"),
            tool_calls=_msg_raw.get("tool_calls"),
        )
        choices.append(
            _OverclawBox(
                message=msg,
                finish_reason=_c.get("finish_reason", "stop") if isinstance(_c, dict) else "stop",
                index=0,
            )
        )
    usage_raw = raw.get("usage") or {}
    usage = _OverclawBox(
        prompt_tokens=usage_raw.get("prompt_tokens", 0),
        completion_tokens=usage_raw.get("completion_tokens", 0),
        total_tokens=usage_raw.get("total_tokens", 0),
    )
    return _OverclawBox(
        choices=choices,
        usage=usage,
        model=raw.get("model", str(model)),
        id="overclaw-shadow",
        object="chat.completion",
        created=0,
    )


# ---------------------------------------------------------------------------
# LLM interception — cassette-record real completions; replay when available.
# Prompt changes naturally invalidate the cassette key, so prompt optimisation
# still hits the real model.  This is the one interception that gives us real
# signal in shadow mode.
# ---------------------------------------------------------------------------

def _ocl_install_litellm_intercept():
    try:
        import litellm as _litellm
    except Exception:
        return

    _orig_completion = _litellm.completion

    def _wrapped_completion(*args, **kwargs):
        model = kwargs.get("model") or (args[0] if args else "unknown")
        messages = kwargs.get("messages") or (args[1] if len(args) > 1 else [])
        tools = kwargs.get("tools") or []
        payload = {"messages": messages, "tools": tools}
        cached = _ocl_replay("llm", str(model), payload)
        if cached is not None:
            _ocl_tag("llm:" + str(model), "cassette", "replayed from cassette")
            try:
                return _overclaw_box_response(cached.get("result") or {}, model)
            except Exception:
                pass
        try:
            resp = _orig_completion(*args, **kwargs)
            _ocl_tag("llm:" + str(model), "llm_real", "real model call")
            try:
                _serialised = {
                    "model": getattr(resp, "model", str(model)),
                    "choices": [
                        {
                            "message": {
                                "role": getattr(c.message, "role", "assistant"),
                                "content": getattr(c.message, "content", ""),
                                "tool_calls": [
                                    {
                                        "id": getattr(tc, "id", ""),
                                        "type": getattr(tc, "type", "function"),
                                        "function": {
                                            "name": getattr(tc.function, "name", ""),
                                            "arguments": getattr(tc.function, "arguments", ""),
                                        },
                                    }
                                    for tc in (getattr(c.message, "tool_calls", None) or [])
                                ] or None,
                            },
                            "finish_reason": getattr(c, "finish_reason", "stop"),
                        }
                        for c in getattr(resp, "choices", [])
                    ],
                    "usage": {
                        "prompt_tokens": getattr(getattr(resp, "usage", None), "prompt_tokens", 0),
                        "completion_tokens": getattr(getattr(resp, "usage", None), "completion_tokens", 0),
                        "total_tokens": getattr(getattr(resp, "usage", None), "total_tokens", 0),
                    },
                }
                _ocl_record("llm", str(model), payload, _serialised, metadata={"source": "live"})
            except Exception:
                pass
            return resp
        except Exception as _exc:
            # Graceful degradation: when the real LLM call fails and we
            # have no cassette hit, return a clearly-labelled placeholder
            # response so the agent can continue and we can still collect
            # structural signal (tool call sequence, output parsing, etc.).
            # The trace carries source="simulated" so the optimiser knows
            # this datapoint is low confidence.
            _ocl_tag(
                "llm:" + str(model),
                "simulated",
                "llm call failed ({}); returning placeholder".format(type(_exc).__name__),
            )
            # Heuristic: if the prompt suggests JSON output (contains the
            # literal word "JSON" or schema markers), return an empty JSON
            # object — lots of adapters (DSPy JSONAdapter, LangChain
            # StructuredOutputParser) expect valid JSON and will at least
            # parse an empty object without crashing.
            _joined = str(messages)[:4000]
            _wants_json = (
                "json" in _joined.lower()
                or "{{" in _joined
                or "[[ ## " in _joined
            )
            placeholder = (
                "{}"
                if _wants_json
                else (
                    "[OverClaw shadow] Placeholder LLM response - the live "
                    "call failed and no cassette entry matched. Re-run on "
                    "your own infrastructure to capture a real response."
                )
            )
            return _overclaw_box_response(
                {
                    "choices": [
                        {
                            "message": {
                                "content": placeholder,
                                "role": "assistant",
                                "tool_calls": None,
                            },
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": {
                        "prompt_tokens": 0,
                        "completion_tokens": 0,
                        "total_tokens": 0,
                    },
                },
                model,
            )

    _litellm.completion = _wrapped_completion


_ocl_install_litellm_intercept()


# ---------------------------------------------------------------------------
# Network interception — only active when OVERCLAW_SIMULATE_NETWORK=1.
# ---------------------------------------------------------------------------

if _ocl_shadow and _ocl_sim_network:
    def _ocl_wrap_requests():
        try:
            import requests as _requests
        except Exception:
            return
        _orig_request = _requests.api.request

        def _patched_request(method, url, **kwargs):
            payload = {"method": method, "url": url, "kwargs": {k: v for k, v in kwargs.items() if k != "timeout"}}
            cached = _ocl_replay("http", str(url), payload)
            if cached is not None:
                from types import SimpleNamespace as _NS
                _ocl_tag("http:" + str(url), "cassette", "replayed http call")
                raw = cached.get("result") or {}
                resp = _NS(
                    status_code=raw.get("status_code", 200),
                    text=raw.get("text", ""),
                    content=(raw.get("text") or "").encode("utf-8"),
                    headers=raw.get("headers", {}),
                    json=lambda r=raw: r.get("json") or {},
                    ok=raw.get("status_code", 200) < 400,
                )
                return resp
            _ocl_tag("http:" + str(url), "simulated", "no cassette; returning empty response")
            from types import SimpleNamespace as _NS
            return _NS(
                status_code=200,
                text="",
                content=b"",
                headers={},
                json=lambda: {},
                ok=True,
            )

        _requests.api.request = _patched_request
        _requests.request = _patched_request
        for _m in ("get", "post", "put", "patch", "delete", "head", "options"):
            setattr(_requests, _m, lambda *_a, __m=_m, **_kw: _patched_request(__m.upper(), *_a, **_kw))

    _ocl_wrap_requests()


# ---------------------------------------------------------------------------
# Browser interception — only active when OVERCLAW_SIMULATE_BROWSER=1.
# ``browser_use.Agent(...).run()`` becomes a no-op returning an empty
# history.  ``playwright.sync_api.sync_playwright`` / async counterpart raise
# a clear message so optimisation can still proceed with a simulated output.
# ---------------------------------------------------------------------------

if _ocl_shadow and _ocl_sim_browser:
    def _ocl_wrap_browser_use():
        try:
            import browser_use as _bu
        except Exception:
            return
        _orig_agent = getattr(_bu, "Agent", None)
        if _orig_agent is None:
            return

        class _SimulatedHistory:
            def final_result(self):
                return "[OverClaw shadow] browser execution was simulated; no live DOM interaction."

            def is_done(self):
                return True

        class _SimulatedAgent:
            def __init__(self, *args, **kwargs):
                self._task = kwargs.get("task") or (args[0] if args else "")
                _ocl_tag("browser_use.Agent", "simulated", "simulated browser agent")

            def run(self, *_a, **_kw):
                return _SimulatedHistory()

            async def run_async(self, *_a, **_kw):
                return _SimulatedHistory()

            def __getattr__(self, _name):
                def _noop(*_a, **_kw):
                    return None
                return _noop

        _bu.Agent = _SimulatedAgent

    _ocl_wrap_browser_use()


# -- end shadow bootstrap ----------------------------------------------------
"""


@dataclass(frozen=True)
class ShadowConfig:
    """Configuration for shadow execution of a single subprocess run."""

    enabled: bool = False
    cassette_path: str | None = None
    provenance_path: str | None = None
    simulate_browser: bool = True
    simulate_network: bool = True

    def env(self) -> dict[str, str]:
        """Environment variables passed into the subprocess."""
        env: dict[str, str] = {}
        if self.enabled:
            env["OVERCLAW_SHADOW_MODE"] = "1"
        if self.cassette_path:
            env["OVERCLAW_CASSETTE_FILE"] = str(self.cassette_path)
        if self.provenance_path:
            env["OVERCLAW_PROVENANCE_FILE"] = str(self.provenance_path)
        if self.simulate_browser:
            env["OVERCLAW_SIMULATE_BROWSER"] = "1"
        if self.simulate_network:
            env["OVERCLAW_SIMULATE_NETWORK"] = "1"
        return env


def bootstrap_source(config: ShadowConfig | None = None) -> str:
    """Return the shadow bootstrap Python source.

    The *config* is serialised into environment variables by the caller; the
    bootstrap reads those vars at subprocess start.  The returned source is
    prepended (verbatim) to the agent wrapper.

    Three modes, selected automatically:

    * **argv-guard only** — when *config* is ``None`` or has neither
      shadow-mode nor a cassette path.  Returns the minimal snippet that
      neutralises ``sys.argv`` (fixes ~90% of module-level side-effect
      crashes).
    * **record-only** — when a cassette path is set but ``enabled`` is
      ``False``.  The full bootstrap loads, but HTTP / browser intercepts
      stay dormant (gated on ``OVERCLAW_SHADOW_MODE=1``).  Real LLM calls
      are captured transparently so future shadow runs can replay them.
    * **full shadow** — when ``enabled`` is ``True``.  Adds HTTP / browser
      intercepts on top of LLM capture.
    """
    if config is None:
        return _SYS_ARGV_GUARD
    if not config.enabled and not config.cassette_path:
        return _SYS_ARGV_GUARD
    return _BOOTSTRAP


_SYS_ARGV_GUARD = (
    "import sys as _ocl_sys\n"
    "_ocl_sys.argv = [_ocl_sys.argv[0] if _ocl_sys.argv else '_overclaw_agent']\n"
)


def read_provenance_file(path: str | os.PathLike) -> list[dict]:
    """Read a sidecar provenance JSONL file into a list of dicts.

    Missing or malformed entries are skipped silently — the main trace file
    is the source of truth for scoring; this file is a supplement.
    """
    p = Path(path)
    if not p.exists():
        return []
    try:
        text = p.read_text(encoding="utf-8")
    except OSError:
        return []
    out: list[dict] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except (json.JSONDecodeError, ValueError):
            continue
    return out
