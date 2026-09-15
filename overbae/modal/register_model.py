"""Download fine-tune checkpoints (Nebius, Baseten, or Modal), merge LoRA, and FP8-quantize for
serving. ``quantize=False`` skips FP8 and serves merged BF16 — see
``weight_ops.quantize_checkpoint`` for which models need that.

A dense LoRA finetune takes the other path: ``publish_adapter`` copies the adapter out untouched
and it is served on top of a shared base, which skips merge and quantize entirely and lets the
deployment join an already-warm container pool. That base must stay BF16 — a BF16 adapter over an
FP8 base measured 0.234 exact-match against 0.474 for the same adapter merged then quantized,
while the same adapter over a BF16 base scored 0.484.

Storage layout:
  /weights/{job_id}/                   final weights
  /weights/{job_id}/.meta.json         ready marker + serve metadata
  /weights/.staging/{job_id}/          raw download; gone once the serving copy is ready
  /weights/.base_models/{org--model}/  shared HF bases: LoRA merge source and serving base
  /weights/.adapters/{job_id}/         LoRA adapters served on a shared base

S3 (baseten/modal only), the durable provider-independent home the UI's checkpoint-download
button reads from. Archiving is idempotent, and a metadata-only zip counts as missing so the
next deploy retries:
  {user_id}/{finetuning_job_id}/checkpoints/checkpoint.zip
  {user_id}/{finetuning_job_id}/job_logs.txt
  {user_id}/{finetuning_job_id}/metrics.json

Modal secrets, under secret name "overmind-inference":
  INFERENCE_API_KEY     — bearer token for RegisterAPIServer HTTP endpoints
  TOGETHER_API_KEY      — Together SDK checkpoint download + job validation
  NEBIUS_API_KEY        — Nebius download; MUST be the key that submitted the job
  BASETEN_API_KEY       — Baseten checkpoint download
  HF_TOKEN              — optional, needed for gated base models during merge
  AWS_ACCESS_KEY_ID     — S3 checkpoint archive (CloudBucketMount)
  AWS_SECRET_ACCESS_KEY — S3 checkpoint archive (CloudBucketMount)

Deploy with MODAL_ENVIRONMENT set, or the wrong bucket is wired into CloudBucketMount:
  MODAL_ENVIRONMENT=overmind-dev  modal deploy overbae/modal/register_model.py --env overmind-dev
  MODAL_ENVIRONMENT=overmind-prod modal deploy overbae/modal/register_model.py --env overmind-prod
"""

from __future__ import annotations

import json
import os
import re
import shutil
import time
import zipfile
from pathlib import Path

import modal

WEIGHTS_MOUNT = "/weights"
STAGING_DIRNAME = ".staging"
ADAPTERS_DIRNAME = ".adapters"
SFT_MOUNT = "/sft"

weights_vol = modal.Volume.from_name("overmind-weights", create_if_missing=True)
# The training Volume, mounted here so a run's checkpoint/logs/metrics can be read
# straight off it — both apps live in the same Modal account, so no download.
sft_vol = modal.Volume.from_name("overmind-sft", create_if_missing=True)

_WEIGHT_OPS_LOCAL = Path(__file__).parent / "weight_ops.py"

# The bucket name is not secret, so it is picked from MODAL_ENVIRONMENT at deploy time
# and CloudBucketMount is wired without shell env. Credentials still come from the
# Modal secret at container runtime.
S3_MOUNT = "/s3"
_S3_BUCKETS = {
    "overmind-dev": "overmind-finetuning-dev-62hdauj",
    "overmind-staging": "overmind-finetuning-staging-xmpmbnsw",
    "overmind-prod": "overmind-finetuning-prod-cmuziwbk",
}
MODAL_ENVIRONMENT = os.environ.get("MODAL_ENVIRONMENT", "overmind-dev")
S3_BUCKET_NAME = _S3_BUCKETS[MODAL_ENVIRONMENT]
aws_secret = modal.Secret.from_name("overmind-inference")


def _s3_mount(*, read_only: bool) -> modal.CloudBucketMount:
    return modal.CloudBucketMount(S3_BUCKET_NAME, secret=aws_secret, read_only=read_only)


def _s3_prefix(user_id: str, job_id: str) -> Path:
    return Path(S3_MOUNT) / (user_id or "unknown") / job_id


def _zip_directory(src_dir: Path, zip_path: Path) -> None:
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path, mode="w", compression=zipfile.ZIP_STORED) as zf:
        for f in sorted(src_dir.rglob("*")):
            if f.is_file():
                zf.write(f, arcname=str(f.relative_to(src_dir)))


def _checkpoint_step(p: Path) -> int:
    m = re.search(r"(\d+)$", p.name)
    return int(m.group(1)) if m else -1


def _name_is_weight(name: str) -> bool:
    """True for LoRA adapter or full-SFT weight filenames (basename only)."""
    base = Path(name).name
    if base in ("adapter_model.safetensors", "pytorch_model.bin"):
        return True
    if base.startswith("model") and base.endswith(".safetensors"):
        return True
    return base.startswith("pytorch_model") and base.endswith(".bin")


def _dir_has_weights(d: Path) -> bool:
    """True if `d` itself has an actual weight file, not just a config."""
    return any(p.is_file() and _name_is_weight(p.name) for p in d.iterdir())


def _zip_has_weights(zip_path: Path) -> bool:
    """True if ``checkpoint.zip`` contains a real weight file, not just config."""
    try:
        with zipfile.ZipFile(zip_path) as zf:
            return any(_name_is_weight(info.filename) for info in zf.infolist())
    except (OSError, zipfile.BadZipFile):
        return False


BASE_MODELS_DIRNAME = ".base_models"


def _base_model_dir(repo_id: str) -> Path:
    return Path(WEIGHTS_MOUNT) / BASE_MODELS_DIRNAME / repo_id.replace("/", "--")


def _snapshot_is_complete(base_dir: Path) -> bool:
    """A present ``config.json`` proves nothing — ``snapshot_download`` fetches small files first,
    so an interrupted download leaves one behind with shards missing, and the next job merges LoRA
    into an incomplete base. Where a safetensors index exists it is authoritative: every shard in
    ``weight_map`` must exist and be non-empty."""
    if not (base_dir / "config.json").exists():
        return False
    # huggingface_hub streams into *.incomplete next to its .cache dir.
    if any(base_dir.rglob("*.incomplete")):
        return False
    index = base_dir / "model.safetensors.index.json"
    if index.exists():
        try:
            weight_map = json.loads(index.read_text()).get("weight_map") or {}
        except (json.JSONDecodeError, OSError):
            return False
        shards = set(weight_map.values())
        if not shards:
            return False
        return all((base_dir / s).is_file() and (base_dir / s).stat().st_size > 0 for s in shards)
    return _dir_has_weights(base_dir)


def _is_quantized_hf_repo(repo_id: str) -> bool:
    """True for quantized/remapped repos that cannot be LoRA-merge bases.

    Official ``openai/gpt-oss-*`` ships MXFP4 (blocks/scales) — merging LoRA into
    that layout produces checkpoints vLLM cannot load. Prefer the catalog BF16 id.
    """
    low = (repo_id or "").lower()
    if any(tok in low for tok in ("bnb-4bit", "bnb-8bit", "-gptq", "-awq", "fp8-dynamic", "mxfp4")):
        return True
    return low.startswith("openai/gpt-oss")


