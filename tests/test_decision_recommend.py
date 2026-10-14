from __future__ import annotations

import pytest
from factories import auth_client, make_project, make_user

from overbae.modal.decision_catalog import models
from overbae.services.decision_recommend import choose, recommend

pytestmark = pytest.mark.django_db


def test_the_best_fitting_index_wins_and_unscored_models_are_the_fallback():
    specs = [
        {
            "id": "small",
            "params": 1,
            "max_context_length": 100,
            "decision_index": 10,
            "decision_index_edition": "0.3.2",
        },
        {
            "id": "better",
            "params": 2,
            "max_context_length": 100,
            "decision_index": 20,
            "decision_index_edition": "0.3.2",
        },
        {
            "id": "wide",
            "params": 3,
            "max_context_length": 1000,
            "decision_index": 5,
            "decision_index_edition": "0.3.1",
        },
        {"id": "unscored", "params": 1, "max_context_length": 1000},
    ]

    assert choose(specs, 50)["model"] == "better"
    wide = choose(specs, 200)
    assert wide["model"] == "wide"
    assert wide["candidates"][2]["decision_index_edition"] == "0.3.1"
    assert choose([{"id": "unscored", "params": 1, "max_context_length": 1000}], 200)["model"] == (
        "unscored"
    )
    assert choose(specs, 5000)["model"] is None
    tied = choose(
        [
            {
                "id": "larger",
                "params": 10,
                "max_context_length": 100,
                "decision_index": 38.9,
                "decision_index_edition": "0.3.2",
            },
            {
                "id": "smaller",
                "params": 5,
                "max_context_length": 100,
                "decision_index": 38.9,
                "decision_index_edition": "0.3.2",
            },
        ],
        10,
    )
    assert tied["model"] == "smaller"


def test_catalog_scores_prefer_d3_until_the_row_fits_nobody():
    specs = list(models().values())
    short = choose(specs, 100)
    assert short["model"] == "vllm-sr/d3"
    assert "66.3" in short["reason"]
    assert choose(specs, 20_000)["model"] == "vllm-sr/d3"
    assert choose(specs, 40_000)["model"] is None
    assert all(
        spec.get("decision_index") is None
        for spec in specs
        if spec["id"].startswith("OvermindLab/")
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
    assert response.data["model"] == "vllm-sr/d3"
    assert response.data["dataset"]["longest_input_tokens"] > 0
    assert {candidate["model"] for candidate in response.data["candidates"]} == set(models())


def test_a_model_whose_limits_the_dataset_exceeds_is_not_recommended():
    limits = {"options": 26, "score_levels": 10, "questions": 64}
    specs = [
        {
            "id": "narrow",
            "params": 1,
            "max_context_length": 100,
            "decision_index": 50,
            "decision_index_edition": "0.3.2",
            "limits": limits,
        },
        {
            "id": "wide",
            "params": 2,
            "max_context_length": 100,
            "decision_index": 10,
            "decision_index_edition": "0.3.2",
            "limits": {"options": 255, "score_levels": 255, "questions": 100},
        },
    ]
    within = {"options": 26, "score_levels": 2, "questions": 1}
    over = {"options": 27, "score_levels": 2, "questions": 1}

    assert choose(specs, 50, shape=within)["model"] == "narrow"
    assert choose(specs, 50, shape=over)["model"] == "wide"


def _spec(model_id, params, index, *, context=100, limits=None):
    spec = {"id": model_id, "params": params, "max_context_length": context}
    if index is not None:
        spec["decision_index"] = index
        spec["decision_index_edition"] = "0.3.2"
    if limits is not None:
        spec["limits"] = limits
    return spec


def test_within_three_points_the_smallest_fitting_model_is_chosen():
    result = choose([_spec("bigger", 10, 50.0), _spec("smaller", 5, 48.5)], 10)

    assert result["model"] == "smaller"
    assert result["reason"] == (
        "smaller: smallest of 2 models within 3 points of the best Decision Index (50)."
    )


def test_a_model_more_than_three_points_below_the_best_is_not_chosen_even_if_smaller():
    assert choose([_spec("bigger", 10, 50.0), _spec("far", 1, 46.9)], 10)["model"] == "bigger"


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

    assert result["model"] == "vllm-sr/d3"
    assert result["dataset"]["longest_input_tokens"] * 1.1 <= 32_768


def test_models_that_do_not_fit_stay_in_candidates_with_the_reason_they_do_not():
    catalog = list(models().values())

    long_rows = choose(catalog, 40_000)
    by_id = {candidate["model"]: candidate for candidate in long_rows["candidates"]}
    assert {candidate["model"] for candidate in long_rows["candidates"]} == {
        spec["id"] for spec in catalog
    }
    assert long_rows["model"] is None
    assert by_id["vllm-sr/d3"]["fits"] is False
    assert by_id["vllm-sr/d3"]["why_not"] == (
        "Longest input is about 40,000 tokens; context is 32,768."
    )

    wide = choose(catalog, 25_000, shape={"options": 27, "score_levels": 2, "questions": 1})
    clef = next(
        candidate for candidate in wide["candidates"] if candidate["model"] == "Cloudflare/clef"
    )
    assert clef == {
        "model": "Cloudflare/clef",
        "fits": False,
        "why_not": "Takes up to 26 options; this dataset has 27.",
        "decision_index": 53.08,
        "decision_index_edition": "0.3.1",
        "params": 27_400_000_000,
    }
    assert wide["model"] == "vllm-sr/d3"


def test_the_ten_percent_margin_is_part_of_the_fit():
    result = choose([_spec("tight", 1, None, context=100)], 95)

    assert result["candidates"][0]["fits"] is False
    assert result["candidates"][0]["why_not"] == (
        "Longest input is about 95 tokens; with a 10% margin it needs 105; context is 100."
    )
