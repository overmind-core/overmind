"""Dev-only Docker HTTP validation; never sends requests to production."""

import argparse
import json
import math
import os
import time
import uuid

import django
import modal
import requests
from django.apps import apps
from django.conf import settings

from modal_shared.modelfam import serve_image_key
from modal_shared.shared import base_pool_name, worker_cls_name
from overbae.modal.gpu_selector import select_gpu
from overbae.modal.model_registry import get_hf_base, get_model_config_any_backend


def completion(url, token, model):
    started_at, started = time.time(), time.monotonic()
    first_token = None
    content = ""
    with requests.post(
        url,
        headers={"Authorization": f"Bearer {token}"},
        json={
            "model": model,
            "messages": [
                {"role": "user", "content": "What is 17 times 23? Reply with only the number."}
            ],
            "stream": True,
            "temperature": 0,
            "max_tokens": 64,
            "chat_template_kwargs": {"enable_thinking": False},
        },
        stream=True,
        timeout=(10, 2700),
    ) as response:
        response.raise_for_status()
        for line in response.iter_lines(chunk_size=1):
            if not line.startswith(b"data: ") or line == b"data: [DONE]":
                continue
            payload = json.loads(line[6:])
            if "error" in payload:
                raise RuntimeError(payload["error"])
            for choice in payload.get("choices", []):
                text = choice.get("delta", {}).get("content") or ""
                if text and first_token is None:
                    first_token = time.monotonic() - started
                content += text
    if first_token is None or not content.strip():
        raise RuntimeError("Completion returned no content token")
    elapsed = time.monotonic() - started
    if abs(time.time() - started_at - elapsed) > 2:
        raise RuntimeError("Client clock suspension or adjustment invalidated this measurement")
    result = {
        "request_started_at": started_at,
        "ttft_s": first_token,
        "latency_s": elapsed,
        "content": content,
    }
    print(json.dumps({"event": "request", "model": model, **result}), flush=True)
    return result


def cold_readiness(first, info, info_start, info_end):
    # Estimate cross-host clock offset with a measured round-trip uncertainty bound.
    offset = info["observed_at"] - (info_start + info_end) / 2
    uncertainty = (info_end - info_start) / 2
    elapsed = info["ready_at"] - offset - first["request_started_at"]
    return {
        "request_to_ready_estimate_s": elapsed,
        "clock_uncertainty_s": uncertainty,
        "ready_before_request": elapsed + uncertainty < 0,
    }


def distribution_distance(first, second):
    left = {token: math.exp(score) for token, score in first}
    right = {token: math.exp(score) for token, score in second}
    # Include unreported probability mass as one bucket; tail logprob ULPs alone
    # overstated the A-only numerical variation observed on the 35B engine.
    return (
        sum(abs(left.get(token, 0) - right.get(token, 0)) for token in left.keys() | right.keys())
        + abs(sum(left.values()) - sum(right.values()))
    ) / 2


