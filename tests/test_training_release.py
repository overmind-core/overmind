from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from overbae.services import training_release


def test_release_requires_explicit_environment_and_pins_observers_to_saved_release(
    tmp_path, monkeypatch
):
    monkeypatch.delenv("MODAL_ENVIRONMENT", raising=False)
    with pytest.raises(ValueError, match="MODAL_ENVIRONMENT"):
        training_release.current()
    monkeypatch.setenv("MODAL_ENVIRONMENT", "qualification")
    original = training_release.current()
    assert original["app"] != "overmind-sft"
    job = SimpleNamespace(requested_configuration={"runtime": original})
    monkeypatch.setenv("MODAL_ENVIRONMENT", "different-environment")
    assert training_release.for_job(job) == original
    with patch("modal.Function.from_name") as factory:
        factory.return_value.remote.return_value = {**original, "training": "changed"}
        with pytest.raises(ValueError, match="release"):
            training_release.verify(original)
        assert factory.call_args.kwargs["environment_name"] == "qualification"
    assert Path(training_release.__file__).is_file()
