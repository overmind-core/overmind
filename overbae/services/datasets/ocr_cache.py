from __future__ import annotations

import json
from datetime import timedelta

from django.utils import timezone

from overbae.models import DocumentExtractionPage
from sandbox.documents.checkpoints import MAX_CACHE_BYTES, MAX_PAGE_BYTES, digest

RETENTION_DAYS = 30


def export(project_id, document, languages, output):
    if project_id is None:
        return
    records = DocumentExtractionPage.objects.filter(
        project_id=project_id,
        document_sha256=document,
        options_sha256=digest(languages),
        created_at__gte=timezone.now() - timedelta(days=RETENTION_DAYS),
    )
    recipe = records.order_by("-created_at").values_list("recipe_sha256", flat=True).first()
    for page in records.filter(recipe_sha256=recipe).order_by("page").iterator(chunk_size=10):
        value = {
            "recipe_sha256": page.recipe_sha256,
            "page": page.page,
            "result": page.result,
            "result_sha256": page.result_sha256,
        }
        line = (json.dumps(value) + "\n").encode()
        if output.tell() + len(line) > MAX_CACHE_BYTES:
            break
        output.write(line)


def save(project_id, document, languages, value):
    if project_id is None:
        return
    result = value.get("result")
    recipe = value.get("recipe_sha256", "")
    page = value.get("page")
    if (
        not isinstance(result, dict)
        or not isinstance(result.get("rows"), list)
        or not isinstance(page, int)
        or isinstance(page, bool)
        or not 1 <= page <= 2000
        or not isinstance(recipe, str)
        or len(recipe) != 64
        or any(c not in "0123456789abcdef" for c in recipe)
        or value.get("result_sha256") != digest(result)
        or len(json.dumps(value).encode()) > MAX_PAGE_BYTES
    ):
        raise ValueError("The parser returned an invalid OCR checkpoint.")
    DocumentExtractionPage.objects.update_or_create(
        project_id=project_id,
        document_sha256=document,
        options_sha256=digest(languages),
        recipe_sha256=recipe,
        page=page,
        defaults={
            "result": result,
            "result_sha256": value["result_sha256"],
            "created_at": timezone.now(),
        },
    )


def prune():
    # Bound each maintenance pass; a later pass continues any backlog.
    ids = list(
        DocumentExtractionPage.objects.filter(
            created_at__lt=timezone.now() - timedelta(days=RETENTION_DAYS)
        ).values_list("pk", flat=True)[:1000]
    )
    return DocumentExtractionPage.objects.filter(pk__in=ids).delete()[0]
