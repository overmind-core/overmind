import os
import time
import uuid
from contextlib import contextmanager
from importlib.metadata import version
from pathlib import Path
from types import SimpleNamespace

import torch
from runai_model_streamer import SafetensorsStreamer
from runai_model_streamer.file_streamer import FileChunks, requests_iterator
from runai_model_streamer.safetensors_streamer.safetensors_pytorch import prepare_request
from safetensors.torch import save_file

from modal_shared.serving.artifacts import (
    ARTIFACT_SCHEMA,
    SHARD_BYTES,
    artifact_directory,
    atomic_json,
    digest_file,
    digest_json,
    file_state,
    read_artifact,
)


def parameter_layout(model):
    return {
        name: {"shape": list(p.shape), "dtype": str(p.dtype), "stride": list(p.stride())}
        for name, p in model.named_parameters()
    }


def validate_metadata(metadata, manifest):
    if len(metadata) != len(manifest["files"]):
        raise ValueError("Metadata shard count mismatch")
    seen, total = set(), 0
    for (offset, tensors, sizes), info in zip(metadata, manifest["files"].values(), strict=True):
        if offset < 8 or offset + sum(sizes) != info["size"]:
            raise ValueError("Metadata does not cover the shard payload")
        position = 0
        for tensor, size in zip(tensors, sizes, strict=True):
            expected = manifest["layout"].get(tensor.name)
            if tensor.name in seen or expected is None:
                raise ValueError("Unexpected or duplicate metadata tensor")
            if (
                list(tensor.shape) != expected["shape"]
                or str(tensor.get_torch_dtype()) != expected["dtype"]
                or tensor.offsets.start != position
                or tensor.offsets.end != position + size
                or size != tensor.get_bytesize()
                or size < 0
            ):
                raise ValueError("Metadata tensor layout mismatch")
            seen.add(tensor.name)
            position += size
        total += position
    if seen != set(manifest["layout"]) or total != manifest["bytes"]:
        raise ValueError("Metadata parameter coverage mismatch")


@contextmanager
def pinned_reader_buffer():
    if version("runai-model-streamer") != "0.16.1":
        raise RuntimeError("Snapshot loader requires runai-model-streamer 0.16.1")
    numpy = requests_iterator.np
    owners = []

    def allocate(size, dtype):
        if dtype != numpy.uint8 or type(size) is not int or size <= 0:
            raise ValueError("Expected a positive byte-buffer allocation")
        owner = torch.empty(size, dtype=torch.uint8, pin_memory=True)
        if not owner.is_pinned():
            raise RuntimeError("Reader backing allocation is not pinned")
        owners.append(owner)
        return owner.numpy()

    # This pinned upstream version allocates at one module-local NumPy call site.
    # Preserve ownership until the reader closes; never replace global numpy.empty.
    requests_iterator.np = SimpleNamespace(empty=allocate, uint8=numpy.uint8)
    try:
        yield
    finally:
        requests_iterator.np = numpy