def verify_adapter_effect(worker, models):
    distributions = []
    # Warm both prefixes before comparing A/A/B/B/A. A cold prefill and a
    # prefix-cache hit took different numerical paths in the live BF16 control.
    for index in (0, 1, 0, 0, 1, 1, 0):
        model = models[index]
        response = worker.infer.remote(
            method="POST",
            path="/v1/chat/completions",
            headers={"content-type": "application/json"},
            adapter=(model.model_id, model.adapter_path.removeprefix("/weights/")),
            body=json.dumps(
                {
                    "model": model.model_id,
                    "messages": [
                        {
                            "role": "user",
                            "content": "I forgot my password and cannot log in. What should I do?",
                        }
                    ],
                    "stream": False,
                    "temperature": 0,
                    "max_tokens": 1,
                    "logprobs": True,
                    "top_logprobs": 5,
                    "chat_template_kwargs": {"enable_thinking": False},
                }
            ).encode(),
        )
        if response["status"] != 200:
            raise RuntimeError(f"Adapter diagnostic returned HTTP {response['status']}")
        payload = json.loads(response["body"])
        if "error" in payload:
            raise RuntimeError(payload["error"])
        entries = payload["choices"][0]["logprobs"]["content"][0]["top_logprobs"]
        distributions.append([(entry["token"], entry["logprob"]) for entry in entries])
    print(json.dumps({"event": "adapter_distributions", "values": distributions}), flush=True)
    first, repeated, other, other_repeated, returned = distributions[2:]
    if first == other:
        raise RuntimeError("Distinct adapters produced identical token distributions")
    recovery_error = distribution_distance(first, returned)
    control_error = max(
        distribution_distance(first, repeated), distribution_distance(other, other_repeated)
    )
    adapter_difference = distribution_distance(first, other)
    if first[0][0] != returned[0][0] or max(recovery_error, control_error) > 0.001:
        raise RuntimeError("Adapter A did not recover its token distribution after B")
    if adapter_difference <= max(0.000001, 4 * max(recovery_error, control_error)):
        raise RuntimeError("Adapter effect is not distinguishable from numerical variation")
    return {
        "adapter_effect_verified": True,
        "warmup_distributions": distributions[:2],
        "distributions_a_a_b_b_a": distributions[2:],
        "control_probability_error": control_error,
        "recovery_probability_error": recovery_error,
        "adapter_probability_difference": adapter_difference,
        "transport": "Docker to GPU RPC; platform HTTP does not forward logprobs",
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", required=True)
    parser.add_argument("--adapter-paths", nargs=2, required=True)
    parser.add_argument("--rank", type=int, default=16)
    parser.add_argument("--context", type=int, default=32768)
    parser.add_argument("--rounds", type=int, default=3)
    args = parser.parse_args()
    if args.rounds < 1 or args.rank < 1:
        parser.error("Rounds and rank must be positive")
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "overbae.settings")
    django.setup()
    api_token_cls, deployed_cls, project_cls, membership_cls, user_cls = (
        apps.get_model("overbae", name)
        for name in ("APIToken", "DeployedModel", "Project", "ProjectMembership", "User")
    )

    if (
        os.environ.get("MODAL_ENVIRONMENT") != "overmind-dev"
        or "overmind-dev" not in settings.INFERENCE_API_URL
    ):
        raise RuntimeError("This validation is restricted to the dev Docker/Modal environment")
    health = requests.get("http://127.0.0.1:8000/health", timeout=10)
    health.raise_for_status()
    cfg = get_model_config_any_backend(args.model)
    if not cfg:
        raise ValueError("Model must be in the platform catalog")
    base_model = get_hf_base(args.model, backend="modal")
    gpu, _ = select_gpu({**cfg, "fp8_supported": False}, args.context)
    base = modal.Function.from_name(
        "overmind-register", "fetch_base_model", environment_name="overmind-dev"
    ).remote(base_model=base_model)
    rel_path = base["base_model_path"].removeprefix("/weights/")
    serve_image = serve_image_key(args.model)
    worker = modal.Cls.from_name(
        "overmind-inference",
        worker_cls_name(gpu, serve_image, enable_lora=True),
        environment_name="overmind-dev",
    )(
        model_path=rel_path,
        model_name=base_pool_name(rel_path),
        max_model_len=args.context,
        enable_lora=True,
        max_lora_rank=args.rank,
        base_identity=base["base_identity"],
    )
    label = uuid.uuid4().hex[:12]
    user = user_cls.objects.create_user(
        email=f"serving-{label}@example.invalid", clerk_user_id=f"serving-{label}"
    )
    project = project_cls.objects.create(
        name=f"Serving validation {label}", slug=f"serving-{label}"
    )
    membership_cls.objects.create(user=user, project=project)
    token, token_row = api_token_cls.create_for_user(user, "Serving validation", project=project)
    models = []
    known_origins, runtimes = set(), set()
    try:
        for index, adapter in enumerate(args.adapter_paths):
            if not adapter.startswith(".adapters/") or ".." in adapter.split("/"):
                raise ValueError("Adapter paths must be inside the dev adapter directory")
            models.append(
                deployed_cls.objects.create(
                    project=project,
                    model_id=f"serving-validation-{label}-{index}",
                    base_model_id=args.model,
                    weights_path=base["base_model_path"],
                    adapter_path=f"/weights/{adapter}",
                    is_lora=True,
                    lora_rank=args.rank,
                    quantization=deployed_cls.Quantization.BF16,
                    gpu_type=gpu,
                    max_model_len=args.context,
                    status=deployed_cls.Status.READY,
                )
            )
        print(
            json.dumps(
                {
                    "event": "configuration",
                    "model": args.model,
                    "gpu": gpu,
                    "base_identity": base["base_identity"],
                    "project": str(project.pk),
                    "adapters": args.adapter_paths,
                    "rank": args.rank,
                    "context": args.context,
                }
            ),
            flush=True,
        )
        preparation_started = time.monotonic()
        prepared = worker.startup_info.remote()
        print(
            json.dumps(
                {
                    "event": "preparation",
                    "elapsed_s": time.monotonic() - preparation_started,
                    "startup": prepared,
                }
            ),
            flush=True,
        )
        known_origins.add(prepared["origin"])
        worker.shutdown_self.remote()
        time.sleep(15)
        for iteration in range(args.rounds):
            url = "http://127.0.0.1:8000/api/v1/chat/completions"
            first = completion(url, token, models[0].model_id)
            second = completion(url, token, models[0].model_id)
            switched = completion(url, token, models[1].model_id)
            returned = completion(url, token, models[0].model_id)
            if any(
                result["content"].strip() != "391" for result in (first, second, switched, returned)
            ):
                raise RuntimeError("Arithmetic canary failed")
            info_start = time.time()
            info = worker.startup_info.remote()
            info_end = time.time()
            if info["runtime"] in runtimes:
                raise RuntimeError("Cold round reused the same live container")
            if set(info["loaded_adapters"]) != {model.model_id for model in models}:
                raise RuntimeError("Adapter isolation/load state mismatch")
            readiness = cold_readiness(first, info, info_start, info_end)
            if readiness["ready_before_request"]:
                raise RuntimeError("Worker was already ready before the cold request")
            print(json.dumps({"event": "cold_readiness", **readiness, "startup": info}), flush=True)
            canary = verify_adapter_effect(worker, models)
            print(
                json.dumps(
                    {
                        "event": "result",
                        "iteration": iteration,
                        "classification": "later-restore"
                        if info["origin"] in known_origins
                        else "new-origin-unclassified",
                        "first": first,
                        "second": second,
                        "adapter_b": switched,
                        "adapter_a_again": returned,
                        "startup": info,
                        "cold_readiness": readiness,
                        "transport": "Docker platform HTTP",
                        "canary": canary,
                    }
                ),
                flush=True,
            )
            known_origins.add(info["origin"])
            runtimes.add(info["runtime"])
            worker.shutdown_self.remote()
            time.sleep(15)
    finally:
        token_row.is_active = False
        token_row.save(update_fields=["is_active"])
        deployed_cls.objects.filter(pk__in=[model.pk for model in models]).update(
            status=deployed_cls.Status.DELETED
        )
        project_cls.objects.filter(pk=project.pk).update(is_active=False)
        user_cls.objects.filter(pk=user.pk).update(is_active=False)


if __name__ == "__main__":
    main()
