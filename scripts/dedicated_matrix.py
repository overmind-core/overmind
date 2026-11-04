"""Dedicated inference for every catalog model, then one adapter serve.

Cold start is the wait until the dedicated tunnel is open. The warm numbers are
the next requests, after one short completion. Streaming and non-streaming use
the same prompt and a 128-token cap.

  PYTHONPATH=. uv run python scripts/dedicated_matrix.py
"""

from __future__ import annotations

import inspect
import json
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import httpx
import modal

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("POSTGRES_HOST", "127.0.0.1")
os.environ.setdefault("DJANGO_DEBUG", "True")
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "overbae.settings")

import django  # noqa: E402

django.setup()

from django.conf import settings  # noqa: E402

from modal_shared.serving.engine import engine_for  # noqa: E402
from modal_shared.serving.speculative import draft_for, mtp_for  # noqa: E402
from modal_shared.shared import rel_weights_path  # noqa: E402
from overbae.modal.gpu_selector import dedicated_gpu_names  # noqa: E402
from overbae.models import DeployedModel  # noqa: E402
from overbae.services.dedicated_runtime import shape_chat_payload  # noqa: E402

ENV = "overmind-dev"
SERVED = "matrix"
OUT = ROOT / "experiments" / "dedicated_matrix" / "results.jsonl"
PROMPT = (
    "Explain how a bicycle stays upright when the rider leans into a turn. "
    "Use plain language and several sentences."
)
MAX_TOKENS = 128
WRITE = threading.Lock()

WEATHER = {
    "type": "function",
    "function": {
        "name": "get_weather",
        "description": "Current weather for a city.",
        "parameters": {
            "type": "object",
            "properties": {"city": {"type": "string"}},
            "required": ["city"],
        },
    },
}
CLOCK = {
    "type": "function",
    "function": {
        "name": "get_time",
        "description": "Current local time in a city.",
        "parameters": {
            "type": "object",
            "properties": {"city": {"type": "string"}},
            "required": ["city"],
        },
    },
}


def speculative(repo: str) -> str:
    if mtp_for(repo):
        return "MTP"
    found = draft_for(repo)
    if not found:
        return "nothing"
    name, _tokens = found
    if "DFlash2" in name:
        return "DFlash2"
    return "DFlash"


def catalog() -> list[dict]:
    doc = json.loads((ROOT / "overbae" / "modal" / "models.json").read_text())
    grouped: dict[str, list[dict]] = {}
    order: list[str] = []
    for entry in doc["models"]:
        grouped.setdefault(entry["id"], []).append(entry)
        if entry["id"] not in order:
            order.append(entry["id"])
    rows = []
    for model_id in order:
        entries = grouped[model_id]
        baseten = next((e for e in entries if e.get("backend") == "baseten"), None)
        chosen = baseten or entries[0]
        rows.append(
            {
                "model": model_id,
                "entry": chosen,
                "disabled": all(e.get("disabled") for e in entries),
                "disabled_reason": next(
                    (e.get("disabled_reason") for e in entries if e.get("disabled_reason")),
                    None,
                ),
                "kind": chosen.get("type") or chosen.get("backend"),
                "tools": bool(chosen.get("supports_tool_calling")),
                "servable": bool(
                    baseten and not baseten.get("disabled") and baseten.get("type") == "chat"
                ),
            }
        )
    return rows


def pick_gpu(entry: dict) -> tuple[list[str], int] | None:
    requested = int((entry.get("inference") or {}).get("max_model_len") or 8192)
    for length in (requested, 4096, 2048, 1024):
        names = dedicated_gpu_names(entry, length, fp8=False)
        if names:
            return names, length
    return None


def record(row: dict) -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with WRITE, OUT.open("a") as handle:
        handle.write(json.dumps(row) + "\n")
        handle.flush()
    err = row.get("error")
    label = err.splitlines()[-1][:180] if err else "ok"
    print(
        f"[{row.get('kind')}] {row['model']} {label} "
        f"cold={row.get('cold_start_s')} ttft={row.get('warm_ttft_s')}",
        flush=True,
    )


def finished() -> set[tuple[str, str]]:
    if not OUT.exists():
        return set()
    done = set()
    for line in OUT.read_text().splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if row.get("error"):
            continue
        if row.get("kind") == "base" and row.get("servable") and "cold_start_s" not in row:
            continue
        done.add((row["model"], row.get("kind") or "base"))
    return done