class SharedBaseWeights:
    @torch.inference_mode()
    def prepare_shared_base(self, root, base_identity, profile):
        model = self.model_runner.get_model()
        if model.lora_manager.list_adapters():
            raise RuntimeError("Shared snapshots must not contain adapters")
        if self.parallel_config.world_size != 1 or self.model_config.quantization is not None:
            raise RuntimeError("Shared snapshots require single-rank unquantized serving")
        if version("runai-model-streamer") != "0.16.1":
            raise RuntimeError("Unsupported snapshot streamer version")
        layout = parameter_layout(model)
        contract = {
            "schema": ARTIFACT_SCHEMA,
            "base": base_identity,
            "profile": profile,
            "vllm": version("vllm"),
            "torch": torch.__version__,
            "cuda": torch.version.cuda,
            "gpu": torch.cuda.get_device_name(),
            "streamer": version("runai-model-streamer"),
            "image": os.environ["MODAL_IMAGE_ID"],
            "loader": digest_file(Path(__file__)),
            "layout": digest_json(layout),
        }
        root = Path(root)
        directory = artifact_directory(root, contract)
        reused = directory is not None
        started = time.monotonic()
        if directory is None:
            parent = root / digest_json(contract)
            directory = parent / uuid.uuid4().hex
            directory.mkdir(parents=True)
            files, shard, size, total = {}, {}, 0, 0

            def flush():
                nonlocal shard, size
                path = directory / f"part-{len(files):04d}.safetensors"
                save_file(shard, str(path))
                files[path.name] = {**file_state(path), "sha256": digest_file(path)}
                shard, size = {}, 0

            for name, param in model.named_parameters():
                count = param.numel() * param.element_size()
                if shard and size + count > SHARD_BYTES:
                    flush()
                shard[name] = param.detach().cpu().contiguous().clone()
                size += count
                total += count
            if shard:
                flush()
            manifest = {"contract": contract, "layout": layout, "files": files, "bytes": total}
            atomic_json(directory / "ready.json", {**manifest, "identity": digest_json(manifest)})
            # Generations are immutable: concurrent builders never overwrite shard data.
            atomic_json(parent / "current.json", {"generation": directory.name})
        manifest = read_artifact(directory, contract)
        if reused:
            for name, info in manifest["files"].items():
                if digest_file(directory / name) != info["sha256"]:
                    raise RuntimeError(f"Inference artifact checksum mismatch: {name}")
        files = [str(directory / name) for name in manifest["files"]]
        with SafetensorsStreamer() as streamer:
            metadata = prepare_request(streamer.file_streamer, files, None)
        validate_metadata(metadata, manifest)
        self.shared_base_metadata = metadata
        self.shared_base_manifest = manifest
        self.shared_base_directory = str(directory)
        self.shared_base_addresses = {name: p.data_ptr() for name, p in model.named_parameters()}
        return {
            "reused": reused,
            "identity": manifest["identity"],
            "bytes": manifest["bytes"],
            "shards": len(files),
            "prepare_s": time.monotonic() - started,
        }

    @torch.inference_mode()
    def restore_shared_base(self):
        started = time.monotonic()
        directory = Path(self.shared_base_directory)
        manifest = read_artifact(directory, self.shared_base_manifest["contract"])
        if manifest != self.shared_base_manifest:
            raise RuntimeError("Snapshot artifact changed")
        model = self.model_runner.get_model()
        manager = model.lora_manager
        if manager.list_adapters() or parameter_layout(model) != manifest["layout"]:
            raise RuntimeError("Snapshot base layout or adapter isolation changed")
        parameters = dict(model.named_parameters())
        if self.shared_base_addresses != {name: p.data_ptr() for name, p in parameters.items()}:
            raise RuntimeError("Snapshot changed CUDA graph parameter addresses")
        memory_gb = 8 if manifest["bytes"] <= 80 * 10**9 else 16
        before_env = {
            key: os.environ.get(key)
            for key in ("RUNAI_STREAMER_MEMORY_LIMIT", "RUNAI_STREAMER_CONCURRENCY")
        }
        original_threads = torch.get_num_threads()
        seen, total = set(), 0
        try:
            os.environ["RUNAI_STREAMER_MEMORY_LIMIT"] = str(memory_gb * 10**9)
            os.environ["RUNAI_STREAMER_CONCURRENCY"] = "16"
            torch.set_num_threads(8)
            with pinned_reader_buffer(), SafetensorsStreamer() as streamer:
                chunks = []
                streamer.files_to_tensors_metadata = {}
                for index, (name, (offset, tensors, sizes)) in enumerate(
                    zip(manifest["files"], self.shared_base_metadata, strict=True)
                ):
                    streamer.files_to_tensors_metadata[index] = tensors
                    chunks.append(FileChunks(index, str(directory / name), offset, list(sizes)))
                streamer.file_streamer.stream_files(
                    chunks, credentials=None, device="cpu", is_distributed=False
                )
                for name, tensor in streamer.get_tensors():
                    if name not in parameters or name in seen:
                        raise RuntimeError(f"Unexpected or duplicate tensor: {name}")
                    destination = parameters[name]
                    if (
                        destination.shape != tensor.shape
                        or destination.dtype != tensor.dtype
                        or not tensor.is_pinned()
                    ):
                        raise RuntimeError(f"Unpinned or incompatible tensor: {name}")
                    # Finish DMA before requesting the next tensor: the buffer is reusable.
                    destination.copy_(tensor, non_blocking=False)
                    total += tensor.numel() * tensor.element_size()
                    seen.add(name)
                    del tensor
            if seen != set(parameters) or total != manifest["bytes"]:
                raise RuntimeError("Incomplete base weight restore")
            for wrapper in manager.modules.values():
                for slot in range(manager.lora_slots):
                    wrapper.reset_lora(slot)
            manager._last_mapping = None
            manager._last_slot_layout = None
            torch.cuda.synchronize()
            if self.shared_base_addresses != {name: p.data_ptr() for name, p in parameters.items()}:
                raise RuntimeError("Weight reload changed CUDA graph addresses")
        finally:
            torch.set_num_threads(original_threads)
            for key, value in before_env.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value
        return {"reload_s": time.monotonic() - started, "bytes": total, "parameters": len(seen)}
