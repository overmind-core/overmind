"""Modal vLLM workers + management API for the serverless inference platform.

``InferenceAPIServer`` (CPU-only) is the single entry point from the Django backend: it registers
models, deletes weights from the shared volume, and can proxy inference. One GPU worker class per
tier sits behind it, and each parametrized instance is its own container pool.

Modal secrets, under secret name "overmind-inference":
  WORKER_API_KEY    — worker-to-worker bearer token (optional)
  INFERENCE_API_KEY — bearer token for InferenceAPIServer HTTP endpoints

Deploy:
  modal deploy overbae/modal/modal_vllm_worker.py --env overmind-dev
"""

import asyncio
import contextlib
import json
import os
import subprocess
import time
import urllib.request
import uuid
from pathlib import Path

import modal

from modal_shared.context_budget import completion_body
from modal_shared.serving.args import lora_load_request
from modal_shared.serving.artifacts import read_base_manifest

MODAL_ENVIRONMENT = os.environ.get("MODAL_ENVIRONMENT", "overmind-dev")
IS_PROD = MODAL_ENVIRONMENT == "overmind-prod"

MINUTES = 60  # seconds

SCALEDOWN_WINDOW_SECONDS = 2 * MINUTES

# Big models pay minutes to reload, so they stay up far longer before being reclaimed.
LARGE_SCALEDOWN_WINDOW_SECONDS = 15 * MINUTES
# Pack concurrent requests onto one warm GPU (vLLM continuous batching) instead of
# eagerly spinning replicas: without a high target plus the max_containers cap, a
# burst of 3–4 inputs against a cold pool fans out into N GPUs. Each parametrized
# instance is its own pool, so this caps GPUs per deployed model, not globally.
MAX_CONCURRENT_INPUTS = 32  # hard ceiling per container (= vLLM --max-num-seqs)
TARGET_CONCURRENT_INPUTS = 28  # autoscaler only adds a replica near this load
MAX_CONTAINERS_PER_MODEL = 1  # queue excess; raise if sustained load needs fan-out

VLLM_PORT = 8000
# NemotronH/Mamba-hybrid checkpoints can need 15-20+ min to become healthy on a cold
# container: large weight download plus Triton SSM-kernel autotuning on first call
# (vllm-project/vllm#34399). The ceiling must leave margin between _wait_for_vllm timing
# out and Modal killing the function, which surfaces as a job stuck "warming" forever
# instead of a clear error.
WORKER_TIMEOUT_SECONDS = 40 * MINUTES

# modal_shared lives outside overbae on purpose — see modal_sft_worker.py.
from modal_shared.modelfam import serve_image_from_checkpoint, serve_image_key  # noqa: E402
from modal_shared.shared import (  # noqa: E402
    ADAPTER_PATH_HEADER,
    GPU_TYPE_HEADER,
    INFERENCE_APP_NAME,
    LORA_RANK_HEADER,
    MAX_MODEL_LEN_HEADER,
    SERVE_IMAGE_HEADER,
    WEIGHTS_MOUNT,
    WEIGHTS_PATH_HEADER,
    base_pool_name,
    parse_routing_headers,
    rel_weights_path,
    resolve_inference_url,
    worker_cls_name,
)

VLLM_CACHE_MOUNT = "/root/.cache/vllm"

weights_vol = modal.Volume.from_name("overmind-weights", create_if_missing=True)
vllm_cache_vol = modal.Volume.from_name("overmind-vllm-cache", create_if_missing=True)
artifacts_vol = modal.Volume.from_name("overmind-inference-artifacts", create_if_missing=True)
ARTIFACTS_MOUNT = "/inference-artifacts"

from modal_shared.images.serve import (  # noqa: E402
    LORA_SERVE_IMAGES,
    SERVE_IMAGES,
    api_server_image,
)
from modal_shared.stacks import GPU_TIER, WORKER_CLS  # noqa: E402

APP_NAME = INFERENCE_APP_NAME
app = modal.App(APP_NAME)

inference_secret = modal.Secret.from_name("overmind-inference")

_volume_mounts = {
    WEIGHTS_MOUNT: weights_vol,
    VLLM_CACHE_MOUNT: vllm_cache_vol,
}


def _wait_for_vllm(timeout: int = 30 * MINUTES, *, proc: subprocess.Popen | None = None) -> None:
    """A ``proc`` that exits before health is ready (OOM, bad weights, engine init crash) fails
    immediately rather than burning the full timeout, which leaves a GPU container hanging while
    Modal retries the input behind it."""
    import urllib.error
    import urllib.request

    url = f"http://localhost:{VLLM_PORT}/health"
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if proc is not None and proc.poll() is not None:
            raise RuntimeError(f"vLLM exited before becoming healthy (exit_code={proc.returncode})")
        try:
            with urllib.request.urlopen(url, timeout=5) as r:
                if r.status == 200:
                    return
        except (urllib.error.URLError, OSError):
            pass
        time.sleep(3)
    raise RuntimeError(f"vLLM did not become healthy within {timeout}s")


