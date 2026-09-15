from __future__ import annotations

import json
import re
import uuid
from collections import defaultdict
from pathlib import Path

import pytest
from django.test import override_settings
from django.urls import reverse
from rest_framework.test import APIClient

from overbae.api.serializers import FinetuningModelCatalogResponseSerializer
from overbae.models import Project, ProjectMembership, User
from overbae.services.finetuning_catalog import fetch_finetuning_model_catalog
from overbae.services.recommendation import tier_models

_MODELS_JSON = Path(__file__).resolve().parents[1] / "overbae" / "modal" / "models.json"
_CATALOG: list[dict] = json.loads(_MODELS_JSON.read_text())["models"]

_GENERATION_PATTERNS = (
    re.compile(r"Qwen(\d+(?:\.\d+)?)"),
    re.compile(r"Llama-(\d+(?:\.\d+)?)"),
    re.compile(r"gemma-(\d+(?:\.\d+)?)"),
    re.compile(r"LFM(\d+(?:\.\d+)?)"),
)


@pytest.mark.django_db
@pytest.mark.parametrize("backend", ["baseten", "modal", "together"])
def test_api_models_endpoint_matches_shared_catalog_service(backend):
    user = User.objects.create_user(
        email=f"catalog-{uuid.uuid4().hex[:8]}@example.com",
        password="pass",
        clerk_user_id=f"clerk_{uuid.uuid4().hex}",
    )
    project = Project.objects.create(name="Catalog", slug=f"catalog-{uuid.uuid4().hex[:8]}")
    ProjectMembership.objects.create(user=user, project=project)
    client = APIClient()
    client.force_authenticate(user=user)

    with override_settings(FINETUNING_BACKEND=backend):
        response = client.get(reverse("finetuningjob-models"), {"has_tool_calling": "true"})
        expected = fetch_finetuning_model_catalog(has_tool_calling=True)

    assert response.status_code == 200
    assert response.data == expected


def test_api_catalog_response_schema_accepts_modal_backend():
    serializer = FinetuningModelCatalogResponseSerializer(
        data={
            "backend": "modal",
            "tiers": [],
            "models": {},
            "has_tool_calling": False,
            "max_context": None,
        }
    )

    assert serializer.is_valid(), serializer.errors


def test_catalog_carries_no_capability_scores():
    assert [m["id"] for m in _CATALOG if "capabilities" in m] == []

    with override_settings(FINETUNING_BACKEND="baseten"):
        exported = [m for models in tier_models().values() for m in models]
    assert [m["id"] for m in exported if "capabilities" in m] == []


def test_each_tier_keeps_a_vendor_contiguous():
    for key, model_ids in _tier_lists().items():
        vendors = [m.split("/")[0].lower() for m in model_ids]
        runs = [v for i, v in enumerate(vendors) if i == 0 or v != vendors[i - 1]]
        assert len(runs) == len(set(runs)), f"{key} interleaves vendors: {vendors}"


def test_each_tier_lists_newest_generation_first():
    # Generation numbers compare only within one vendor: Qwen 3.5 is not older than gemma 4.
    for key, model_ids in _tier_lists().items():
        by_vendor: dict[str, list[str]] = defaultdict(list)
        for model_id in model_ids:
            by_vendor[model_id.split("/")[0].lower()].append(model_id)
        for vendor, vendor_ids in by_vendor.items():
            with_generation = [(m, _generation(m)) for m in vendor_ids if _generation(m)]
            newest_first = sorted(with_generation, key=lambda pair: -pair[1])
            assert with_generation == newest_first, f"{key} {vendor} is not newest-first"


def _generation(model_id: str) -> float | None:
    name = model_id.split("/")[-1]
    for pattern in _GENERATION_PATTERNS:
        match = pattern.search(name)
        if match:
            return float(match.group(1))
    return None


def _tier_lists() -> dict[tuple[str, str], list[str]]:
    lists: dict[tuple[str, str], list[str]] = defaultdict(list)
    for entry in _CATALOG:
        lists[(entry["backend"], entry["tier"])].append(entry["id"])
    return lists