def payload(
    model: str, messages: list, *, stream: bool, max_tokens: int, tools=None, parallel=None
):
    body = {
        "model": SERVED,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": 0,
        "stream": stream,
    }
    if tools is not None:
        body["tools"] = tools
    if parallel is not None:
        body["parallel_tool_calls"] = parallel
    if stream:
        body["stream_options"] = {"include_usage": True}
    shape_chat_payload(body, base_model_id=model)
    body["model"] = SERVED
    body["stream"] = stream
    return body


def _calls(message: dict) -> list[dict]:
    calls = message.get("tool_calls") or []
    out = []
    for call in calls:
        fn = call.get("function") or {}
        out.append(
            {
                "name": fn.get("name"),
                "arguments": fn.get("arguments"),
                "id": call.get("id"),
            }
        )
    return out


def complete(url: str, body: dict) -> dict:
    headers = {
        "content-type": "application/json",
        "authorization": f"Bearer {settings.INFERENCE_API_KEY}",
    }
    started = time.perf_counter()
    if not body.get("stream"):
        response = httpx.post(
            url.rstrip("/") + "/v1/chat/completions",
            json=body,
            headers=headers,
            timeout=600,
        )
        elapsed = time.perf_counter() - started
        data = response.json()
        if response.status_code >= 400:
            raise RuntimeError(f"HTTP {response.status_code}: {json.dumps(data)[:500]}")
        choice = (data.get("choices") or [{}])[0]
        message = choice.get("message") or {}
        usage = data.get("usage") or {}
        tokens = usage.get("completion_tokens")
        return {
            "latency_s": round(elapsed, 3),
            "tokens_per_s": round(tokens / elapsed, 2) if tokens else None,
            "completion_tokens": tokens,
            "finish_reason": choice.get("finish_reason"),
            "tool_calls": _calls(message),
            "content": message.get("content"),
        }
    ttft = None
    usage = None
    tool_bits: list[dict] = []
    with httpx.stream(
        "POST",
        url.rstrip("/") + "/v1/chat/completions",
        json=body,
        headers=headers,
        timeout=600,
    ) as response:
        if response.status_code >= 400:
            raw = response.read().decode()[:500]
            raise RuntimeError(f"HTTP {response.status_code}: {raw}")
        buffer = ""
        for chunk in response.iter_text():
            buffer += chunk
            while "\n" in buffer:
                line, buffer = buffer.split("\n", 1)
                line = line.strip()
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    continue
                try:
                    event = json.loads(data)
                except json.JSONDecodeError:
                    continue
                if event.get("usage"):
                    usage = event["usage"]
                choices = event.get("choices") or []
                if not choices:
                    continue
                delta = choices[0].get("delta") or {}
                content = delta.get("content") or ""
                if (content or delta.get("tool_calls")) and ttft is None:
                    ttft = time.perf_counter() - started
                for piece in delta.get("tool_calls") or []:
                    tool_bits.append(piece)
        elapsed = time.perf_counter() - started
    tokens = (usage or {}).get("completion_tokens")
    return {
        "latency_s": round(elapsed, 3),
        "ttft_s": round(ttft, 3) if ttft is not None else None,
        "tokens_per_s": round(tokens / elapsed, 2) if tokens else None,
        "completion_tokens": tokens,
        "tool_deltas": len(tool_bits),
    }


def tool_report(url: str, model: str) -> dict:
    tools = [WEATHER, CLOCK]

    def ask(messages, *, parallel=None):
        body = payload(
            model, messages, stream=False, max_tokens=256, tools=tools, parallel=parallel
        )
        return complete(url, body)

    report = {}
    single = ask([{"role": "user", "content": "What is the weather in Paris?"}])
    report["single"] = {
        "ok": [c["name"] for c in single["tool_calls"]] == ["get_weather"],
        "calls": single["tool_calls"],
        "finish_reason": single["finish_reason"],
        "content": (single.get("content") or "")[:240],
    }

    parallel = ask(
        [
            {
                "role": "user",
                "content": "Look up the weather in Paris and the weather in London.",
            }
        ],
        parallel=True,
    )
    names = [c["name"] for c in parallel["tool_calls"]]
    report["parallel"] = {
        "ok": names.count("get_weather") >= 2,
        "calls": parallel["tool_calls"],
        "finish_reason": parallel["finish_reason"],
    }

    first = ask([{"role": "user", "content": "What is the weather in Paris?"}])
    follow = {"ok": False, "calls": [], "finish_reason": None}
    if first["tool_calls"]:
        call = first["tool_calls"][0]
        follow = ask(
            [
                {"role": "user", "content": "What is the weather in Paris?"},
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": call["id"] or "call_weather",
                            "type": "function",
                            "function": {
                                "name": call["name"],
                                "arguments": call["arguments"] or "{}",
                            },
                        }
                    ],
                },
                {
                    "role": "tool",
                    "tool_call_id": call["id"] or "call_weather",
                    "content": '{"city":"Paris","temp_c":18}',
                },
                {"role": "user", "content": "Now get the local time in that city."},
            ]
        )
        follow = {
            "ok": any(c["name"] == "get_time" for c in follow["tool_calls"]),
            "calls": follow["tool_calls"],
            "finish_reason": follow["finish_reason"],
        }
    report["multi_step"] = follow

    history = ask(
        [
            {"role": "user", "content": "What is the weather in Paris?"},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call_weather",
                        "type": "function",
                        "function": {
                            "name": "get_weather",
                            "arguments": '{"city":"Paris"}',
                        },
                    }
                ],
            },
            {
                "role": "tool",
                "tool_call_id": "call_weather",
                "content": '{"city":"Paris","temp_c":18}',
            },
            {"role": "assistant", "content": "It is 18C in Paris."},
            {"role": "user", "content": "What time is it there?"},
        ]
    )
    report["assistant_history"] = {
        "ok": any(c["name"] == "get_time" for c in history["tool_calls"]),
        "calls": history["tool_calls"],
        "finish_reason": history["finish_reason"],
    }
    return report