def _resolve_merge_base(adapter_base: str, preferred: str) -> str:
    """Prefers the job's catalog id when the adapter points at a quantized remapped repo: Unsloth
    QLoRA writes unsloth/…-bnb-4bit into adapter_config, and merging into packed 4-bit weights
    raises shape mismatches."""
    preferred = (preferred or "").strip()
    adapter_base = (adapter_base or "").strip()
    if preferred and (not adapter_base or _is_quantized_hf_repo(adapter_base)):
        return preferred
    return adapter_base or preferred


def _resolve_staged_base(
    *,
    staging: Path,
    model_id: str,
    merge_base_model: str,
    log_prefix: str,
) -> dict:
    """Shared by both staging paths, direct-from-sft-volume and via-S3, so the
    ``.download_meta.json`` contract prepare_fp8 reads stays identical."""
    adapter_cfg_path = staging / "adapter_config.json"
    is_lora = adapter_cfg_path.exists()
    base_model = ""
    base_model_path = None

    if is_lora:
        adapter_cfg = json.loads(adapter_cfg_path.read_text())
        adapter_base = adapter_cfg.get("base_model_name_or_path", "") or ""
        base_model = _resolve_merge_base(adapter_base, merge_base_model or model_id)
        if base_model != adapter_base:
            print(f"[{log_prefix}] adapter base {adapter_base!r} → merge base {base_model!r}")
            adapter_cfg["base_model_name_or_path"] = base_model
            adapter_cfg_path.write_text(json.dumps(adapter_cfg, indent=2) + "\n")
        base_model_path = fetch_base_model.remote(base_model=base_model)["base_model_path"]
        print(f"[{log_prefix}] LoRA adapter staged — will merge+FP8 on GPU")
    else:
        # Full SFT still needs the HF base: to fill frozen multimodal weights Unsloth
        # omitted, and to stamp base_model into .meta so vLLM can apply
        # --language-model-only for a text-only FT artifact.
        base_model = (merge_base_model or model_id or "").strip()
        if base_model:
            base_model_path = fetch_base_model.remote(base_model=base_model)["base_model_path"]
            print(
                f"[{log_prefix}] Full SFT staged — will overlay onto base {base_model!r} then FP8"
            )
        else:
            print(f"[{log_prefix}] Full SFT checkpoint staged — will FP8 on GPU (no base)")

    return {
        "staging_path": str(staging),
        "quantization": "none",
        "model_id": model_id,
        "base_model": base_model,
        "is_lora": is_lora,
        "base_model_path": base_model_path,
        "stage": "downloaded",
        "ready": False,
    }


def resolve_artifact_dir(staging: Path) -> tuple[Path, bool]:
    """Returns ``(dir, is_lora)``. Baseten produces two real layouts: the final artifact at the
    staging ROOT, or ONLY nested ``checkpoint-N/`` dirs when the root save did not sync, in which
    case the artifact is the highest-step checkpoint. Handing the staging root to AutoConfig in
    the nested case raises "Unrecognized model".

    A config file alone does NOT make a directory the artifact: Baseten's listing can surface
    config.json before the larger weight files finish syncing, so a directory is trusted only once
    it also holds a weight file.
    """
    if (staging / "adapter_config.json").exists() and _dir_has_weights(staging):
        return staging, True
    if (staging / "config.json").exists() and _dir_has_weights(staging):
        return staging, False
    candidates = [
        d
        for d in staging.iterdir()
        if d.is_dir()
        and ((d / "adapter_config.json").exists() or (d / "config.json").exists())
        and _dir_has_weights(d)
    ]
    if candidates:
        best = max(candidates, key=_checkpoint_step)
        return best, (best / "adapter_config.json").exists()
    raise FileNotFoundError(
        f"No model artifact with weights in {staging}: expected adapter_config.json or "
        f"config.json plus a weight file at the root or inside a checkpoint-N/ subdir"
    )


download_image = (
    modal.Image.debian_slim(python_version="3.11")
    .pip_install(
        "openai>=1.0.0",
        "huggingface_hub>=0.27.0",
        "requests>=2.32.0",
    )
    # Saturates NIC + CPU for hf_xet transfers, but it is NOT the main cost: writing
    # into the weights Volume runs ~131 MB/s effective, which is what puts a 145 GB
    # base near 20 min. Prefetching during training is the fix that matters there.
    .env({"HF_XET_HIGH_PERFORMANCE": "1"})
)

# L40S fits ≤~14B BF16 during llmcompressor load (~2× weight VRAM).
quantize_image = (
    modal.Image.from_registry(
        "nvidia/cuda:12.8.0-devel-ubuntu22.04",
        add_python="3.11",
    )
    .apt_install("git", "curl")
    # llmcompressor>=0.12 requires torch>=2.10; pin the lowest satisfying cu128
    # build up front so nothing later in the chain silently reinstalls torch
    # and invalidates the causal-conv1d/mamba-ssm extensions built below.
    .pip_install(
        "torch==2.10.0",
        index_url="https://download.pytorch.org/whl/cu128",
    )
    .pip_install(
        "transformers>=5.10.2",
        "peft>=0.17.0",
        "accelerate>=1.1.0",
        "safetensors>=0.4.5",
        "sentencepiece",
        "protobuf",
        "wheel",
        "setuptools",
        "ninja",
        "packaging",
    )
    .pip_install(
        "llmcompressor>=0.12.0",
        "compressed-tensors>=0.9.0",
    )
    .pip_install(
        "mamba-ssm>=2.2.0",
        "causal-conv1d>=1.4.0",
        extra_options="--no-build-isolation",
    )
    # Container-local. fetch_base_model passes local_dir=, which writes straight to
    # .base_models/ and never populates the blob cache, so all this holds is xet's download
    # scratch — putting that on the shared Volume only grew it and let containers race.
    .env({"HF_HOME": "/tmp/hf_cache", "PYTHONPATH": "/root"})
    .add_local_file(str(_WEIGHT_OPS_LOCAL), remote_path="/root/weight_ops.py", copy=True)
)
# modal_shared lives outside overbae on purpose — see modal_sft_worker.py.
from modal_shared.images import attach_modelfam  # noqa: E402

# Every image in this file needs modal_shared attached, even ones whose
# functions never call resolve(): Modal re-imports this whole entrypoint file
# to reconstruct any function/class in it, regardless of which image backs it.
download_image = attach_modelfam(download_image)
quantize_image = attach_modelfam(quantize_image)

api_image = attach_modelfam(
    modal.Image.debian_slim(python_version="3.11").pip_install(
        "fastapi>=0.100.0",
        "openai>=1.0.0",
    )
)
inference_secret = modal.Secret.from_name("overmind-inference")

app = modal.App("overmind-register")


# Abandoned unzip with no serving copy (probes, crashed deploys). In-progress
# merge/quantize is hours at the high end, not a day.
_STAGING_ORPHAN_S = 24 * 60 * 60


def _staging_dir(job_id: str) -> Path:
    return Path(WEIGHTS_MOUNT) / STAGING_DIRNAME / job_id


def _drop_staging(job_id: str) -> bool:
    staging = _staging_dir(job_id)
    if not staging.exists():
        return False
    shutil.rmtree(staging, ignore_errors=True)
    return True


def _tree_mtime(path: Path) -> float:
    newest = path.stat().st_mtime
    for p in path.rglob("*"):
        try:
            newest = max(newest, p.stat().st_mtime)
        except OSError:
            continue
    return newest


