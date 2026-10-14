from __future__ import annotations

import pytest
from factories import auth_client, make_project, make_user

from overbae.modal.decision_catalog import models
from overbae.services.decision_recommend import choose, recommend

pytestmark = pytest.mark.django_db


def _spec(model_id, params, index, *, context=100, limits=None):
    spec = {"id": model_id, "params": params, "max_context_length": context}
    if index is not None:
        spec["decision_index"] = index
        spec["decision_index_edition"] = "0.3.2"
    if limits is not None:
        spec["limits"] = limits
    return spec


def _by_label(result):
    return {label: pick["model"] for pick in result["picks"] for label in pick["labels"]}


def test_the_catalog_offers_the_strongest_a_smaller_and_the_smallest_model():
    result = choose(list(models().values()), 100)

    assert _by_label(result) == {
        "strongest": "vllm-sr/d3",
        "smaller": "vllm-sr/d3-flash",
        "smallest": "LiquidAI/d1-omni-600M",
    }
    assert [pick["reason"] for pick in result["picks"]] == [
        "Highest Decision Index that fits (66.3).",
        "8.4B, Decision Index 55.8; under half the size of vllm-sr/d3.",
        "Smallest model that fits (0.6B).",
    ]


def test_a_dataset_beyond_a_family_s_limits_leaves_that_family_out_of_the_picks():
    shape = {"options": 27, "score_levels": 2, "questions": 1}
    result = choose(list(models().values()), 100, shape=shape)

    assert not any(pick["model"].startswith("Cloudflare/") for pick in result["picks"])
    clef = next(row for row in result["candidates"] if row["model"] == "Cloudflare/clef")
    assert clef["fits"] is False
    assert clef["why_not"] == "Takes up to 26 options; this dataset has 27."


def test_a_model_filling_two_labels_is_returned_once_with_both():
    result = choose([_spec("only", 1_000_000_000, 40.0), _spec("big", 20_000_000_000, 30.0)], 10)

    assert result["picks"] == [
        {
            "labels": ["strongest", "smallest"],
            "model": "only",
            "reason": "Highest Decision Index that fits (40). Smallest model that fits (1.0B).",
        }
    ]


def test_the_smallest_pick_may_be_unscored_and_ties_go_to_the_higher_index():
    unscored = choose([_spec("scored", 10, 50.0), _spec("tiny", 1, None)], 10)
    assert _by_label(unscored) == {"strongest": "scored", "smallest": "tiny"}

    tied = choose([_spec("low", 4, 20.0), _spec("high", 4, 30.0)], 10)
    assert _by_label(tied)["smallest"] == "high"


def test_a_label_without_a_model_is_omitted():
    only_unscored = choose([_spec("tiny", 600_000_000, None)], 10)
    assert only_unscored["picks"] == [
        {"labels": ["smallest"], "model": "tiny", "reason": "Smallest model that fits (0.6B)."}
    ]

    near = choose([_spec("a", 10, 50.0), _spec("b", 6, 45.0)], 10)
    assert "smaller" not in _by_label(near)


def test_nothing_fitting_gives_no_picks_and_every_candidate_keeps_its_reason():
    catalog = list(models().values())

    result = choose(catalog, 40_000)

    assert result["picks"] == []
    by_id = {candidate["model"]: candidate for candidate in result["candidates"]}
    assert set(by_id) == {spec["id"] for spec in catalog}
    assert by_id["vllm-sr/d3"]["why_not"] == (
        "Longest input is about 40,000 tokens; context is 32,768."
    )


def test_a_decision_dataset_recommends_through_the_api(make_dataset):
    from test_decision_jobs import ROWS

    user = make_user()
    project = make_project(member=user)
    client = auth_client(user)
    dataset = make_dataset(project, ROWS)
    response = client.post(
        "/api/decision-jobs/recommend/",
        {"project": str(project.id), "dataset": str(dataset.id)},
        format="json",
    )
    assert response.status_code == 200, response.data
    assert response.data["picks"][0]["model"] == "vllm-sr/d3"
    assert response.data["picks"][0]["labels"] == ["strongest"]
    assert response.data["dataset"]["longest_input_tokens"] > 0
    assert {candidate["model"] for candidate in response.data["candidates"]} == set(models())


def test_length_counts_the_input_not_the_expected_output(make_dataset):
    from test_decision_jobs import TONE, URGENT

    # 255 option keys: the expected weights repeat every key, so counting them would
    # push this row past the 32,768-token context of the d3 model.
    options = [f"option-{index:03d}-" + "x" * 90 for index in range(255)]
    route = {
        "type": "choice",
        "instructions": "Pick the queue.",
        "criteria": dict.fromkeys(options, ""),
    }
    row = {
        "input": {
            "state": "Ticket: refund me now. " * 1_900,
            "questions": {"route": route, "urgent": URGENT, "tone": TONE},
        },
        "expected_output": {"route": dict.fromkeys(options, 1), "urgent": "false", "tone": 0},
    }
    dataset = make_dataset(make_project(member=make_user()), [row])

    result = recommend(dataset)

    assert result["picks"][0]["model"] == "vllm-sr/d3"
    assert result["dataset"]["longest_input_tokens"] * 1.1 <= 32_768


def test_the_ten_percent_margin_is_part_of_the_fit():
    result = choose([_spec("tight", 1, None, context=100)], 95)

    assert result["candidates"][0]["fits"] is False
    assert result["candidates"][0]["why_not"] == (
        "Longest input is about 95 tokens; with a 10% margin it needs 105; context is 100."
    )
