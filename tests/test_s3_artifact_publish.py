import base64
import hashlib
import io
import json
from unittest.mock import Mock

import pytest
from botocore.exceptions import ClientError

from experiments.inference_upgrade import s3_artifact_publish as publisher
from experiments.inference_upgrade.s3_artifact_publish import BUCKET, publish_directory
from experiments.inference_upgrade.s3_artifact_reader import (
    MAX_INDEX_BYTES,
    REGION,
    native_s3_environment,
    resolve_files,
)
from experiments.inference_upgrade.streamer_metadata import artifact_identity


def failure(code):
    return ClientError({"Error": {"Code": code}}, "S3")


@pytest.fixture
def artifact(tmp_path):
    target = tmp_path / ("a" * 20)
    target.mkdir()
    (target / "part-0000.safetensors").write_bytes(b"12345678abcd")
    (target / "ready.json").write_text(json.dumps({"files": ["part-0000.safetensors"], "bytes": 4}))
    return target


@pytest.fixture
def storage():
    objects = {}

    def head(**kwargs):
        assert kwargs["Bucket"] == BUCKET and kwargs["ChecksumMode"] == "ENABLED"
        if kwargs["Key"] not in objects:
            raise failure("404")
        body = objects[kwargs["Key"]]
        return {
            "ContentLength": len(body),
            "ChecksumSHA256": base64.b64encode(hashlib.sha256(body).digest()).decode(),
            "ETag": '"etag"',
        }

    def put(**kwargs):
        assert kwargs["Bucket"] == BUCKET and kwargs["IfNoneMatch"] == "*"
        if kwargs["Key"] in objects:
            raise failure("PreconditionFailed")
        body = kwargs["Body"].read()
        assert len(body) == kwargs["ContentLength"]
        assert base64.b64encode(hashlib.sha256(body).digest()).decode() == kwargs["ChecksumSHA256"]
        objects[kwargs["Key"]] = body

    def get(**kwargs):
        assert kwargs["Bucket"] == BUCKET
        body = objects[kwargs["Key"]]
        return {"Body": io.BytesIO(body), "ContentLength": len(body)}

    return Mock(
        head_object=Mock(side_effect=head),
        put_object=Mock(side_effect=put),
        get_object=Mock(side_effect=get),
    ), objects


def test_publisher_verifies_bytes_publishes_index_last_and_reuses(artifact, storage):
    s3, objects = storage
    first = publish_directory(artifact, s3)
    keys = list(objects)
    assert keys[-1] == first["index"]["key"]
    index = json.loads(objects[keys[-1]])
    descriptor = index["objects"]["part-0000.safetensors"]
    assert objects[descriptor["key"]] == (artifact / "part-0000.safetensors").read_bytes()
    assert (
        index["source_manifest_sha256"]
        == hashlib.sha256((artifact / "ready.json").read_bytes()).hexdigest()
    )
    second = publish_directory(artifact, s3)
    assert first["index"] == second["index"]
    assert second["uploaded_bytes"] == 0 and second["reused_shards"] == 1
    assert first["file_bytes"] == 12 and first["payload_bytes"] == 4
    assert s3.put_object.call_count == 2
    objects[descriptor["key"]] = b"x" * 12
    with pytest.raises(RuntimeError, match="Remote object differs"):
        publish_directory(artifact, s3)
    assert s3.put_object.call_count == 2


def test_missing_remote_checksum_is_not_accepted(artifact, storage):
    s3, _ = storage
    s3.head_object.side_effect = lambda **kwargs: {"ContentLength": 12, "ETag": '"etag"'}
    with pytest.raises(RuntimeError, match="Remote object differs"):
        publish_directory(artifact, s3)
    s3.put_object.assert_not_called()


def test_manifest_race_stops_before_remote_io(artifact, storage, monkeypatch):
    s3, _ = storage
    monkeypatch.setattr(publisher, "artifact_identity", lambda *args: ("changed", []))
    with pytest.raises(RuntimeError, match="Source manifest changed"):
        publish_directory(artifact, s3)
    s3.head_object.assert_not_called()