def spent_staging_names(
    weights: Path, *, now: float | None = None, max_age_s: float = _STAGING_ORPHAN_S
) -> list[str]:
    """Staging keys that already have a serving copy, or that sat unused past max_age_s."""
    staging = weights / STAGING_DIRNAME
    if not staging.is_dir():
        return []
    now = time.time() if now is None else now
    names: list[str] = []
    for child in staging.iterdir():
        if not child.is_dir():
            continue
        key = child.name
        adapter_ready = (weights / ADAPTERS_DIRNAME / key / ".meta.json").is_file()
        merged = _read_json(weights / key / ".meta.json") or {}
        if adapter_ready or merged.get("ready"):
            names.append(key)
            continue
        if now - _tree_mtime(child) >= max_age_s:
            names.append(key)
    return names


def _weights_dir(job_id: str) -> Path:
    return Path(WEIGHTS_MOUNT) / job_id


def _read_json(path: Path) -> dict | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return None


@app.cls(
    image=api_image,
    secrets=[inference_secret],
    volumes={WEIGHTS_MOUNT: weights_vol},
    scaledown_window=60,
    timeout=4 * 60 * 60,
    cpu=0.25,
)
@modal.concurrent(max_inputs=50)
class RegisterAPIServer:
    """CPU gateway: cache check → Baseten download → GPU merge/quantize."""

    @modal.method()
    def register(
        self,
        *,
        remote_job_id: str,
        model_id: str,
        provider: str = "baseten",
        baseten_api_key: str = "",
        user_id: str = "",
        job_id: str = "",
        job_result: dict | None = None,
        quantize: bool = True,
        merge_base_model: str = "",
        params_b: float = 0.0,
    ) -> dict:
        """Runs download + quantize (or copy) on a cache miss.

        ``user_id``/``job_id``/``job_result`` apply only to the baseten and modal providers, where
        they address the S3 archive. ``quantize`` carries models.json's ``fp8_supported``.
        ``merge_base_model`` is the catalog HF id, used when adapter_config points at a quantized
        remapped repo. ``params_b`` only picks the merge/quantize GPU tier.
        """
        if provider not in ("nebius", "baseten", "modal"):
            raise ValueError(
                f"Provider {provider!r} is not supported. Use 'nebius', 'baseten', or 'modal'."
            )
        if provider in ("baseten", "modal") and not job_id:
            raise ValueError(
                f"job_id is required for provider={provider!r} (S3 archive addressing)"
            )

        weights_vol.reload()

        # Cache key: Baseten remote_id is "project_id:job_id", stable part LAST; Modal
        # remote_id is "run_id:function_call_id", stable part FIRST, because a
        # FunctionCall id is not reusable across retries. This is the FP8-cache key,
        # independent of the S3 archive's {user_id}/{job_id} namespace.
        if provider == "baseten":
            cache_key = remote_job_id.split(":")[-1]
        elif provider == "modal":
            cache_key = remote_job_id.split(":")[0]
        else:
            cache_key = remote_job_id
        expected_quant = "fp8" if quantize else "bf16"
        weights_dir = _weights_dir(cache_key)
        meta_file = weights_dir / ".meta.json"
        cached = _read_json(meta_file)
        if cached and cached.get("ready") and cached.get("quantization") == expected_quant:
            print(f"[RegisterAPI] {cache_key} already ready ({expected_quant}) — skipping")
            return cached

        staging = _staging_dir(cache_key)
        download_meta = _read_json(staging / ".download_meta.json")

        def _dispatch_download() -> dict:
            if provider == "baseten":
                key = baseten_api_key or os.environ.get("BASETEN_API_KEY", "")
                print(f"[RegisterAPI] Archiving {remote_job_id} (Baseten) to S3")
                sync_baseten_checkpoint_to_s3.remote(
                    remote_id=remote_job_id,
                    user_id=user_id,
                    job_id=job_id,
                    baseten_api_key=key,
                    job_result=job_result or {},
                )
                print(f"[RegisterAPI] Pulling {job_id} checkpoint back from S3")
                return download_checkpoint_from_s3.remote(
                    user_id=user_id,
                    job_id=job_id,
                    model_id=model_id,
                    cache_key=cache_key,
                    merge_base_model=merge_base_model,
                )
            if provider == "modal":
                # Training wrote the checkpoint to a Volume in this same Modal account, so stage
                # it volume→volume rather than gating the deploy on zip→upload→download→unzip.
                # The S3 archive that backs user download is dispatched by the caller, because
                # the adapter path never reaches this function.
                print(f"[RegisterAPI] Staging {remote_job_id} straight off the sft volume")
                staged = stage_modal_checkpoint.remote(
                    remote_id=remote_job_id,
                    model_id=model_id,
                    cache_key=cache_key,
                    merge_base_model=merge_base_model,
                )
                if not staged.get("final_missing"):
                    return staged
                # Retention drops final/ once the deploy has landed, so a rebuild — the deployment
                # was deleted, or fp8_supported flipped and invalidated the cache — finds nothing
                # on the volume. Rare enough that the round trip costs nothing that matters.
                print(
                    f"[RegisterAPI] {job_id} checkpoint pruned from the volume — restoring from S3"
                )
                return download_checkpoint_from_s3.remote(
                    user_id=user_id,
                    job_id=job_id,
                    model_id=model_id,
                    cache_key=cache_key,
                    merge_base_model=merge_base_model,
                )

            from openai import OpenAI  # noqa: PLC0415

            nebius_api_key = os.environ.get("NEBIUS_API_KEY", "")
            if not nebius_api_key:
                raise RuntimeError(
                    "NEBIUS_API_KEY is not set in the Modal overmind-inference secret"
                )
            nebius_client = OpenAI(
                base_url="https://api.tokenfactory.nebius.com/v1/",
                api_key=nebius_api_key,
            )
            nebius_job = nebius_client.fine_tuning.jobs.retrieve(remote_job_id)
            if str(getattr(nebius_job, "status", "")).lower() not in ("succeeded", "completed"):
                raise ValueError(
                    f"Job {remote_job_id} is not completed (status={nebius_job.status})"
                )
            print(f"[RegisterAPI] Dispatching {remote_job_id} to Nebius download worker")
            return download_nebius_checkpoint.remote(nebius_job_id=remote_job_id, model_id=model_id)

        if not download_meta:
            download_meta = _dispatch_download()

        # Re-detect is_lora from the staging content: a cached .download_meta.json can
        # have been written by an older code version.
        is_lora = bool(download_meta.get("is_lora"))
        base_model_path = download_meta.get("base_model_path")
        # A full SFT is quantized in place off the sft Volume, so staging holds only the metadata
        # file and the weights to verify live at source_dir instead.
        artifact = Path(download_meta.get("source_dir") or staging)
        if artifact.exists():
            is_lora_actual = (artifact / "adapter_config.json").exists()
            # Weight presence is re-verified too, not just is_lora/base_model_path: a
            # "downloaded" meta can exist with the config staged and the larger weight
            # file never landed. This check sits ABOVE the download worker's own
            # cache-check, which is why the worker's guard alone is not enough.
            weights_actual = (
                (artifact / "adapter_model.safetensors").exists()
                if is_lora_actual
                else any(artifact.glob("model*.safetensors"))
                or (artifact / "pytorch_model.bin").exists()
            )
            cached_base = download_meta.get("base_model") or ""
            # Unsloth QLoRA can stage a bnb-4bit remapped base; merge needs bf16.
            needs_bf16_rebase = bool(merge_base_model) and _is_quantized_hf_repo(cached_base)
            meta_is_stale = (
                (is_lora_actual != is_lora)
                or not weights_actual
                or (is_lora_actual and (not base_model_path or not Path(base_model_path).exists()))
                or needs_bf16_rebase
            )
            if meta_is_stale:
                print(
                    f"[RegisterAPI] Stale download meta for {cache_key} "
                    f"(is_lora: {is_lora} → {is_lora_actual}, base_model_path: {base_model_path!r}, "
                    f"base_model: {cached_base!r}, merge_base: {merge_base_model!r}) "
                    f"— re-dispatching download to fix"
                )
                download_meta = _dispatch_download()
                is_lora = bool(download_meta.get("is_lora"))
                base_model_path = download_meta.get("base_model_path")

        fn, tier = _prepare_fp8_fn(params_b)
        print(
            f"[RegisterAPI] Dispatching {cache_key} to {tier} "
            f"{'FP8' if quantize else 'BF16 (no quant)'} worker "
            f"(is_lora={is_lora}, params_b={params_b or '?'})"
        )
        return fn.remote(
            job_id=cache_key,
            model_id=model_id,
            is_lora=is_lora,
            base_model_path=base_model_path,
            base_model=download_meta.get("base_model", ""),
            quantize=quantize,
            source_dir=download_meta.get("source_dir") or "",
        )

    @modal.method()
    def register_base(
        self,
        *,
        base_model: str,
        model_id: str,
        quantize: bool = True,
        params_b: float = 0.0,
    ) -> dict:
        """Prepares an untuned HF base for baseline-eval serving, keyed on ``model_id`` (already a
        ``base--…`` slug from Django) and running the same merge/quantize path with
        ``is_lora=False``."""
        weights_vol.reload()
        expected_quant = "fp8" if quantize else "bf16"
        weights_dir = _weights_dir(model_id)
        meta_file = weights_dir / ".meta.json"
        cached = _read_json(meta_file)
        if cached and cached.get("ready") and cached.get("quantization") == expected_quant:
            print(f"[RegisterAPI] base {model_id} already ready ({expected_quant}) — skipping")
            return cached

        print(f"[RegisterAPI] Fetching base {base_model} for {model_id}")
        fetch = fetch_base_model.remote(base_model=base_model)
        base_path = Path(fetch["base_model_path"])

        staging = _staging_dir(model_id)
        if staging.exists():
            shutil.rmtree(staging)
        staging.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(base_path, staging)
        (staging / ".download_meta.json").write_text(
            json.dumps(
                {
                    "is_lora": False,
                    "base_model": base_model,
                    "base_model_path": str(base_path),
                }
            )
        )
        weights_vol.commit()

        fn, tier = _prepare_fp8_fn(params_b)
        print(f"[RegisterAPI] Dispatching base {model_id} to {tier} worker")
        return fn.remote(
            job_id=model_id,
            model_id=model_id,
            is_lora=False,
            base_model_path=None,
            base_model=base_model,
            quantize=quantize,
        )

    @modal.asgi_app()
    def api(self):
        from fastapi import Depends, FastAPI, HTTPException
        from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
        from pydantic import BaseModel

        web = FastAPI(title="Overmind Register API")
        bearer = HTTPBearer()

        def _auth(creds: HTTPAuthorizationCredentials = Depends(bearer)):
            if creds.credentials != os.environ.get("INFERENCE_API_KEY", ""):
                raise HTTPException(status_code=401, detail="Unauthorized")

        class RegisterRequest(BaseModel):
            remote_job_id: str
            model_id: str
            provider: str = "baseten"
            baseten_api_key: str = ""
            user_id: str = ""
            job_id: str = ""
            job_result: dict | None = None
            quantize: bool = True

        @web.get("/health")
        def health():
            return {"status": "ok"}

        @web.post("/register", dependencies=[Depends(_auth)])
        def register(req: RegisterRequest):
            return self.register.local(
                remote_job_id=req.remote_job_id,
                model_id=req.model_id,
                provider=req.provider,
                baseten_api_key=req.baseten_api_key,
                user_id=req.user_id,
                job_id=req.job_id,
                job_result=req.job_result,
                quantize=req.quantize,
            )

        @web.get("/jobs/{remote_job_id}", dependencies=[Depends(_auth)])
        def get_job(remote_job_id: str):
            meta = _read_json(_weights_dir(remote_job_id) / ".meta.json")
            if not meta:
                raise HTTPException(status_code=404, detail="Job not found")
            return meta

        return web


