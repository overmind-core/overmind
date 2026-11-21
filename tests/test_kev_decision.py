import pytest

from modal_shared.modelfam import resolve
from modal_shared.serving.decision_http import decision_sglang_command
from modal_shared.serving.speculative import draft_for, mtp_for
from modal_shared.stacks import SERVE_SGLANG_KEV, TRAIN_KEV
from overbae.services.sft_assets.kev_engine import kev_label, kev_record, kev_record_from_prepared

pytestmark = pytest.mark.covers("model-runtime")


def test_kev_family_uses_its_own_stacks() -> None:
    spec = resolve("jaredpalmer/kev-4b")
    assert spec.key == "kev"
    assert spec.train_image == TRAIN_KEV
    assert spec.serve_image == SERVE_SGLANG_KEV
    assert decision_sglang_command(model_path="/weights/kev", port=8000)[:4] == [
        "sglang",
        "serve",
        "--model-path",
        "/weights/kev",
    ]


def test_kev_serving_stays_on_the_base() -> None:
    command = decision_sglang_command(model_path="/weights/kev", port=8000)
    assert "speculative" not in command
    for repo in ("jaredpalmer/kev-0.8b", "jaredpalmer/kev-4b", "jaredpalmer/kev-9b"):
        assert mtp_for(repo) is None
        assert draft_for(repo) is None


def test_kev_record_keeps_a_one_hot_choice() -> None:
    row = kev_record(
        {
            "decision": {
                "state": "A coin landed heads.",
                "question": "Which side?",
                "kind": "choice",
                "options": ["heads", "tails"],
                "target_probabilities": [1.0, 0.0],
            }
        }
    )
    assert row["questions"]["decision"]["label"] == "heads"
    assert kev_label(
        {
            "kind": "noul",
            "options": ["no", "yes"],
            "target_probabilities": [0.0, 1.0],
        }
    )


def test_kev_record_reads_a_prepared_row() -> None:
    row = kev_record_from_prepared(
        {
            "record": {"state": "A coin landed heads."},
            "question": "Which side?",
            "kind": "choice",
            "options": ["heads", "tails"],
            "target_probabilities": [1.0, 0.0],
        }
    )
    assert row["questions"]["decision"]["label"] == "heads"


def test_kev_record_refuses_a_soft_target() -> None:
    with pytest.raises(ValueError, match="one-hot"):
        kev_label(
            {
                "kind": "choice",
                "options": ["heads", "tails"],
                "target_probabilities": [0.7, 0.3],
            }
        )