def test_head_permission_failure_is_not_treated_as_missing(artifact, storage):
    s3, _ = storage
    s3.head_object.side_effect = failure("AccessDenied")
    with pytest.raises(ClientError):
        publish_directory(artifact, s3)
    s3.put_object.assert_not_called()


def test_concurrent_publication_requires_checksum_verified_winner(artifact, storage):
    s3, objects = storage
    put = s3.put_object.side_effect

    def competing_put(**kwargs):
        put(**kwargs)
        raise failure("PreconditionFailed")

    s3.put_object.side_effect = competing_put
    result = publish_directory(artifact, s3)
    assert result["uploaded_bytes"] == 0 and result["reused_shards"] == 1
    assert len(objects) == 2


@pytest.mark.parametrize("code", ["AccessDenied", "ConditionalRequestConflict"])
def test_publisher_preserves_remote_errors_without_ready_index(artifact, storage, code):
    s3, objects = storage
    s3.put_object.side_effect = failure(code)
    with pytest.raises(ClientError) as raised:
        publish_directory(artifact, s3)
    assert raised.value.response["Error"]["Code"] == code
    assert not objects


def test_changed_source_never_publishes_ready_index(artifact, storage):
    s3, objects = storage
    put = s3.put_object.side_effect

    def changing_put(**kwargs):
        put(**kwargs)
        (artifact / "part-0000.safetensors").write_bytes(b"changed")

    s3.put_object.side_effect = changing_put
    with pytest.raises(RuntimeError, match="Source artifact changed"):
        publish_directory(artifact, s3)
    assert len(objects) == 1 and not any("/manifests/" in key for key in objects)


@pytest.mark.parametrize(
    "names", [[], ["../outside"], ["part-0000.safetensors"] * 2, ["adapter_model.safetensors"]]
)
def test_publisher_rejects_unsafe_or_ambiguous_shards(artifact, storage, names):
    s3, objects = storage
    (artifact / "adapter_model.safetensors").write_bytes(b"adapter")
    (artifact / "ready.json").write_text(json.dumps({"files": names, "bytes": 4}))
    with pytest.raises(ValueError):
        publish_directory(artifact, s3)
    s3.head_object.assert_not_called()
    assert not objects


def test_publisher_rejects_symlink_shards(artifact, storage):
    s3, objects = storage
    (artifact / "other").write_bytes(b"bytes")
    shard = artifact / "part-0000.safetensors"
    shard.unlink()
    shard.symlink_to(artifact / "other")
    with pytest.raises(ValueError, match="Invalid or oversized"):
        publish_directory(artifact, s3)
    assert not objects


def test_reader_resolves_published_shards_without_copying_weights(artifact, storage):
    s3, objects = storage
    publication = publish_directory(artifact, s3)
    manifest = json.loads((artifact / "ready.json").read_bytes())
    s3.reset_mock()
    files, validation = resolve_files(
        s3, publication["index_uri"], artifact.name, manifest, artifact_identity(artifact, manifest)
    )
    assert files == [f"s3://{BUCKET}/{next(iter(objects))}"]
    assert validation["file_bytes"] == 12 and validation["shards"] == 1
    assert validation["region"] == REGION and validation["validation_s"] >= 0
    s3.get_object.assert_called_once_with(Bucket=BUCKET, Key=publication["index"]["key"])
    s3.head_object.assert_called_once()
    s3.put_object.assert_not_called()


@pytest.mark.parametrize(
    "change",
    [
        {"bucket": "other"},
        {"region": "us-east-1"},
        {"artifact_key": "b" * 20},
        {"source_manifest_sha256": "0" * 64},
        {"manifest": {}},
        {"objects": {}},
    ],
)
def test_reader_rejects_validly_hashed_index_for_wrong_base(artifact, storage, change):
    s3, objects = storage
    publication = publish_directory(artifact, s3)
    index = json.loads(objects[publication["index"]["key"]])
    index.update(change)
    body = json.dumps(index).encode()
    key = publication["index"]["key"].replace(
        publication["index"]["sha256"], hashlib.sha256(body).hexdigest()
    )
    objects[key] = body
    manifest = json.loads((artifact / "ready.json").read_bytes())
    s3.reset_mock()
    with pytest.raises(ValueError, match="match|coverage"):
        resolve_files(
            s3,
            f"s3://{BUCKET}/{key}",
            artifact.name,
            manifest,
            artifact_identity(artifact, manifest),
        )
    s3.head_object.assert_not_called()