def _make_worker_health_app():
    """Kept only so ``_resolve_worker_url`` can mint a stable URL string for the registry. Real
    traffic goes through the ``infer`` RPC."""
    from starlette.applications import Starlette
    from starlette.responses import JSONResponse
    from starlette.routing import Route

    async def health(_request):
        return JSONResponse({"ok": True})

    return Starlette(routes=[Route("/health", health)])


def _rewrite_legacy_layer_types(cfg_path: Path, *, _json) -> None:
    """transformers≥5.11 dropped ``attention`` from allowed layer_types; old FT configs still use it."""
    if not cfg_path.is_file():
        return
    try:
        cfg = _json.loads(cfg_path.read_text())
    except (OSError, _json.JSONDecodeError, TypeError):
        return
    changed = False

    def _walk(obj: object) -> None:
        nonlocal changed
        if not isinstance(obj, dict):
            return
        types = obj.get("layer_types")
        if isinstance(types, list) and any(t == "attention" for t in types):
            obj["layer_types"] = ["full_attention" if t == "attention" else t for t in types]
            changed = True
        for v in obj.values():
            if isinstance(v, dict):
                _walk(v)

    _walk(cfg)
    if not changed:
        return
    cfg_path.write_text(_json.dumps(cfg, indent=2) + "\n")
    print("[vLLM] rewrote layer_types attention → full_attention")


def _peek_base_model(model_path: str) -> str:
    meta_path = Path(WEIGHTS_MOUNT) / model_path / ".meta.json"
    if not meta_path.is_file():
        return ""
    try:
        return json.loads(meta_path.read_text()).get("base_model") or ""
    except (OSError, json.JSONDecodeError, TypeError):
        return ""


def _peek_config_blobs(model_path: str) -> tuple[str, list[str]]:
    cfg_path = Path(WEIGHTS_MOUNT) / model_path / "config.json"
    if not cfg_path.is_file():
        return "", []
    try:
        cfg = json.loads(cfg_path.read_text())
    except (OSError, json.JSONDecodeError, TypeError):
        return "", []
    arches = cfg.get("architectures") or []
    if not isinstance(arches, list):
        arches = []
    return str(cfg.get("model_type") or ""), [str(a) for a in arches]


def _serve_image_for(*, model_name: str, model_path: str, hinted: str = "") -> str:
    if hinted and hinted != "vllm":
        return hinted
    weights_vol.reload()
    model_type, arches = _peek_config_blobs(model_path)
    return serve_image_from_checkpoint(
        model_name=model_name,
        base_model=_peek_base_model(model_path),
        model_type=model_type,
        architectures=arches,
    )


def _make_worker(
    *,
    gpu_type: str,
    model_path: str,
    model_name: str,
    max_model_len: int,
    serve_image: str = "vllm",
    enable_lora: bool = False,
    max_lora_rank: int = 16,
):
    cls_name = worker_cls_name(gpu_type, serve_image, enable_lora=enable_lora)
    worker_cls = modal.Cls.from_name(APP_NAME, cls_name)
    identity = {}
    if enable_lora:
        weights_vol.reload()
        manifest = read_base_manifest(Path(WEIGHTS_MOUNT) / model_path)
        identity["base_identity"] = manifest["identity"]
    return worker_cls(
        model_path=model_path,
        model_name=model_name,
        max_model_len=max_model_len,
        enable_lora=enable_lora,
        max_lora_rank=max_lora_rank,
        **identity,
    ), cls_name


