from unittest.mock import Mock

from overbae.modal import register_model as registration


def test_archive_restore_preserves_base_identity_instead_of_cache_key(tmp_path, monkeypatch):
    staging = tmp_path / "adapter-cache"
    staging.mkdir()
    (staging / "adapter_config.json").write_text("{}")
    download = Mock()
    monkeypatch.setattr(registration, "download_checkpoint_from_s3", Mock(remote=download))
    monkeypatch.setattr(registration, "weights_vol", Mock())
    monkeypatch.setattr(registration, "_staging_dir", lambda key: staging)
    assert (
        registration._restore_adapter_from_s3(
            run_id="run",
            cache_key="adapter-cache",
            user_id="user",
            job_id="job",
            base_model="org/real-base",
        )
        == staging
    )
    download.assert_called_once_with(
        user_id="user",
        job_id="job",
        model_id="org/real-base",
        cache_key="adapter-cache",
        merge_base_model="org/real-base",
    )
