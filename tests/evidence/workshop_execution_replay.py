"""Run with manage.py shell: REPLAY_CASE=document|native|explore, or REPLAY_PROJECT=<id>."""

import asyncio
import json
import os
import uuid

from django.test import override_settings
from rest_framework.test import APIClient

from overbae.models import APIToken, BillingTelemetry, Dataset, Project, ProjectMembership, User
from overbae.services.datasets import paths, store
from overbae.services.mcp.catalog import CATALOG
from overbae.services.mcp.context import MCPContext


def inspect(project):
    user = ProjectMembership.objects.select_related("user").get(project=project).user
    context = MCPContext(
        user=user,
        project=project,
        token=APIToken(
            scope={
                "scope": "project",
                "resourceIds": [str(project.pk)],
                "permission": ["read", "write"],
            }
        ),
    )
    results = []
    for dataset in Dataset.objects.filter(project=project).select_related("active", "project"):
        call = asyncio.run(CATALOG.call("inspect_dataset", {"dataset": str(dataset.pk)}, context))
        assert not call.isError, call.structuredContent
        active, source = dataset.active_cell, dataset.source
        output = list(store.iter_rows(paths.cell_path(dataset.pk, active.pk))) if active else []
        original = list(store.iter_rows(paths.cell_path(dataset.pk, source.pk))) if source else []
        turn = (dataset.chat or [{}])[-1]
        errors = [
            {"turn": item.get("id"), **step}
            for item in dataset.chat or []
            for step in item.get("steps", [])
            if step.get("status") == "error" or step.get("ok") is False
        ]
        audit_batches = {}
        for report in dataset.cells.values_list("quality_report", flat=True):
            for batch in (report or {}).get("semantic_audit", {}).get("batches", []):
                audit_batches[batch["id"]] = len(batch["source_rows"])
        preserved_native = (
            len(output) == len(original)
            and all(
                all(
                    after.get("decision", {}).get(key) == value
                    for key, value in before.get("decision", {}).items()
                )
                and after.get("group_id") == before.get("group_id")
                for before, after in zip(original, output, strict=True)
            )
            if "Native" in dataset.name
            else None
        )
        results.append(
            {
                "dataset": str(dataset.pk),
                "name": dataset.name,
                "state": dataset.state,
                "error": dataset.error,
                "source_intact": store.file_sha256(paths.cell_path(dataset.pk, source.pk))
                == source.fingerprint
                if source
                else None,
                "source_rows": len(original),
                "output_rows": len(output),
                "compatible": active.fits(dataset.intent)[0] if active else False,
                "unique_ids": len({row.get(store.SOURCE_ROW) for row in output}) == len(output),
                "workflow": call.structuredContent.get("workflow"),
                "turn_status": turn.get("status"),
                "turn_error": turn.get("error"),
                "text": turn.get("text"),
                "turns": [
                    {
                        key: item.get(key)
                        for key in ("id", "at", "ms", "engine", "model", "status", "error")
                    }
                    for item in dataset.chat or []
                ],
                "agent_elapsed_ms": sum(item.get("ms", 0) or 0 for item in dataset.chat or []),
                "failed_steps": errors,
                "quality": active.quality_report if active else None,
                "semantic_row_budget": dataset.preparation_plan.get("specification", {}).get(
                    "semantic_row_budget"
                ),
                "semantic_rows_reserved": dataset.preparation_plan.get("semantic_rows_reserved", 0),
                "semantic_rows_judged_across_versions": sum(audit_batches.values()),
                "semantic_audit_batch_ids": list(audit_batches),
                "document_attribution_complete": all(
                    row.get("_overmind_document_id") and row.get("source_name") for row in output
                )
                if "Document" in dataset.name
                else None,
                "generated_rows_marked_not_human_reviewed": all(
                    row.get("human_reviewed") is False for row in output
                )
                if "Document" in dataset.name
                else None,
                "native_targets_preserved": preserved_native,
                "exploration_source_unchanged": output == original
                if dataset.intent == "explore"
                else None,
            }
        )
    print(
        json.dumps(
            {
                "project": str(project.pk),
                "datasets": results,
                "recorded_ledger": list(
                    BillingTelemetry.objects.filter(project=project).values(
                        "id", "service", "amount", "timestamp"
                    )
                ),
                "cost_scope": "Recorded platform ledger only; missing provider usage and infrastructure charges are not a complete invoice.",
            },
            default=str,
        )
    )