@pytest.mark.parametrize(
    "change", [{"key": "outside"}, {"sha256": "bad"}, {"bytes": 13}, {"etag": ""}]
)
def test_reader_rejects_invalid_shard_descriptor(artifact, storage, change):
    s3, objects = storage
    publication = publish_directory(artifact, s3)
    index = json.loads(objects[publication["index"]["key"]])
    index["objects"]["part-0000.safetensors"].update(change)
    body = json.dumps(index).encode()
    key = publication["index"]["key"].replace(
        publication["index"]["sha256"], hashlib.sha256(body).hexdigest()
    )
    objects[key] = body
    manifest = json.loads((artifact / "ready.json").read_bytes())
    s3.reset_mock()
    with pytest.raises(ValueError, match="descriptor"):
        resolve_files(
            s3,
            f"s3://{BUCKET}/{key}",
            artifact.name,
            manifest,
            artifact_identity(artifact, manifest),
        )
    s3.head_object.assert_not_called()


@pytest.mark.parametrize(
    "field,value",
    [
        ("ContentLength", 13),
        ("ChecksumSHA256", None),
        ("ETag", "changed"),
        ("VersionId", "changed"),
    ],
)
def test_reader_rejects_changed_remote_shard(artifact, storage, field, value):
    s3, _ = storage
    publication = publish_directory(artifact, s3)
    head = s3.head_object.side_effect
    s3.head_object.side_effect = lambda **kwargs: {**head(**kwargs), field: value}
    manifest = json.loads((artifact / "ready.json").read_bytes())
    with pytest.raises(RuntimeError, match="shard changed"):
        resolve_files(
            s3,
            publication["index_uri"],
            artifact.name,
            manifest,
            artifact_identity(artifact, manifest),
        )


@pytest.mark.parametrize("suffix", ["?versionId=other", "#fragment", "/../other"])
def test_reader_rejects_ambiguous_uri_before_io(artifact, storage, suffix):
    s3, _ = storage
    publication = publish_directory(artifact, s3)
    s3.reset_mock()
    with pytest.raises(ValueError, match="index URI"):
        resolve_files(s3, publication["index_uri"] + suffix, artifact.name, {}, ("", []))
    s3.get_object.assert_not_called()


@pytest.mark.parametrize("body", [b"changed", b"x" * (MAX_INDEX_BYTES + 1)])
def test_reader_rejects_corrupt_or_oversized_index(artifact, storage, body):
    s3, objects = storage
    publication = publish_directory(artifact, s3)
    objects[publication["index"]["key"]] = body
    s3.reset_mock()
    with pytest.raises((ValueError, RuntimeError), match="length|checksum"):
        resolve_files(s3, publication["index_uri"], artifact.name, {}, ("", []))
    s3.head_object.assert_not_called()


def test_native_s3_environment_is_restored_on_failure(monkeypatch):
    monkeypatch.setenv("AWS_REGION", "previous")
    monkeypatch.delenv("AWS_DEFAULT_REGION", raising=False)
    monkeypatch.delenv("RUNAI_STREAMER_NO_BOTO3_SESSION", raising=False)
    with pytest.raises(RuntimeError), native_s3_environment():
        assert publisher.os.environ["AWS_REGION"] == REGION
        assert publisher.os.environ["RUNAI_STREAMER_NO_BOTO3_SESSION"] == "0"
        raise RuntimeError("reader failed")
    assert publisher.os.environ["AWS_REGION"] == "previous"
    assert "AWS_DEFAULT_REGION" not in publisher.os.environ
    assert "RUNAI_STREAMER_NO_BOTO3_SESSION" not in publisher.os.environ