class _BaseVLLMWorker:
    """Shared vLLM lifecycle for GPU workers.

    ``@enter`` must never raise: Modal retries enter failures forever,
    ignoring ``retries=0``. ``_startup_error`` is recorded instead and fails once in ``infer``.
    Uses ``@modal.asgi_app()`` because ``@modal.web_server`` does not support parametrized classes.
    """

    model_path: str = modal.parameter()
    model_name: str = modal.parameter()
    max_model_len: int = modal.parameter(default=8192)
    # Shared-base mode: model_path is a stock base and the adapters that ride on it arrive at
    # runtime. Both are parameters because both change the vLLM argv, and a Modal parameter set
    # is what separates one container pool from another — every deployment sharing this base,
    # tier and rank lands in the same pool and so shares its warm containers.
    enable_lora: bool = modal.parameter(default=False)
    max_lora_rank: int = modal.parameter(default=16)
    snapshot_weights = False

    def startup(self) -> None:
        """Never raise here, and never call ``stop_fetching_inputs`` here either: that exits
        before the input is consumed, which triggers a reschedule just as an enter failure does."""
        self._start()

    def _start(self) -> None:
        self._startup_error: str | None = None
        self._loaded_adapters: set[str] = set()
        self._adapter_lock = None
        try:
            self._startup_inner(_json=json, _os=os)
        except Exception as e:
            self._fail_startup(e)

    def _fail_startup(self, error) -> None:
        proc = getattr(self, "_proc", None)
        if proc is not None and proc.poll() is None:
            proc.kill()
        self._startup_error = (
            f"vLLM startup failed for model_name={self.model_name!r} "
            f"model_path={self.model_path!r}: {error}"
        )
        print(f"[vLLM] {self._startup_error}")

    def _ensure_ready(self) -> None:
        err = getattr(self, "_startup_error", None)
        if err:
            with contextlib.suppress(Exception):
                modal.experimental.stop_fetching_inputs()
            raise RuntimeError(err)

    def _startup_inner(self, *, _json, _os) -> None:
        # model_path is stored without the /weights/ prefix, because slashes break Modal
        # URL params in parametrized classes.
        full_path = f"{WEIGHTS_MOUNT}/{self.model_path}"

        # Exception stacks to inference clients only outside prod.
        _os.environ["VLLM_SERVER_DEV_MODE"] = "1" if self.snapshot_weights or not IS_PROD else "0"

        # A container's Volume mount is a point-in-time view, so weights RegisterAPIServer
        # commits after this container is scheduled stay invisible until reload(). Deploy
        # uses exactly that ordering — write weights, then drive a worker. The compile
        # cache Volume is the same: without reload the last boot's inductor artifacts
        # are invisible and vLLM recompiles from scratch.
        weights_vol.reload()
        vllm_cache_vol.reload()

        if not self.enable_lora:
            _rewrite_legacy_layer_types(Path(full_path) / "config.json", _json=_json)

        # Fail before allocating GPU/vLLM when the weights are genuinely gone, or a bad
        # param-set crash-loops the input backlog for minutes per attempt.
        if not _os.path.isdir(full_path):
            raise RuntimeError(
                f"weights missing at {full_path} (model_name={self.model_name!r}); "
                "refusing to start — re-register the deployment"
            )

        meta_path = f"{full_path}/.meta.json"
        quantization = None
        base_model = ""
        if _os.path.exists(meta_path):
            try:
                with open(meta_path) as _f:
                    meta = _json.loads(_f.read())
                quantization = meta.get("quantization")
                base_model = meta.get("base_model") or ""
            except Exception:
                pass

        # Runtime adapter load is what lets a new deployment join an already-warm base pool
        # instead of booting its own container.
        if self.enable_lora:
            _os.environ["VLLM_ALLOW_RUNTIME_LORA_UPDATING"] = "1"
            base_model = read_base_manifest(Path(full_path))["repo"]

        from modal_shared.modelfam import resolve
        from modal_shared.serving.args import (
            VllmServeContext,
            build_vllm_args,
            multimodal_text_only_args,
        )

        spec = resolve(base_model or self.model_name)
        print(
            f"[vLLM] Serving {full_path} "
            f"(quantization={quantization!r} base_model={base_model!r} "
            f"tool_parser={spec.tool_call_parser} family={spec.key})"
        )
        cmd = build_vllm_args(
            VllmServeContext(
                model_path=full_path,
                model_name=self.model_name,
                max_model_len=self.max_model_len,
                port=VLLM_PORT,
                max_num_seqs=MAX_CONCURRENT_INPUTS,
                quantization=quantization,
                base_model=base_model,
                enable_lora=self.enable_lora,
                max_lora_rank=self.max_lora_rank,
            ),
            spec,
        )
        if self.snapshot_weights:
            if not self.enable_lora:
                raise ValueError("Snapshot workers require shared-base LoRA mode")
            cmd += [
                "--enable-sleep-mode",
                "--distributed-executor-backend",
                "modal_shared.serving.snapshot.SnapshotExecutor",
                "--worker-extension-cls",
                "modal_shared.serving.weights.SharedBaseWeights",
            ]
        if "--chat-template" in cmd:
            print(f"[vLLM] Using checkpoint chat template: {full_path}/chat_template.jinja")

        if _is_text_only_finetune_of_multimodal_base(base_model, full_path, self.model_name):
            # Skip vLLM's multimodal init entirely, or it looks for the
            # preprocessor_config.json this checkpoint does not have.
            print(
                f"[vLLM] {base_model!r}/{self.model_name!r} is natively multimodal "
                "but checkpoint has no preprocessor/processor_config.json — "
                "serving language-model-only"
            )
            cmd += multimodal_text_only_args()

        print("Starting vLLM:", " ".join(cmd))
        self._serve_command = cmd
        # List form (no shell) so JSON kwargs like --default-chat-template-kwargs
        # are not mangled by the shell.
        self._proc = subprocess.Popen(cmd)  # noqa: S603
        _wait_for_vllm(proc=self._proc)
        # Writes to a Volume are discarded unless this container commits. Concurrent
        # boots can clobber each other's new files (last commit wins); a lost cache
        # only means the next cold start recompiles.
        with contextlib.suppress(Exception):
            vllm_cache_vol.commit()

    @modal.exit()
    def shutdown(self) -> None:
        if hasattr(self, "_proc") and self._proc.poll() is None:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                self._proc.kill()

    async def _ensure_adapter(self, name: str, rel_path: str) -> None:
        """Register an adapter with the running engine, once per container.

        Called on the request path because a shared base pool cannot know its adapters at
        startup. Loading is ~0.3-2 s against a 100-200 s container boot, and only the first
        request for a given adapter pays it.
        """
        import asyncio

        import httpx

        if name in self._loaded_adapters:
            return
        if self._adapter_lock is None:
            self._adapter_lock = asyncio.Lock()
        async with self._adapter_lock:
            if name in self._loaded_adapters:
                return
            # The adapter was very likely committed to the Volume after this container
            # started, so the mount has to be refreshed before vLLM can see it.
            weights_vol.reload()
            full = f"{WEIGHTS_MOUNT}/{rel_path}"
            async with httpx.AsyncClient(timeout=300) as client:
                resp = await client.post(
                    f"http://localhost:{VLLM_PORT}/v1/load_lora_adapter",
                    json=lora_load_request(name, full),
                )
            text = resp.text or ""
            # vLLM answers 400 "has already been loaded" when another container in this pool
            # got there first, which is success as far as the caller is concerned.
            if resp.status_code == 200 or "already been loaded" in text:
                self._loaded_adapters.add(name)
                print(f"[vLLM] adapter {name!r} ready from {full}")
                return
            raise RuntimeError(
                f"loading adapter {name!r} from {full} failed: {resp.status_code} {text[:300]}"
            )

    @modal.method()
    async def infer(
        self,
        *,
        method: str,
        path: str,
        body: bytes,
        headers: dict,
        adapter: tuple[str, str] | None = None,
    ) -> dict:
        """RPC entry point for the InferenceAPIServer proxy, returning
        ``{"status": int, "headers": dict, "body": bytes}``.

        ``adapter`` is a ``(served_name, weights-relative path)`` pair for shared-base mode; the
        request body must name that adapter as its model.

        Modal RPC rather than HTTP: ``@modal.asgi_app()`` on a parametrized class dispatches
        through a 303 redirect, which mandates GET and so loses a POST. ``@modal.enter()`` blocks
        here until vLLM is healthy, so the caller never handles redirect chains.
        """
        import httpx

        self._ensure_ready()

        if path not in {"/health", "/v1/models", "/v1/chat/completions", "/v1/completions"}:
            raise ValueError("Only inference endpoints may be proxied")

        if adapter:
            await self._ensure_adapter(adapter[0], adapter[1])

        try:
            body = completion_body(path, body)
        except ValueError as exc:
            return {
                "status": 400,
                "headers": {"content-type": "application/json"},
                "body": json.dumps(
                    {"error": {"message": str(exc), "type": "invalid_request_error"}}
                ).encode(),
            }
        url = f"http://localhost:{VLLM_PORT}{path}"
        safe_headers = {
            k: v for k, v in headers.items() if k.lower() not in ("host", "content-length")
        }
        async with httpx.AsyncClient(timeout=300) as client:
            resp = await client.request(method=method, url=url, headers=safe_headers, content=body)
        return {
            "status": resp.status_code,
            "headers": dict(resp.headers),
            "body": resp.content,
        }

    @modal.method()
    async def infer_stream(
        self,
        *,
        method: str,
        path: str,
        body: bytes,
        headers: dict,
        adapter: tuple[str, str] | None = None,
    ):
        """Streaming counterpart to ``infer``: yields one ``{"status", "headers"}`` frame, then
        raw response bytes as vLLM emits them.

        ``infer`` returns ``resp.content``, which does not resolve until generation finishes, so
        a streaming client sees the whole body at once and time-to-first-token equals total
        latency. Everything else about the two paths is identical.
        """
        import httpx

        self._ensure_ready()

        if path not in {"/v1/chat/completions", "/v1/completions"}:
            raise ValueError("Only completion endpoints may be streamed")

        if adapter:
            await self._ensure_adapter(adapter[0], adapter[1])

        try:
            body = completion_body(path, body)
        except ValueError as exc:
            yield {"status": 400, "headers": {"content-type": "application/json"}}
            yield json.dumps(
                {"error": {"message": str(exc), "type": "invalid_request_error"}}
            ).encode()
            return
        url = f"http://localhost:{VLLM_PORT}{path}"
        safe_headers = {
            k: v for k, v in headers.items() if k.lower() not in ("host", "content-length")
        }
        async with (
            httpx.AsyncClient(timeout=300) as client,
            client.stream(method, url, headers=safe_headers, content=body) as resp,
        ):
            yield {"status": resp.status_code, "headers": dict(resp.headers)}
            async for chunk in resp.aiter_bytes():
                yield chunk

    @modal.method()
    def shutdown_self(self) -> None:
        """Tears this container down ~3 s after the RPC returns, releasing the GPU.

        SIGTERM alone is not enough: vLLM's engine subprocess does not exit cleanly from it, and
        its torch.distributed teardown then retries a broken-pipe TCPStore write once a second
        forever, flooding the app log stream. Kill the vLLM child first, then escalate.
        """
        import os
        import signal
        import threading

        def _do_exit() -> None:
            time.sleep(3)
            proc = getattr(self, "_proc", None)
            if proc is not None and proc.poll() is None:
                print("[shutdown_self] killing vLLM child")
                proc.kill()
            print("[shutdown_self] sending SIGTERM")
            os.kill(os.getpid(), signal.SIGTERM)
            time.sleep(10)
            print("[shutdown_self] still alive — SIGKILL")
            os.kill(os.getpid(), signal.SIGKILL)

        threading.Thread(target=_do_exit, daemon=True).start()
        print("[shutdown_self] container will exit in ~3 s")

    @modal.asgi_app()
    def api(self):
        return _make_worker_health_app()