def _fetch_baseten_logs_text(project_id: str, job_id: str, headers: dict, api_base: str) -> str:
    """Same pagination shape as ``BasetenRunner._fetch_logs``, but standalone: this module has no
    Django import and calls Baseten's API directly."""
    import requests  # noqa: PLC0415

    lines: list[str] = []
    cursor_ms = 0
    last_ns = -1
    for _ in range(30):
        resp = requests.get(
            f"{api_base}/v1/training_projects/{project_id}/jobs/{job_id}/logs",
            headers=headers,
            params={"limit": 1000, "direction": "asc", "start_epoch_millis": cursor_ms},
            timeout=30,
        )
        resp.raise_for_status()
        page = resp.json().get("logs", [])
        fresh = (
            [e for e in page if int(e.get("timestamp") or 0) > last_ns] if last_ns >= 0 else page
        )
        for entry in fresh:
            lines.append(str(entry.get("message") or "").rstrip("\n"))
        if len(page) < 1000 or (last_ns >= 0 and not fresh):
            break
        last_ns = int(page[-1].get("timestamp") or 0)
        cursor_ms = last_ns // 1_000_000
    return "\n".join(lines)


def _archive_to_s3(
    user_id: str, job_id: str, local_zip: Path, log_text: str, metrics: dict
) -> None:
    """Caller must be a Function whose volumes include ``S3_MOUNT: _s3_mount(read_only=False)``."""
    prefix = _s3_prefix(user_id, job_id)
    ckpt_dest = prefix / "checkpoints" / "checkpoint.zip"
    ckpt_dest.parent.mkdir(parents=True, exist_ok=True)
    # NOT shutil.copy2: its copystat() calls os.utime, which CloudBucketMount's
    # S3-backed filesystem rejects with "Operation not permitted".
    shutil.copyfile(local_zip, ckpt_dest)
    (prefix / "job_logs.txt").write_text(log_text)
    (prefix / "metrics.json").write_text(json.dumps(metrics, indent=2))
    print(f"[S3] Archived checkpoint + logs + metrics → {prefix}")


def _s3_checkpoint_exists(user_id: str, job_id: str) -> bool:
    """A metadata-only zip counts as MISSING, so a deploy retries the archive instead of skipping
    it forever: an archive can be written before the provider finishes syncing the weights."""
    zip_path = _s3_prefix(user_id, job_id) / "checkpoints" / "checkpoint.zip"
    return zip_path.exists() and zip_path.stat().st_size > 0 and _zip_has_weights(zip_path)