project_id = os.environ.get("REPLAY_PROJECT")
if project_id:
    inspect(Project.objects.get(pk=project_id))
else:
    case = os.environ.get("REPLAY_CASE", "document")
    unique = uuid.uuid4().hex[:12]
    user = User.objects.create_user(
        email=f"workshop-acceptance-{unique}@example.test", password=None
    )
    project = Project.objects.create(
        name=f"Workshop acceptance {case} {unique}", slug=f"workshop-acceptance-{unique}"
    )
    ProjectMembership.objects.create(project=project, user=user)
    client = APIClient()
    client.force_authenticate(user=user)
    with override_settings(ALLOWED_HOSTS=["testserver", "localhost", "127.0.0.1"]):
        if case == "document":
            text = "# Fictional branch handbook\n\n" + "\n\n".join(
                f"## Branch {i}\nBranch {i} opens at {8 + i % 3}:00 and closes at {17 + i % 3}:00. Its book loan lasts {10 + i} days. Returns go in the blue box beside entrance {i}. Its contact extension is {100 + i}."
                for i in range(20)
            )
            content = text.encode()
            upload = client.post("/api/uploads/", {"filename": "branch-handbook.md"}, format="json")
            assert upload.status_code == 201, upload.data
            upload_id = upload.data["upload_id"]
            sent = client.put(
                f"/api/uploads/{upload_id}/chunk/?offset=0",
                content,
                content_type="application/octet-stream",
            )
            assert sent.status_code == 200, sent.data
            ready = client.post(
                f"/api/uploads/{upload_id}/inspect/", {"size": len(content)}, format="json"
            )
            assert ready.status_code == 200, ready.data
            payload = {
                "name": "Document derivation acceptance",
                "intent": "train",
                "source": {"uploads": [upload_id]},
                "brief": "Prepare 32 distinct source-grounded training question/answer examples from this fictional handbook. The eventual model receives the relevant passage and question. Cover different branches and facts, preserve source evidence, check answer support on a bounded representative sample, and report any unmet coverage or count. Finish the work autonomously.",
            }
        elif case == "native":
            rows = [
                {
                    "decision": {
                        "state": "" if i % 2 else f"Observation {i}",
                        "question": "Choose an outcome",
                        "kind": "choice",
                        "options": ["negative", "positive"],
                        "target_probabilities": [0.3, 0.7],
                    },
                    "family": "publisher",
                    "group_id": f"case-{i // 2}",
                }
                for i in range(24)
            ]
            rows[1] = rows[0].copy()
            payload = {
                "name": "Native preservation acceptance",
                "intent": "train",
                "source": {"rows": rows},
                "brief": "Prepare these 24 observations for native decision training. The probability vectors are supplied annotator distributions. Preserve every observation, including duplicates, option order, blank states and full targets. Do not rejudge or regenerate labels. Verify compatibility and preservation; no new examples are requested.",
            }
        else:
            payload = {
                "name": "Exploration acceptance",
                "intent": "explore",
                "source": {
                    "rows": [
                        {
                            "region": "north" if i < 137 else "south",
                            "volume": i * 2,
                            "note": None if i % 9 == 0 else "observed",
                        }
                        for i in range(150)
                    ]
                },
                "brief": "Explore the entire table, report regional coverage and missing notes. Preserve the source. No training target or generated answers are requested. Record measured checks and finish.",
            }
        response = client.post(
            "/api/datasets/",
            {"project": str(project.pk), "capability": None, **payload},
            format="json",
        )
        assert response.status_code == 201, response.data
    print(
        json.dumps(
            {
                "project": str(project.pk),
                "dataset": response.data["id"],
                "case": case,
                "provider_mocked": False,
                "worker_mocked": False,
            }
        )
    )