class _SharedBaseVLLMWorker(_BaseVLLMWorker):
    base_identity: str = modal.parameter(default="")
    snapshot_weights = True

    def _control(self, path: str, payload=None):
        request = urllib.request.Request(
            f"http://127.0.0.1:{VLLM_PORT}{path}",
            data=json.dumps(payload).encode() if payload is not None else b"",
            headers={"content-type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=WORKER_TIMEOUT_SECONDS) as response:
            body = response.read()
            return json.loads(body) if body else None

    def _base_rpc(self, method, *args):
        response = self._control("/collective_rpc", {"method": method, "args": list(args)})
        if not isinstance(response, dict) or len(response.get("results", [])) != 1:
            raise RuntimeError("Expected exactly one shared-base worker result")
        return response["results"][0]

    def _verify_base_identity(self):
        manifest = read_base_manifest(Path(WEIGHTS_MOUNT) / self.model_path)
        if manifest["identity"] != self.base_identity:
            raise RuntimeError("Base revision changed; refusing to reuse this snapshot")

    @modal.enter(snap=True)
    def startup(self) -> None:
        self._snapshot_origin = uuid.uuid4().hex
        self._startup_error = None
        try:
            weights_vol.reload()
            self._verify_base_identity()
            self._start()
            if self._startup_error:
                return
            artifacts_vol.reload()
            self._artifact = self._base_rpc(
                "prepare_shared_base", ARTIFACTS_MOUNT, self.base_identity, self._serve_command
            )
            artifacts_vol.commit()
            self._control("/sleep?level=2")
            print(
                json.dumps(
                    {
                        "event": "shared_base_snapshot",
                        "origin": self._snapshot_origin,
                        "artifact": self._artifact,
                    }
                ),
                flush=True,
            )
        except Exception as error:
            self._fail_startup(error)

    @modal.enter(snap=False)
    def restore(self) -> None:
        self._runtime = uuid.uuid4().hex
        self._loaded_adapters = set()
        self._adapter_lock = None
        started = time.monotonic()
        self._restore_metrics = {}
        if self._startup_error:
            return
        try:
            weights_vol.reload()
            self._verify_base_identity()
            artifacts_vol.reload()
            self._control("/wake_up?tags=weights")
            self._restore_metrics = self._base_rpc("restore_shared_base")
            self._control("/wake_up?tags=kv_cache")
            _wait_for_vllm(proc=self._proc)
            self._restore_metrics.update(
                restore_to_ready_s=time.monotonic() - started, ready_at=time.time()
            )
            print(
                json.dumps(
                    {
                        "event": "shared_base_ready",
                        "origin": self._snapshot_origin,
                        "runtime": self._runtime,
                        **self._restore_metrics,
                    }
                ),
                flush=True,
            )
        except Exception as error:
            self._fail_startup(error)

    @modal.method()
    def startup_info(self) -> dict:
        self._ensure_ready()
        return {
            "origin": self._snapshot_origin,
            "runtime": self._runtime,
            "artifact": self._artifact,
            "observed_at": time.time(),
            "task_id": os.environ.get("MODAL_TASK_ID"),
            "loaded_adapters": sorted(self._loaded_adapters),
            **self._restore_metrics,
        }


def _wants_stream(body: bytes) -> bool:
    """Streaming is decided from the request, not the response: the RPC shape has to be
    picked before the worker is called."""
    try:
        payload = json.loads(body)
    except (ValueError, TypeError):
        return False
    return isinstance(payload, dict) and bool(payload.get("stream"))


async def stream_with_keepalive(chunks, interval_s=15, *, sse=True):
    pending = None
    first = True
    ping = b": \n\n" if sse else b"\n"
    try:
        yield ping
        while True:
            pending = asyncio.ensure_future(anext(chunks))
            while not (await asyncio.wait({pending}, timeout=interval_s))[0]:
                yield ping
            try:
                chunk = pending.result()
            except StopAsyncIteration:
                if first:
                    raise RuntimeError("Worker returned no response") from None
                return
            if first:
                first = False
                if chunk["status"] != 200:
                    raise RuntimeError(f"Worker returned HTTP {chunk['status']}")
            else:
                yield chunk
    except Exception as exc:
        print(f"[proxy-stream-error] {type(exc).__name__}: {exc}")
        # Headers were sent before GPU allocation; late failures use the body error contract.
        error = b'{"error":{"message":"Inference backend error.","type":"server_error"}}'
        yield b"data: " + error + b"\n\n" if sse else error
        if sse:
            yield b"data: [DONE]\n\n"
    finally:
        if pending is not None:
            pending.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await pending
        await chunks.aclose()


def _normalize_model_blob(*parts: str) -> str:
    """Lowercase + unify separators so qwen3.5 / qwen3_5 / qwen3-5 all match."""
    return " ".join(parts).lower().replace("_", "-").replace(".", "-")


def _is_text_only_finetune_of_multimodal_base(
    base_model: str, full_path: str, model_name: str = ""
) -> bool:
    """Detection must not rest on ``.meta.json``'s ``base_model`` alone: older registers wrote it
    empty, which skipped ``--language-model-only`` and crash-looped vLLM on a missing
    ``preprocessor_config.json``. ``model_name`` and config.json are checked too."""
    import json as _json
    import os as _os

    from modal_shared.modelfam import is_natively_multimodal_blob

    # Qwen-VL / Gemma4 ship preprocessor_config.json; Muse ships processor_config.json.
    if _os.path.exists(f"{full_path}/preprocessor_config.json") or _os.path.exists(
        f"{full_path}/processor_config.json"
    ):
        return False

    if is_natively_multimodal_blob(base_model, model_name):
        return True

    cfg_path = f"{full_path}/config.json"
    if not _os.path.exists(cfg_path):
        return False
    try:
        with open(cfg_path) as f:
            cfg = _json.load(f)
    except Exception:
        return False

    arches = " ".join(cfg.get("architectures") or []).lower()
    # Qwen3_5ForConditionalGeneration / Qwen3VLForConditionalGeneration, …
    if "forconditionalgeneration" in arches.replace("_", ""):
        return True
    mtype = _normalize_model_blob(str(cfg.get("model_type") or ""))
    return is_natively_multimodal_blob(mtype)


# Modal fixes the container spec at class-definition time, so a separate class per
# GPU type is required even though the logic is identical. Only gpu and
# scaledown_window differ, so the rest lives in one dict rather than six copies.
_WORKER_KWARGS = {
    "secrets": [inference_secret],
    "volumes": _volume_mounts,
    "timeout": WORKER_TIMEOUT_SECONDS,
    "max_containers": MAX_CONTAINERS_PER_MODEL,
    # Broken cold starts (missing weights, OOM) must not be retried: retries are what
    # turn a single bad param-set into hanging inputs and workers.
    "retries": modal.Retries(max_retries=0),
    "enable_memory_snapshot": False,
}

_worker_concurrency = modal.concurrent(
    max_inputs=MAX_CONCURRENT_INPUTS,
    target_inputs=TARGET_CONCURRENT_INPUTS,
)

_SCALEDOWN = {
    "short": SCALEDOWN_WINDOW_SECONDS,
    "large": LARGE_SCALEDOWN_WINDOW_SECONDS,
}


def _register_worker(cls_name: str, gpu_type: str, serve_image: str, *, lora=False) -> None:
    bucket, default_len = GPU_TIER[gpu_type]
    image = (LORA_SERVE_IMAGES if lora else SERVE_IMAGES)[serve_image]
    parameters = {
        "__annotations__": {
            "model_path": str,
            "model_name": str,
            "max_model_len": int,
            "enable_lora": bool,
            "max_lora_rank": int,
        },
        "model_path": modal.parameter(),
        "model_name": modal.parameter(),
        "max_model_len": modal.parameter(default=default_len),
        "enable_lora": modal.parameter(default=lora),
        "max_lora_rank": modal.parameter(default=16),
    }
    if lora:
        parameters["__annotations__"]["base_identity"] = str
        parameters["base_identity"] = modal.parameter(default="")
    else:
        # Modal discovers hooks separately by snapshot phase across the entire MRO.
        # A post-snapshot hook on the common base would also run for LoRA restores.
        parameters["startup"] = modal.enter()(_BaseVLLMWorker.startup)
    cls = type(
        cls_name,
        (_SharedBaseVLLMWorker if lora else _BaseVLLMWorker,),
        parameters,
    )
    cls = _worker_concurrency(cls)
    globals()[cls_name] = app.cls(
        gpu=gpu_type,
        scaledown_window=_SCALEDOWN[bucket],
        image=image,
        **{
            **_WORKER_KWARGS,
            **(
                {
                    "cpu": 8,
                    "memory": {
                        "L4": 65536,
                        "L40S": 98304,
                        "A100-80GB": 131072,
                        "H200": 196608,
                        "B200": 262144,
                        "B300": 393216,
                    }[gpu_type],
                    "volumes": {**_volume_mounts, ARTIFACTS_MOUNT: artifacts_vol},
                    "enable_memory_snapshot": True,
                    "experimental_options": {"enable_gpu_snapshot": True},
                }
                if lora
                else {}
            ),
        },
    )(cls)


for (_gpu, _serve_image), _cls_name in WORKER_CLS.items():
    _register_worker(_cls_name, _gpu, _serve_image)
    _register_worker(
        worker_cls_name(_gpu, _serve_image, enable_lora=True), _gpu, _serve_image, lora=True
    )


@app.cls(
    image=api_server_image,
    secrets=[inference_secret],
    volumes={WEIGHTS_MOUNT: weights_vol},
    scaledown_window=5,
    # Proxy waits on worker.infer.remote through cold-start; 70B/72B first
    # load can exceed Modal's default 300s and return gateway 500s otherwise.
    timeout=WORKER_TIMEOUT_SECONDS,
)
@modal.concurrent(max_inputs=500)
class InferenceAPIServer:
    """CPU-only management + inference proxy. Routing (gpu / weights / max_model_len)
    arrives on each request from Django/Postgres — not a Volume JSON.

    Worker HTTP URLs are minted for Django to store; real chat goes through ``infer``
    RPC because parametrized ``@modal.asgi_app`` 303s lose POST.
    """

    def _resolve_worker_url(
        self,
        *,
        gpu_type: str,
        model_path: str,
        model_name: str,
        max_model_len: int,
        serve_image: str = "vllm",
    ) -> str:
        image = _serve_image_for(
            model_name=model_name,
            model_path=rel_weights_path(model_path),
            hinted=serve_image,
        )
        return resolve_inference_url(
            gpu_type=gpu_type,
            model_path=model_path,
            model_name=model_name,
            max_model_len=max_model_len,
            environment=MODAL_ENVIRONMENT,
            serve_image=image,
        )

    @modal.method()
    def register_model(
        self,
        *,
        model_id: str,
        weights_path: str,
        max_model_len: int,
        gpu_type: str,
        tokenizer_name: str = "",
    ) -> str:
        """Mint the parametrized worker URL. Caller persists it in Postgres."""
        return self._resolve_worker_url(
            gpu_type=gpu_type,
            model_path=weights_path,
            model_name=model_id,
            max_model_len=max_model_len,
            serve_image=serve_image_key(tokenizer_name, model_id),
        )

    @modal.method()
    def delete_model(self, *, model_id: str, weights_path: str = "") -> None:  # noqa: ARG002
        import shutil

        if not weights_path:
            return
        dest = Path(weights_path)
        if dest.exists() and dest.is_relative_to(Path(WEIGHTS_MOUNT)):
            shutil.rmtree(dest, ignore_errors=True)
            weights_vol.commit()

    @modal.asgi_app()
    def api(self):
        import shutil

        from fastapi import Depends, FastAPI, HTTPException, Request
        from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
        from pydantic import BaseModel
        from starlette.responses import StreamingResponse

        api_key = os.environ.get("INFERENCE_API_KEY", "")

        def _verify(credentials: HTTPAuthorizationCredentials = Depends(HTTPBearer())):
            if api_key and credentials.credentials != api_key:
                raise HTTPException(status_code=403, detail="Invalid API key")

        fastapi_app = FastAPI(dependencies=[Depends(_verify)])

        class RegisterRequest(BaseModel):
            model_id: str
            weights_path: str
            max_model_len: int
            gpu_type: str
            tokenizer_name: str = ""

        @fastapi_app.get("/health")
        async def health():
            return {"status": "ok"}

        @fastapi_app.post("/models/register")
        async def register(req: RegisterRequest):
            url = self._resolve_worker_url(
                gpu_type=req.gpu_type,
                model_path=req.weights_path,
                model_name=req.model_id,
                max_model_len=req.max_model_len,
                serve_image=serve_image_key(req.tokenizer_name, req.model_id),
            )
            return {"model_id": req.model_id, "inference_url": url}

        @fastapi_app.delete("/models/{model_id}")
        async def delete(model_id: str, request: Request):
            routing = parse_routing_headers(dict(request.headers))
            weights_path = request.headers.get(WEIGHTS_PATH_HEADER, "") or (routing or {}).get(
                "weights_path", ""
            )
            if not weights_path:
                raise HTTPException(
                    status_code=400,
                    detail=f"Missing {WEIGHTS_PATH_HEADER} — Postgres holds the path",
                )
            dest = Path(weights_path)
            if dest.exists() and dest.is_relative_to(Path(WEIGHTS_MOUNT)):
                shutil.rmtree(dest, ignore_errors=True)
                weights_vol.commit()
            return {"deleted": model_id}

        # Proxies over Modal RPC, not HTTP — see ``_BaseVLLMWorker.infer``.
        @fastapi_app.api_route("/v1/{path:path}", methods=["GET", "POST"])
        async def proxy_inference(path: str, request: Request):
            model_id = None
            try:
                body_json = await request.json()
                model_id = body_json.get("model")
            except Exception:
                pass

            if not model_id:
                raise HTTPException(
                    status_code=400, detail="Could not determine model from request body"
                )

            routing = parse_routing_headers(dict(request.headers))
            if routing is None:
                raise HTTPException(
                    status_code=400,
                    detail="Missing routing headers (gpu type, weights path, max_model_len)",
                )

            rel_path = rel_weights_path(routing["weights_path"])
            serve_image = _serve_image_for(
                model_name=model_id,
                model_path=rel_path,
                hinted=routing.get("serve_image") or "",
            )
            # Shared-base mode: the pool is keyed by the base, so it is named after the base
            # rather than this model. Naming it after the model would give every adapter its
            # own pool and its own cold start, which is the thing this avoids.
            adapter_rel = rel_weights_path(routing.get("adapter_path") or "")
            adapter = (model_id, adapter_rel) if adapter_rel else None
            try:
                worker, cls_name = _make_worker(
                    gpu_type=routing["gpu_type"],
                    model_path=rel_path,
                    model_name=base_pool_name(rel_path) if adapter else model_id,
                    max_model_len=routing["max_model_len"],
                    serve_image=serve_image,
                    enable_lora=bool(adapter),
                    max_lora_rank=routing.get("lora_rank") or 16,
                )
            except ValueError as e:
                raise HTTPException(status_code=400, detail=str(e)) from e

            body = await request.body()
            _skip_hdr = {
                "host",
                "content-length",
                GPU_TYPE_HEADER.lower(),
                WEIGHTS_PATH_HEADER.lower(),
                MAX_MODEL_LEN_HEADER.lower(),
                SERVE_IMAGE_HEADER.lower(),
                ADAPTER_PATH_HEADER.lower(),
                LORA_RANK_HEADER.lower(),
            }
            headers = {k: v for k, v in request.headers.items() if k.lower() not in _skip_hdr}
            print(
                f"[proxy-rpc] {model_id} → {cls_name}(model_path={rel_path}"
                f"{f', adapter={adapter_rel}' if adapter else ''})"
            )

            stream = _wants_stream(body)

            async def chunks():
                kwargs = dict(
                    method=request.method,
                    path=f"/v1/{path}",
                    body=body,
                    headers=headers,
                    adapter=adapter,
                )
                if stream:
                    async for chunk in worker.infer_stream.remote_gen.aio(**kwargs):
                        yield chunk
                else:
                    result = await worker.infer.remote.aio(**kwargs)
                    yield {"status": result["status"], "headers": result["headers"]}
                    yield result["body"]

            return StreamingResponse(
                stream_with_keepalive(chunks(), sse=stream),
                headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
                media_type="text/event-stream" if stream else "application/json",
            )

        return fastapi_app


@app.function(
    image=api_server_image,
    volumes={WEIGHTS_MOUNT: weights_vol},
    timeout=5 * MINUTES,
)
def cleanup_job_weights(*, job_id: str) -> list[str]:
    """Delete one job's final + staging dirs so register re-runs download+FP8."""
    import shutil

    removed = []
    for path in (
        Path(WEIGHTS_MOUNT) / job_id,
        Path(WEIGHTS_MOUNT) / ".staging" / job_id,
    ):
        if path.exists():
            shutil.rmtree(path)
            removed.append(str(path))
    if removed:
        weights_vol.commit()
    return removed


@app.function(
    image=api_server_image,
    # One cold start, which is ~30 min at the 72B end.
    timeout=45 * MINUTES,
    volumes={WEIGHTS_MOUNT: weights_vol},
)
def pre_warm(
    *,
    model_id: str,
    weights_path: str,
    gpu_type: str = "L4",
    max_model_len: int = 8192,
    adapter: tuple[str, str] | None = None,
    lora_rank: int = 0,
) -> None:
    """Boots the deployment once and leaves the container running. CPU-only, driving the GPU
    worker over Modal RPC.

    The single dummy call proves the checkpoint actually serves before the deployment is marked
    READY. The GPU worker commits ``overmind-vllm-cache`` after that boot so later cold starts
    reuse torch.compile artifacts (CUDA graphs still recapture). The container is left up —
    the scaledown window reclaims it — because a warm container is what makes the next request
    fast.

    For an adapter, ``weights_path`` is the shared base and the call additionally proves the
    adapter loads onto it. That usually costs seconds rather than a boot, because the base pool
    is often already serving another adapter.
    """
    rel_path = rel_weights_path(weights_path)
    serve_image = _serve_image_for(model_name=model_id, model_path=rel_path)
    worker, _cls_name = _make_worker(
        gpu_type=gpu_type,
        model_path=rel_path,
        model_name=base_pool_name(rel_path) if adapter else model_id,
        max_model_len=max_model_len,
        serve_image=serve_image,
        enable_lora=bool(adapter),
        max_lora_rank=lora_rank or 16,
    )
    body = json.dumps(
        {
            "model": model_id,
            "messages": [{"role": "user", "content": "ping"}],
            "max_tokens": 1,
        }
    ).encode()
    started = time.monotonic()
    print(f"[pre_warm] {model_id} — boot + dummy request...")
    result = worker.infer.remote(
        method="POST",
        path="/v1/chat/completions",
        body=body,
        headers={"content-type": "application/json"},
        adapter=tuple(adapter) if adapter else None,
    )
    status = result["status"]
    print(f"[pre_warm] status={status} in {time.monotonic() - started:.0f}s")
    if status not in (200, 201):
        raise RuntimeError(f"pre_warm inference failed: {status} {result['body'][:300]!r}")
    print(f"[pre_warm] {model_id} done — leaving {gpu_type} container warm")
