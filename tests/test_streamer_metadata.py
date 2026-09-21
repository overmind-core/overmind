import json
from collections import namedtuple
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from experiments.inference_upgrade.streamer_metadata import (
    artifact_identity,
    order_shards,
    stream_with_metadata,
    validate_metadata,
)


@pytest.fixture
def artifact(tmp_path):
    manifest = {
        "files": ["part.safetensors"],
        "bytes": 4,
        "layout": {"weight": {"shape": [2], "dtype": "torch.bfloat16", "stride": [1]}},
    }
    (tmp_path / "ready.json").write_text(json.dumps(manifest))
    (tmp_path / "part.safetensors").write_bytes(b"0" * 12)
    tensor = SimpleNamespace(
        name="weight",
        shape=[2],
        offsets=SimpleNamespace(start=0, end=4),
        get_torch_dtype=lambda: "torch.bfloat16",
        get_bytesize=lambda: 4,
    )
    metadata = [(8, [tensor], [4])]
    return tmp_path, manifest, metadata


def test_cached_metadata_covers_layout_and_reuses_without_mutating(artifact):
    target, manifest, metadata = artifact
    identity = artifact_identity(target, manifest)
    validate_metadata(metadata, manifest, identity)
    streamer = SimpleNamespace(file_streamer=Mock(), files_to_tensors_metadata={})
    chunks = namedtuple("Chunks", "index path offset sizes")
    files = [str(target / manifest["files"][0])]
    for _ in range(2):
        assert stream_with_metadata(streamer, files, metadata, chunks) >= 0
        args, kwargs = streamer.file_streamer.stream_files.call_args
        assert args[0] == [chunks(0, files[0], 8, [4])]
        assert kwargs == {"credentials": None, "device": "cpu", "is_distributed": False}
        args[0][0].sizes.clear()
        assert metadata[0][2] == [4]
        assert streamer.files_to_tensors_metadata == {0: metadata[0][1]}
    assert artifact_identity(target, manifest) == identity


@pytest.mark.parametrize("change", ["manifest", "shard", "missing"])
def test_identity_detects_changed_artifacts(artifact, change):
    target, manifest, _ = artifact
    before = artifact_identity(target, manifest)
    if change == "manifest":
        (target / "ready.json").write_text("changed")
    elif change == "shard":
        (target / "part.safetensors").write_bytes(b"changed")
    else:
        (target / "part.safetensors").unlink()
        with pytest.raises(FileNotFoundError):
            artifact_identity(target, manifest)
        return
    assert artifact_identity(target, manifest) != before


@pytest.mark.parametrize("change", ["count", "offset", "shape", "dtype", "name", "bytes", "gap"])
def test_metadata_rejects_inconsistent_layout(artifact, change):
    target, manifest, metadata = artifact
    identity = artifact_identity(target, manifest)
    tensor = metadata[0][1][0]
    if change == "count":
        metadata.clear()
    elif change == "offset":
        metadata[0] = (9, metadata[0][1], [4])
    elif change == "shape":
        tensor.shape = [3]
    elif change == "dtype":
        tensor.get_torch_dtype = lambda: "torch.float32"
    elif change == "name":
        tensor.name = "other"
    elif change == "bytes":
        manifest["bytes"] += 1
    else:
        tensor.offsets.start = 1
    with pytest.raises(ValueError):
        validate_metadata(metadata, manifest, identity)


@pytest.mark.parametrize("files", [[], ["x", "x"], ["../outside"], ["/absolute"]])
def test_identity_rejects_ambiguous_shard_names(tmp_path, files):
    with pytest.raises(ValueError):
        artifact_identity(tmp_path, {"files": files})


def test_native_submission_failure_propagates(artifact):
    _, _, metadata = artifact
    streamer = SimpleNamespace(file_streamer=Mock())
    streamer.file_streamer.stream_files.side_effect = RuntimeError("reader failure")
    with pytest.raises(RuntimeError, match="reader failure"):
        stream_with_metadata(streamer, ["part"], metadata, lambda *args: args)


@pytest.mark.parametrize(
    "order,indices",
    [("manifest", [0, 1, 2]), ("smallest-first", [1, 0, 2]), ("largest-first", [0, 2, 1])],
)
def test_shard_order_preserves_file_metadata_pairing_and_cache(order, indices):
    files = ["a", "b", "c"]
    metadata = [(8, ["a-weight"], [4]), (16, ["b-weight"], [2]), (24, ["c-weight"], [1, 3])]
    original_files, original_metadata = list(files), list(metadata)
    ordered_files, ordered_metadata = order_shards(files, metadata, order)
    assert ordered_files == [files[index] for index in indices]
    assert all(ordered_metadata[i] is metadata[index] for i, index in enumerate(indices))
    streamer = SimpleNamespace(file_streamer=Mock())
    chunks = namedtuple("Chunks", "index path offset sizes")
    stream_with_metadata(streamer, ordered_files, ordered_metadata, chunks)
    assert streamer.file_streamer.stream_files.call_args.args[0] == [
        chunks(i, files[index], metadata[index][0], metadata[index][2])
        for i, index in enumerate(indices)
    ]
    assert streamer.files_to_tensors_metadata == {
        i: metadata[index][1] for i, index in enumerate(indices)
    }
    assert files == original_files and metadata == original_metadata
    assert ordered_files is not files and ordered_metadata is not metadata


@pytest.mark.parametrize(
    "files,metadata,order",
    [
        ([], [], "manifest"),
        (["a"], [], "manifest"),
        ([], [(8, [], [])], "manifest"),
        (["a"], [(8, [], [])], "invalid"),
    ],
)
def test_shard_order_rejects_empty_mismatched_or_unknown_requests(files, metadata, order):
    with pytest.raises(ValueError):
        order_shards(files, metadata, order)