def open_worker(job: dict):
    gpu = job["gpu"]
    engine = job["engine"]
    token = gpu.replace("-", "")
    cls = modal.Cls.from_name(
        "overmind-dedicated",
        f"{token}_{engine}_dedicated",
        environment_name=ENV,
    )
    cls = cls.with_options(max_containers=48, scaledown_window=120)
    kwargs = {
        "model_path": job["model_path"],
        "model_name": SERVED,
        "max_model_len": job["max_model_len"],
        "enable_lora": False,
        "max_lora_rank": 16,
        "adapter_path": job.get("adapter_path") or "",
    }
    if engine == "vllm":
        kwargs["snapshot_engine"] = True
    instance = cls(**kwargs)
    started = time.perf_counter()
    url = instance.direct_url.spawn().get(timeout=2400)
    cold = time.perf_counter() - started
    if not url:
        raise RuntimeError("Dedicated worker returned an empty tunnel URL.")
    return instance, url, cold


def release(instance) -> None:
    try:
        from asgiref.sync import async_to_sync

        result = instance.update_autoscaler(min_containers=0, max_containers=1, scaledown_window=2)
        if inspect.iscoroutine(result):

            async def _wait():
                return await result

            async_to_sync(_wait)()
    except Exception as exc:
        print(f"release failed: {exc}", flush=True)


def measure(job: dict) -> dict:
    row = {
        "model": job["model"],
        "kind": job["kind"],
        "engine": job["engine"],
        "speculative": job["speculative"],
        "gpu": job["gpu"],
        "max_model_len": job["max_model_len"],
        "supports_tool_calling": job["tools"],
    }
    instance = None
    try:
        instance, url, cold = open_worker(job)
        row["cold_start_s"] = round(cold, 1)
        complete(
            url,
            payload(
                job["model"], [{"role": "user", "content": "ping"}], stream=False, max_tokens=16
            ),
        )
        streamed = complete(
            url,
            payload(
                job["model"],
                [{"role": "user", "content": PROMPT}],
                stream=True,
                max_tokens=MAX_TOKENS,
            ),
        )
        plain = complete(
            url,
            payload(
                job["model"],
                [{"role": "user", "content": PROMPT}],
                stream=False,
                max_tokens=MAX_TOKENS,
            ),
        )
        row["warm_ttft_s"] = streamed.get("ttft_s")
        row["stream_latency_s"] = streamed["latency_s"]
        row["stream_tokens_per_s"] = streamed["tokens_per_s"]
        row["stream_completion_tokens"] = streamed["completion_tokens"]
        row["nonstream_latency_s"] = plain["latency_s"]
        row["nonstream_tokens_per_s"] = plain["tokens_per_s"]
        row["nonstream_completion_tokens"] = plain["completion_tokens"]
        try:
            row["tools"] = tool_report(url, job["model"])
        except Exception as exc:
            row["tools"] = {"error": str(exc)[:800]}
    except Exception as exc:
        row["error"] = str(exc)[:2000]
    finally:
        if instance is not None:
            release(instance)
    return row


