from __future__ import annotations

import pytest
from factories import auth_client, make_project, make_user

from overbae.modal.decision_catalog import models
from overbae.services.decision_recommend import choose

pytestmark = pytest.mark.django_db


def test_the_highest_published_index_that_fits_is_chosen():
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
    assert "0.3.1" in wide["reason"]
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
    assert response.data["longest_row_tokens"] > 0
