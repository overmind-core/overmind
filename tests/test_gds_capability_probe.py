import json
from pathlib import Path
from unittest.mock import Mock

import pytest

from experiments.inference_upgrade import gds_capability_probe as probe


@pytest.mark.parametrize("failure", [None, "open", "file", "buffer", "read", "verify", "cleanup"])
def test_strict_read_reports_failure_and_cleans_registered_resources(monkeypatch, failure):
    opening, closing = Mock(return_value=17), Mock()
    if failure == "open":
        opening.side_effect = OSError("direct IO unsupported")
    monkeypatch.setattr(probe.os, "O_DIRECT", 16384, raising=False)
    monkeypatch.setattr(probe.os, "open", opening)
    monkeypatch.setattr(probe.os, "close", closing)
    torch, gds = Mock(), Mock()
    reference = torch.frombuffer.return_value
    destination = torch.bitwise_not.return_value.to.return_value
    storage = destination.untyped_storage.return_value
    file = gds.GdsFile.return_value
    torch.equal.return_value = failure != "verify"
    if failure == "file":
        gds.GdsFile.side_effect = RuntimeError("file registration unsupported")
    if failure == "buffer":
        gds.gds_register_buffer.side_effect = RuntimeError("GPU registration unsupported")
    if failure == "read":
        file.load_storage.side_effect = RuntimeError("native read unsupported")
    if failure == "cleanup":
        gds.gds_deregister_buffer.side_effect = RuntimeError("GPU deregistration failed")
    monkeypatch.setattr(probe, "torch", torch, raising=False)
    monkeypatch.setattr(probe, "gds", gds, raising=False)
    expected = b"a" * 65536
    result = probe.check_path("/artifacts/existing", expected)
    assert result["verified_bytes"] == (0 if failure else len(expected))
    assert (result["error"] is None) is (failure is None)
    if failure:
        assert (
            result["failed_stage"]
            == {
                "open": "open_direct",
                "file": "register_file",
                "buffer": "register_gpu_buffer",
                "read": "read",
                "verify": "verify",
                "cleanup": "cleanup",
            }[failure]
        )
    assert closing.call_count == (0 if failure == "open" else 1)
    assert file.deregister_handle.call_count == (0 if failure in {"open", "file"} else 1)
    assert gds.gds_deregister_buffer.call_count == (
        0 if failure in {"open", "file", "buffer"} else 1
    )
    if failure not in {"open", "file"}:
        torch.bitwise_not.assert_called_once_with(reference)
        torch.bitwise_not.return_value.to.assert_called_once_with("cuda")
    if failure not in {"open", "file", "buffer"}:
        file.load_storage.assert_called_once_with(storage, offset=0)


def test_probe_requires_strict_environment_and_existing_artifact_id(monkeypatch):
    method = probe.Probe._get_user_cls().check._get_raw_f()
    with pytest.raises(ValueError, match="artifact ID"):
        method(None, "../other")
    for key, value in probe.STRICT_ENV.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("CUFILE_ALLOW_COMPAT_MODE", "true")
    with pytest.raises(RuntimeError, match="Strict cuFile"):
        method(None, "a" * 20)


def test_probe_returns_plain_version_without_client_torch_dependency(monkeypatch, tmp_path):
    class TorchVersion(str):
        pass

    artifact = "a" * 20
    directory = tmp_path / "artifacts" / artifact
    directory.mkdir(parents=True)
    (directory / "ready.json").write_text(json.dumps({"files": ["part.safetensors"]}))
    (directory / "part.safetensors").write_bytes(b"a" * 65536)
    monkeypatch.setattr(
        probe,
        "Path",
        lambda path: (
            tmp_path / "artifacts"
            if str(path) == "/artifacts"
            else tmp_path / "missing.log"
            if str(path) == probe.STRICT_ENV["CUFILE_LOGFILE_PATH"]
            else Path(path)
        ),
    )
    for key, value in probe.STRICT_ENV.items():
        monkeypatch.setenv(key, value)
    torch = Mock(__version__=TorchVersion("2.13.0"))
    torch.cuda.get_device_name.return_value = "H200"
    torch.version.cuda = "13.0"
    monkeypatch.setattr(probe, "torch", torch, raising=False)
    monkeypatch.setattr(probe, "artifacts", Mock())
    monkeypatch.setattr(probe, "distributions", lambda: [])
    reading = Mock(return_value={"verified_bytes": 65536, "error": None})
    monkeypatch.setattr(probe, "check_path", reading)

    result = probe.Probe._get_user_cls().check._get_raw_f()(None, artifact)

    assert type(result["torch_version"]) is str
    assert result["torch_version"] == "2.13.0"
    assert reading.call_count == 2
    assert json.loads(json.dumps(result)) == result