def serve_jobs(cached: set[str]) -> tuple[list[dict], list[dict]]:
    jobs = []
    skipped = []
    for item in catalog():
        if not item["servable"]:
            reason = item["disabled_reason"] or (
                "Decision models do not serve chat completions."
                if item["kind"] == "decision"
                else "Not in the Modal chat catalog."
            )
            skipped.append(
                {
                    "model": item["model"],
                    "kind": "skipped",
                    "engine": None,
                    "speculative": None,
                    "reason": reason,
                    "servable": False,
                }
            )
            continue
        picked = pick_gpu(item["entry"])
        if picked is None:
            skipped.append(
                {
                    "model": item["model"],
                    "kind": "base",
                    "engine": engine_for(item["model"]),
                    "speculative": speculative(item["model"]),
                    "error": "No GPU holds the BF16 weights.",
                    "servable": True,
                }
            )
            continue
        gpus, length = picked
        hf = item["entry"].get("hf_model_id") or item["model"]
        if item["model"] in cached:
            repo, ready = item["model"], True
        elif hf in cached:
            repo, ready = hf, True
        else:
            repo, ready = hf, False
        jobs.append(
            {
                "model": item["model"],
                "kind": "base",
                "engine": engine_for(item["model"]),
                "speculative": speculative(item["model"]),
                "gpu": gpus[0],
                "gpus": gpus,
                "max_model_len": length,
                "model_path": ".base_models/" + repo.replace("/", "--"),
                "fetch_repo": repo,
                "tools": item["tools"],
                "cached": ready,
            }
        )
    return jobs, skipped


def adapter_job() -> dict | None:
    deployed = (
        DeployedModel.objects.filter(status=DeployedModel.Status.READY, is_lora=True)
        .order_by("-created_at")
        .first()
    )
    if deployed is None:
        return None
    entry = next(item["entry"] for item in catalog() if item["model"] == deployed.base_model_id)
    picked = pick_gpu(entry)
    if picked is None:
        return None
    gpus, length = picked
    return {
        "model": deployed.base_model_id,
        "kind": "adapter",
        "engine": engine_for(deployed.base_model_id),
        "speculative": speculative(deployed.base_model_id),
        "gpu": gpus[0],
        "gpus": gpus,
        "max_model_len": min(length, deployed.max_model_len or length),
        "model_path": rel_weights_path(deployed.weights_path),
        "adapter_path": rel_weights_path(deployed.adapter_path),
        "tools": True,
        "cached": True,
        "adapter_model_id": deployed.model_id,
    }


def fetch_base(model: str) -> None:
    print(f"fetch {model}", flush=True)
    modal.Function.from_name("overmind-register", "fetch_base_model", environment_name=ENV).remote(
        base_model=model
    )


def run_one(job: dict) -> dict:
    try:
        if not job.get("cached"):
            fetch_base(job.get("fetch_repo") or job["model"])
        row = measure(job)
        err = row.get("error") or ""
        if "DFlash draft" in err or "not on the weights volume" in err:
            fetch_base(job.get("fetch_repo") or job["model"])
            row = measure(job)
            err = row.get("error") or ""
        gpus = job.get("gpus") or []
        idx = gpus.index(job["gpu"]) if job.get("gpu") in gpus else -1
        while err and "exit_code" in err and idx + 1 < len(gpus):
            idx += 1
            print(f"retry {job['model']} on {gpus[idx]}", flush=True)
            row = measure({**job, "gpu": gpus[idx]})
            err = row.get("error") or ""
        return row
    except Exception as exc:
        return {
            "model": job["model"],
            "kind": job["kind"],
            "engine": job.get("engine"),
            "speculative": job.get("speculative"),
            "gpu": job.get("gpu"),
            "error": str(exc)[:1500],
        }


def main() -> None:
    done = finished()
    cached = set(
        modal.Function.from_name(
            "overmind-register", "list_base_models", environment_name=ENV
        ).remote()
    )
    print(f"cached bases: {len(cached)}", flush=True)
    jobs, skipped = serve_jobs(cached)
    for row in skipped:
        if (row["model"], row.get("kind") or "base") not in done:
            record(row)
    jobs = [job for job in jobs if (job["model"], job["kind"]) not in done]
    only = [arg for arg in sys.argv[1:] if not arg.startswith("-")]
    if only:
        jobs = [job for job in jobs if job["model"] in only]
    adapter = adapter_job()
    if adapter is not None and (adapter["model"], adapter["kind"]) not in done:
        print(f"adapter {adapter['adapter_model_id']} on {adapter['model']}", flush=True)
        jobs.append(adapter)
    elif adapter is None and (None, "adapter") not in done:
        record(
            {
                "model": None,
                "kind": "adapter",
                "error": "No ready LoRA adapter. A fine-tune was not started in this pass.",
            }
        )
    print(f"launching {len(jobs)}", flush=True)
    if not jobs:
        return
    with ThreadPoolExecutor(max_workers=len(jobs)) as pool:
        futures = [pool.submit(run_one, job) for job in jobs]
        for future in as_completed(futures):
            record(future.result())


if __name__ == "__main__":
    main()