@app.function(
    image=download_image,
    timeout=2 * 60 * 60,
    scaledown_window=2,
    volumes={S3_MOUNT: _s3_mount(read_only=False)},
    secrets=[inference_secret, aws_secret],
)
@modal.concurrent(max_inputs=1)
def sync_baseten_checkpoint_to_s3(
    *,
    remote_id: str,
    user_id: str,
    job_id: str,
    baseten_api_key: str = "",
    job_result: dict | None = None,
) -> dict:
    """``remote_id`` is ``"{project_id}:{baseten_job_id}"``; ``job_id`` is the Django FinetuningJob
    PK, the S3 addressing key and distinct from ``baseten_job_id``. Idempotent."""
    import requests  # noqa: PLC0415

    if _s3_checkpoint_exists(user_id, job_id):
        print(f"[Baseten→S3] {job_id} already archived — skipping")
        return {"job_id": job_id, "skipped": True}

    # Drop a metadata-only zip left by a mid-sync archive, or it poisons the bucket
    # next to the fresh one.
    stale = _s3_prefix(user_id, job_id) / "checkpoints" / "checkpoint.zip"
    if stale.exists():
        print(f"[Baseten→S3] {job_id} has incomplete checkpoint.zip — re-archiving")
        stale.unlink(missing_ok=True)

    baseten_api_key = baseten_api_key or os.environ.get("BASETEN_API_KEY", "")
    if not baseten_api_key:
        raise RuntimeError(
            "BASETEN_API_KEY is not set (pass it from Django settings or add to Modal overmind-inference secret)"
        )

    project_id, baseten_job_id = remote_id.split(":", 1)
    headers = {"Authorization": f"Bearer {baseten_api_key}"}
    api_base = "https://api.baseten.co"

    # TRAINING_JOB_COMPLETED alone is not enough: Baseten surfaces tiny config files
    # in the listing before the large weight shards land, so archiving mid-sync writes
    # a metadata-only zip. The sync status must be checked too.
    job_resp = requests.get(
        f"{api_base}/v1/training_projects/{project_id}/jobs/{baseten_job_id}",
        headers=headers,
        timeout=30,
    )
    job_resp.raise_for_status()
    job_data = job_resp.json().get("training_job", {})
    job_status = job_data.get("current_status", "")
    if job_status != "TRAINING_JOB_COMPLETED":
        raise ValueError(f"Baseten job {baseten_job_id} not completed (status={job_status})")
    sync_status = job_data.get("checkpoint_sync_status", "")
    if sync_status != "COMPLETED":
        raise ValueError(
            f"Baseten job {baseten_job_id} checkpoint sync not done "
            f"(checkpoint_sync_status={sync_status!r}) — retry later"
        )

    print(f"[Baseten→S3] Listing checkpoint files for {baseten_job_id}")
    all_files: list[dict] = []
    page_token = 0
    while True:
        resp = requests.get(
            f"{api_base}/v1/training_projects/{project_id}/jobs/{baseten_job_id}/checkpoint_files",
            headers=headers,
            params={"page_size": 500, "page_token": page_token},
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        all_files.extend(data.get("presigned_urls", []))
        next_token = data.get("next_page_token")
        if next_token is None:
            break
        page_token = next_token

    if not all_files:
        raise RuntimeError(f"No checkpoint files found for Baseten job {baseten_job_id}")

    # Two real artifact layouts: adapter at the listing root, or ONLY nested
    # checkpoint-N/ dirs when the root save did not sync — then pull just the
    # highest-step checkpoint, since each carries a full adapter_model.safetensors.
    # A root config file is NOT proof the root artifact is usable, so an actual root
    # weight file is required before the nested fallback is skipped.
    rel_names = [f["relative_file_name"] for f in all_files]
    root_names = {r for r in rel_names if "/" not in r}
    root_has_config = any(r in ("adapter_config.json", "config.json") for r in root_names)
    root_has_weights = any(
        n == "adapter_model.safetensors" or n == "pytorch_model.bin" or n.startswith("model")
        for n in root_names
        if n.endswith((".safetensors", ".bin"))
    )
    root_has_artifact = root_has_config and root_has_weights
    ckpt_dirs = {r.split("/")[0] for r in rel_names if r.split("/")[0].startswith("checkpoint-")}
    latest_ckpt = max(ckpt_dirs, key=lambda n: _checkpoint_step(Path(n))) if ckpt_dirs else None
    # The fallback flattens the nested dir onto the local staging root, because every
    # downstream check reads the root, not a nested checkpoint-N/.
    use_nested_ckpt = not root_has_artifact and latest_ckpt is not None
    if use_nested_ckpt:
        print(f"[Baseten→S3] Root artifact incomplete — using nested {latest_ckpt}/ instead")

    # A LoRA job needs only the adapter + tokenizer, so the large base-model shards TRL
    # may have copied are skipped. A full-SFT job's trained weights ARE the
    # model*.safetensors, so those must be pulled. Optimizer state is skipped either way.
    listing_is_lora = any(Path(r).name == "adapter_config.json" for r in rel_names)
    skip_patterns = (
        ("model.safetensors", "optimizer.pt", "model-")
        if listing_is_lora
        else ("optimizer.pt", "scheduler.pt", "rng_state")
    )
    always_download = (
        "adapter_config.json",
        "adapter_model.safetensors",
        "tokenizer_config.json",
        "special_tokens_map.json",
        "tokenizer.json",
        "tokenizer.model",
        "trainer_state.json",
    )
    print(f"[Baseten→S3] Checkpoint type: {'LoRA adapter' if listing_is_lora else 'full SFT'}")

    local_dir = Path(f"/tmp/baseten_ckpt_{job_id}")
    if local_dir.exists():
        shutil.rmtree(local_dir)
    local_dir.mkdir(parents=True, exist_ok=True)

    for file_info in all_files:
        rel = file_info["relative_file_name"]

        if use_nested_ckpt:
            prefix = f"{latest_ckpt}/"
            if not rel.startswith(prefix):
                continue  # skip root-level (incomplete) and other checkpoints
            rel = rel[len(prefix) :]  # flatten onto local root
        elif "/" not in rel:
            pass  # root-level artifact path — use as-is
        else:
            continue  # root artifact is usable; ignore other checkpoint-N/ dirs

        fname = Path(rel).name
        size_mb = file_info.get("size_bytes", 0) / 1e6

        is_critical = any(fname == a for a in always_download)
        is_large = any(fname.startswith(p) for p in skip_patterns)

        if is_large and not is_critical:
            print(f"[Baseten→S3]   (skip large) {rel}  ({size_mb:.0f} MB)")
            continue

        dest = local_dir / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        r = requests.get(file_info["url"], timeout=600, stream=True)
        r.raise_for_status()
        with dest.open("wb") as f:
            for chunk in r.iter_content(chunk_size=4 << 20):
                f.write(chunk)
        print(f"[Baseten→S3]   ✓ {rel}  ({dest.stat().st_size / 1e3:.0f} KB)")

    if not any(local_dir.iterdir()):
        raise RuntimeError(f"No files staged for Baseten job {baseten_job_id} — nothing to archive")

    # Never write a metadata-only zip: Celery retries until the listing includes real
    # weights, or the nested-checkpoint fallback finds them.
    if not _dir_has_weights(local_dir):
        raise RuntimeError(
            f"Baseten job {baseten_job_id} staged without weight files "
            f"(files: {sorted(p.name for p in local_dir.iterdir())}) — "
            "checkpoint listing incomplete, retry later"
        )

    zip_path = Path(f"/tmp/{job_id}_checkpoint.zip")
    _zip_directory(local_dir, zip_path)

    print(f"[Baseten→S3] Fetching full job log for {baseten_job_id}")
    log_text = _fetch_baseten_logs_text(project_id, baseten_job_id, headers, api_base)

    _archive_to_s3(user_id, job_id, zip_path, log_text, job_result or {})
    shutil.rmtree(local_dir, ignore_errors=True)
    zip_path.unlink(missing_ok=True)
    return {"job_id": job_id, "skipped": False}


@app.function(
    image=download_image,
    timeout=30 * 60,
    scaledown_window=2,
    volumes={SFT_MOUNT: sft_vol, S3_MOUNT: _s3_mount(read_only=False)},
    secrets=[inference_secret, aws_secret],
)
@modal.concurrent(max_inputs=1)
def sync_modal_checkpoint_to_s3(
    *, remote_id: str, user_id: str, job_id: str, job_result: dict | None = None
) -> dict:
    """``remote_id`` is ``"{run_id}:{function_call_id}"``; ``job_id`` is the Django FinetuningJob
    PK. No external download: training and this Function share a Modal account, so the checkpoint
    is read straight off the overmind-sft Volume. Idempotent."""
    if _s3_checkpoint_exists(user_id, job_id):
        print(f"[Modal→S3] {job_id} already archived — skipping")
        return {"job_id": job_id, "skipped": True}

    stale = _s3_prefix(user_id, job_id) / "checkpoints" / "checkpoint.zip"
    if stale.exists():
        print(f"[Modal→S3] {job_id} has incomplete checkpoint.zip — re-archiving")
        stale.unlink(missing_ok=True)

    run_id = remote_id.split(":", 1)[0]
    sft_vol.reload()
    run_dir = Path(SFT_MOUNT) / "runs" / run_id
    final_dir = run_dir / "final"
    if not final_dir.is_dir() or not any(final_dir.iterdir()):
        raise RuntimeError(f"Modal run {run_id!r} has no final checkpoint at {final_dir}")
    if not _dir_has_weights(final_dir):
        raise RuntimeError(
            f"Modal run {run_id!r} final checkpoint has no weight files at {final_dir}"
        )

    zip_path = Path(f"/tmp/{job_id}_checkpoint.zip")
    _zip_directory(final_dir, zip_path)

    log_path = run_dir / "train_stdout.log"
    log_text = log_path.read_text() if log_path.exists() else ""

    metrics = dict(job_result or {})
    metrics_path = run_dir / "metrics.jsonl"
    if metrics_path.exists():
        lines = [line for line in metrics_path.read_text().splitlines() if line.strip()]
        metrics.setdefault("raw_metrics_jsonl", [json.loads(line) for line in lines])

    _archive_to_s3(user_id, job_id, zip_path, log_text, metrics)
    zip_path.unlink(missing_ok=True)
    print(f"[Modal→S3] Archived {run_id} final checkpoint → job {job_id}")
    return {"job_id": job_id, "skipped": False}


@app.function(
    image=download_image,
    timeout=300,
    scaledown_window=2,
    volumes={WEIGHTS_MOUNT: weights_vol},
)
def list_base_models() -> list[str]:
    """Repo ids whose ``.base_models/`` snapshot is complete.

    The pre-warm sweep asks the Volume rather than trusting a name to mean "present", so a
    half-finished download is re-driven instead of counted as done.
    """
    weights_vol.reload()
    root = Path(WEIGHTS_MOUNT) / BASE_MODELS_DIRNAME
    if not root.is_dir():
        return []
    return sorted(
        entry.name.replace("--", "/", 1)
        for entry in root.iterdir()
        if entry.is_dir() and _snapshot_is_complete(entry)
    )


@app.function(
    image=download_image,
    timeout=4 * 60 * 60,
    scaledown_window=2,
    volumes={WEIGHTS_MOUNT: weights_vol},
    secrets=[inference_secret],
    cpu=8.0,  # HF_XET_HIGH_PERFORMANCE saturates several cores; default fraction throttles it
    max_containers=1,  # global mutex: never let two callers write one base dir
)
@modal.concurrent(max_inputs=1)
def fetch_base_model(*, base_model: str) -> dict:
    """The single choke point for base downloads: ``max_containers=1`` plus ``max_inputs=1`` make
    it a global mutex, so concurrent callers cannot corrupt a shared
    ``.base_models/{org--model}`` dir and the second caller just sees a complete snapshot.

    Django ``.spawn()``s this when a fine-tune STARTS, so a ~145 GB base downloads alongside
    training instead of adding ~25 min to the deploy. The deploy path calls the same Function, so
    a cold base is still correct, only slower.
    """
    from huggingface_hub import snapshot_download  # noqa: PLC0415

    weights_vol.reload()
    base_dir = _base_model_dir(base_model)
    if _snapshot_is_complete(base_dir):
        print(f"[base] {base_model} already cached at {base_dir}")
        return {"base_model": base_model, "base_model_path": str(base_dir), "cached": True}

    print(f"[base] Downloading {base_model} → {base_dir}")
    base_dir.mkdir(parents=True, exist_ok=True)
    snapshot_download(
        repo_id=base_model,
        local_dir=str(base_dir),
        ignore_patterns=["*.bin", "original/*", "*.pt"],
        token=os.environ.get("HF_TOKEN") or None,
        max_workers=16,
    )
    if not _snapshot_is_complete(base_dir):
        raise RuntimeError(
            f"Base snapshot for {base_model} is incomplete after download "
            f"({base_dir}) — refusing to hand a partial base to the LoRA merge"
        )
    weights_vol.commit()
    print(f"[base] {base_model} ready at {base_dir}")
    return {"base_model": base_model, "base_model_path": str(base_dir), "cached": False}


@app.function(
    image=download_image,
    timeout=30 * 60,
    scaledown_window=2,
    volumes={WEIGHTS_MOUNT: weights_vol, SFT_MOUNT: sft_vol},
    secrets=[inference_secret],
)
@modal.concurrent(max_inputs=1)
def stage_modal_checkpoint(
    *,
    remote_id: str,
    model_id: str,
    cache_key: str,
    merge_base_model: str = "",
) -> dict:
    """Training wrote the checkpoint to a Volume in this same Modal account, so the first deploy
    never goes out through S3 and back (zip → upload → download → unzip, plus two cold
    containers). The S3 archive still runs, concurrently, for the UI's download button.

    ``final_missing`` rather than a raise: retention deletes ``final/`` once the deploy has landed
    and the archive is confirmed, so an absent checkpoint is the expected state for a rebuild and
    the caller restores from S3 instead.
    """
    run_id = remote_id.split(":", 1)[0]
    sft_vol.reload()
    final_dir = Path(SFT_MOUNT) / "runs" / run_id / "final"
    if not final_dir.is_dir() or not any(final_dir.iterdir()):
        print(f"[sft→weights] {run_id} has no final checkpoint at {final_dir}")
        return {"final_missing": True}

    staging = _staging_dir(cache_key)
    if staging.exists():
        shutil.rmtree(staging)
    staging.parent.mkdir(parents=True, exist_ok=True)

    # A full SFT checkpoint is quantized straight off the sft Volume. Copying it here would
    # duplicate the entire model — the whole checkpoint, not an adapter — and prepare_fp8 rewrites
    # every tensor into a fresh directory anyway, so the copy buys nothing. Staging still gets the
    # metadata file that keys the cache. LoRA cannot take this path: _resolve_staged_base rewrites
    # adapter_config.json when the merge base differs, and the training output is not ours to edit.
    is_lora = (final_dir / "adapter_config.json").is_file()
    source = final_dir if not is_lora else staging
    if is_lora:
        shutil.copytree(final_dir, staging)
        print(f"[sft→weights] Copied {final_dir} → {staging}")
    else:
        staging.mkdir(parents=True, exist_ok=True)
        print(f"[sft→weights] Full SFT — quantizing in place from {final_dir}, no staging copy")

    result = _resolve_staged_base(
        staging=source,
        model_id=model_id,
        merge_base_model=merge_base_model,
        log_prefix="sft→weights",
    )
    if not is_lora:
        result["source_dir"] = str(final_dir)
    (staging / ".download_meta.json").write_text(json.dumps(result))
    weights_vol.commit()
    return result


def _restore_adapter_from_s3(*, run_id: str, cache_key: str, user_id: str, job_id: str) -> Path:
    """Unpack the download archive back onto the weights Volume and return the staged adapter.

    Reached only when both the training checkpoint and a previously published adapter are gone,
    which means the deployment was deleted after retention pruned ``final/``. Delegated rather
    than unzipped here so there is one S3 restore, and so this Function need not mount the bucket.
    """
    if not (user_id and job_id):
        raise RuntimeError(
            f"Modal run {run_id!r} has no checkpoint on the volume and no job identity to "
            "restore its archive with"
        )

    print(f"[adapter] {cache_key} pruned from the volume — restoring from S3")
    download_checkpoint_from_s3.remote(
        user_id=user_id, job_id=job_id, model_id=cache_key, cache_key=cache_key
    )
    weights_vol.reload()
    staging = _staging_dir(cache_key)
    if not (staging / "adapter_config.json").is_file():
        raise RuntimeError(
            f"Modal run {run_id!r} has no adapter_config.json in {staging} — "
            "not a LoRA checkpoint, so it cannot be served on a shared base"
        )
    return staging


@app.function(
    image=download_image,
    timeout=30 * 60,
    scaledown_window=2,
    volumes={WEIGHTS_MOUNT: weights_vol, SFT_MOUNT: sft_vol},
    secrets=[inference_secret],
)
@modal.concurrent(max_inputs=1)
def publish_adapter(
    *, remote_id: str, cache_key: str, base_model: str, user_id: str = "", job_id: str = ""
) -> dict:
    """Copy a LoRA adapter to ``.adapters/{cache_key}`` for serving on a shared base.

    This is the whole deploy for a dense LoRA finetune: no merge, no quantize, no per-model
    checkpoint. An adapter is tens of MB against tens of GB for a merged copy, so the copy is
    seconds rather than minutes, and the deployment then rides a base pool that may already be
    warm instead of booting its own container.
    """
    run_id = remote_id.split(":", 1)[0]
    sft_vol.reload()
    final_dir = Path(SFT_MOUNT) / "runs" / run_id / "final"

    base_dir = _base_model_dir(base_model)
    if not _snapshot_is_complete(base_dir):
        raise RuntimeError(
            f"shared base {base_model!r} is not staged at {base_dir}; "
            "fetch_base_model must complete before an adapter can be published against it"
        )

    dest = Path(WEIGHTS_MOUNT) / ADAPTERS_DIRNAME / cache_key
    source = final_dir
    if not (final_dir / "adapter_config.json").is_file():
        # Retention drops final/ once the deploy has landed and the archive is confirmed, so its
        # absence means "already published" far more often than "not a LoRA checkpoint".
        if (dest / "adapter_config.json").is_file():
            print(f"[adapter] {cache_key} already published — reusing {dest}")
            source = dest
        else:
            source = _restore_adapter_from_s3(
                run_id=run_id, cache_key=cache_key, user_id=user_id, job_id=job_id
            )

    if source != dest:
        if dest.exists():
            shutil.rmtree(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(source, dest)

    cfg = json.loads((dest / "adapter_config.json").read_text())
    # The adapter was trained against a specific base; pointing it at a different one silently
    # produces garbage, so the serving side records what it expects.
    meta = {
        "base_model": base_model,
        "base_path": f".base_models/{base_model.replace('/', '--')}",
        "lora_rank": int(cfg.get("r") or 0),
        "is_adapter": True,
    }
    (dest / ".meta.json").write_text(json.dumps(meta))
    _drop_staging(cache_key)
    weights_vol.commit()
    print(f"[adapter] {final_dir} → {dest} (base={base_model} r={meta['lora_rank']})")
    return meta


@app.function(
    image=download_image,
    timeout=30 * 60,
    scaledown_window=2,
    volumes={WEIGHTS_MOUNT: weights_vol, S3_MOUNT: _s3_mount(read_only=True)},
    secrets=[inference_secret, aws_secret],
)
@modal.concurrent(max_inputs=1)
def download_checkpoint_from_s3(
    *,
    user_id: str,
    job_id: str,
    model_id: str,
    cache_key: str,
    merge_base_model: str = "",
) -> dict:
    """The common download path for both the baseten and modal providers: once a checkpoint is
    archived, the FP8 pipeline no longer cares which provider trained it.

    ``job_id``/``user_id`` address the S3 archive; ``cache_key`` is the SEPARATE FP8-cache key
    ``register`` derives from remote_job_id. Staging must be keyed by cache_key, because that is
    what register() checks and what prepare_fp8 is dispatched with. ``merge_base_model`` wins over
    adapter_config's base when that points at a quantized remapped repo — merge needs bf16.
    """
    zip_path = _s3_prefix(user_id, job_id) / "checkpoints" / "checkpoint.zip"
    if not zip_path.exists():
        raise FileNotFoundError(f"No S3 checkpoint archive found for job {job_id} at {zip_path}")

    staging = _staging_dir(cache_key)
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(zip_path) as zf:
        zf.extractall(staging)
    print(f"[S3→weights] Extracted {zip_path} → {staging}")

    result = _resolve_staged_base(
        staging=staging,
        model_id=model_id,
        merge_base_model=merge_base_model,
        log_prefix="S3→weights",
    )
    (staging / ".download_meta.json").write_text(json.dumps(result))
    weights_vol.commit()
    print(f"[S3→weights] Download done — staged at {staging}")
    return result


@app.function(
    image=download_image,
    timeout=10 * 60,
    scaledown_window=2,
    volumes={WEIGHTS_MOUNT: weights_vol},
    secrets=[inference_secret],
)
def prune_spent_staging(*, max_age_s: float = _STAGING_ORPHAN_S) -> dict:
    """Drop staging trees that already have a serving copy, or that sat unused past max_age_s."""
    weights_vol.reload()
    root = Path(WEIGHTS_MOUNT)
    removed = []
    for key in spent_staging_names(root, max_age_s=max_age_s):
        if _drop_staging(key):
            removed.append(key)
    if removed:
        weights_vol.commit()
    return {"removed": removed}


@app.function(
    image=download_image,
    timeout=2 * 60 * 60,
    scaledown_window=2,
    volumes={WEIGHTS_MOUNT: weights_vol},
    secrets=[inference_secret],
)
@modal.concurrent(max_inputs=1)
def download_nebius_checkpoint(*, nebius_job_id: str, model_id: str) -> dict:
    """Download the final Nebius checkpoint into /weights/.staging/{job_id}/."""
    from openai import OpenAI, PermissionDeniedError  # noqa: PLC0415

    nebius_api_key = os.environ.get("NEBIUS_API_KEY", "")
    if not nebius_api_key:
        raise RuntimeError("NEBIUS_API_KEY is not set in the Modal secret")

    client = OpenAI(
        base_url="https://api.tokenfactory.nebius.com/v1/",
        api_key=nebius_api_key,
    )

    staging = _staging_dir(nebius_job_id)
    meta_file = staging / ".download_meta.json"
    existing = _read_json(meta_file)
    if existing and existing.get("stage") == "downloaded":
        print(f"[Nebius] {nebius_job_id} already staged — skipping download")
        return existing

    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True, exist_ok=True)

    print(f"[Nebius] Fetching checkpoints for {nebius_job_id}")
    try:
        checkpoints = client.fine_tuning.jobs.checkpoints.list(nebius_job_id).data
    except PermissionDeniedError as exc:
        raise RuntimeError(
            f"Nebius denied access to job {nebius_job_id}. "
            "Ensure NEBIUS_API_KEY in the Modal overmind-inference secret "
            "matches the key that submitted this fine-tuning job."
        ) from exc
    if not checkpoints:
        raise RuntimeError(f"No checkpoints found for Nebius job {nebius_job_id}")

    final_checkpoint = max(checkpoints, key=lambda c: c.step_number)
    print(
        f"[Nebius] Using checkpoint step={final_checkpoint.step_number} "
        f"({len(final_checkpoint.result_files)} files)"
    )

    for file_id in final_checkpoint.result_files:
        file_obj = client.files.retrieve(file_id)
        filename = os.path.basename(file_obj.filename)
        dest = staging / filename
        print(f"[Nebius]   downloading {filename} ({file_obj.bytes} bytes)")
        content = client.files.content(file_id)
        content.write_to_file(str(dest))

    ft_job = client.fine_tuning.jobs.retrieve(nebius_job_id)
    base_model = getattr(ft_job, "model", "")

    is_lora = (staging / "adapter_model.safetensors").exists() or (
        staging / "adapter_model.bin"
    ).exists()
    base_model_path = None

    if is_lora:
        adapter_cfg = json.loads((staging / "adapter_config.json").read_text())
        adapter_base = adapter_cfg.get("base_model_name_or_path", "") or ""
        base_model_name = _resolve_merge_base(adapter_base, base_model)
        if base_model_name != adapter_base:
            print(f"[Nebius] adapter base {adapter_base!r} → merge base {base_model_name!r}")
            adapter_cfg["base_model_name_or_path"] = base_model_name
            (staging / "adapter_config.json").write_text(json.dumps(adapter_cfg, indent=2) + "\n")

        base_model_path = fetch_base_model.remote(base_model=base_model_name)["base_model_path"]

        chat_tmpl_src = staging / "chat_template.jinja"
        if chat_tmpl_src.exists():
            tok_cfg_path = staging / "tokenizer_config.json"
            if tok_cfg_path.exists():
                tok_cfg = json.loads(tok_cfg_path.read_text())
                if not tok_cfg.get("chat_template"):
                    tok_cfg["chat_template"] = chat_tmpl_src.read_text()
                    if tok_cfg.get("tokenizer_class") == "TokenizersBackend":
                        tok_cfg["tokenizer_class"] = "PreTrainedTokenizerFast"
                    tok_cfg_path.write_text(json.dumps(tok_cfg, indent=2))
                    print("[Nebius] Patched adapter tokenizer_config.json with chat_template")

        print("[Nebius] LoRA adapter staged — will merge+FP8 on GPU")
    else:
        print("[Nebius] Full SFT checkpoint staged — will FP8 on GPU")

    result = {
        "staging_path": str(staging),
        "quantization": "none",
        "model_id": model_id,
        "base_model": base_model,
        "is_lora": is_lora,
        "base_model_path": base_model_path,
        "stage": "downloaded",
        "ready": False,
    }
    meta_file.write_text(json.dumps(result))
    weights_vol.commit()
    print(f"[Nebius] Download done — staged at {staging}")
    return result


# Merge is CPU/safetensors-bound and FP8_DYNAMIC is a data-free weight-only transform,
# so these are mostly IO jobs that happen to hold a GPU: the generous cpu= matters more
# than the tier, which only limits how much llmcompressor offloads layer-by-layer.
# Modal fixes a Function's GPU at decoration time, hence two Functions rather than one.
_FP8_TIER_SPLIT_B = 34.0


def _prepare_fp8_fn(params_b: float):
    if params_b and params_b > _FP8_TIER_SPLIT_B:
        return prepare_fp8_large, "H200"
    return prepare_fp8, "L40S"


def _prepare_fp8_body(
    *,
    job_id: str,
    model_id: str,
    is_lora: bool,
    base_model_path: str | None,
    base_model: str,
    quantize: bool,
    source_dir: str = "",
) -> dict:
    """``quantize=False`` (models.json ``fp8_supported: false``) skips the FP8 step and serves the
    merged BF16 weights as-is — see weight_ops.quantize_checkpoint for why.

    ``source_dir`` reads the checkpoint from outside staging — the sft Volume for a full SFT — and
    is never deleted afterwards, being the training output rather than a staging copy.
    """
    import weight_ops

    weights_vol.reload()
    if source_dir:
        sft_vol.reload()

    expected_quant = "fp8" if quantize else "bf16"
    weights_dir = _weights_dir(job_id)
    meta_file = weights_dir / ".meta.json"
    cached = _read_json(meta_file)
    if cached and cached.get("ready") and cached.get("quantization") == expected_quant:
        print(f"[FP8] {job_id} already done ({expected_quant}) — returning cached")
        return cached

    staging = _staging_dir(job_id)
    source = Path(source_dir) if source_dir else staging
    if not source.exists():
        raise FileNotFoundError(f"Checkpoint source missing for {job_id}: {source}")

    print(
        f"[FP8] Preparing {job_id} "
        f"({'LoRA merge+' if is_lora else ''}{'FP8' if quantize else 'BF16 (no quant)'}) "
        f"from {source}"
    )
    extra = weight_ops.prepare_fp8_weights(
        source_dir=source,
        output_dir=weights_dir,
        is_lora=is_lora,
        base_model_path=Path(base_model_path) if base_model_path else None,
        quantize=quantize,
    )

    result = {
        "weights_path": str(weights_dir),
        "quantization": expected_quant,
        "model_id": model_id,
        "base_model": base_model,
        "is_lora": False,
        "base_model_path": None,
        "merge_method": extra.get("merge_method"),
        "ready": True,
    }
    meta_file.write_text(json.dumps(result))

    _drop_staging(job_id)
    weights_vol.commit()
    print(f"[FP8] Done — weights at {weights_dir}")
    return result


_FP8_WORKER_KWARGS = {
    "image": quantize_image,
    "timeout": 2 * 60 * 60,
    "scaledown_window": 2,
    # sft is mounted so a full SFT can be quantized straight off the training output instead of
    # being copied into staging first.
    "volumes": {WEIGHTS_MOUNT: weights_vol, SFT_MOUNT: sft_vol},
    "secrets": [inference_secret],
}


@app.function(gpu="L40S", cpu=8.0, memory=65536, **_FP8_WORKER_KWARGS)
@modal.concurrent(max_inputs=1)
def prepare_fp8(
    *,
    job_id: str,
    model_id: str,
    is_lora: bool,
    base_model_path: str | None = None,
    base_model: str = "",
    quantize: bool = True,
    source_dir: str = "",
) -> dict:
    """Merge+FP8 for models up to ~34B."""
    return _prepare_fp8_body(
        job_id=job_id,
        model_id=model_id,
        is_lora=is_lora,
        base_model_path=base_model_path,
        base_model=base_model,
        quantize=quantize,
        source_dir=source_dir,
    )


@app.function(gpu="H200", cpu=16.0, memory=196608, **_FP8_WORKER_KWARGS)
@modal.concurrent(max_inputs=1)
def prepare_fp8_large(
    *,
    job_id: str,
    model_id: str,
    is_lora: bool,
    base_model_path: str | None = None,
    base_model: str = "",
    quantize: bool = True,
    source_dir: str = "",
) -> dict:
    """Merge+FP8 for >34B models — 141 GB VRAM keeps llmcompressor off the CPU."""
    return _prepare_fp8_body(
        job_id=job_id,
        model_id=model_id,
        is_lora=is_lora,
        base_model_path=base_model_path,
        base_model=base_model,
        quantize=quantize,
        source_dir=source_dir,
    )
