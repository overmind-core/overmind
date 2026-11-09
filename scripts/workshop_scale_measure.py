import hashlib
import json
import os

from overbae.models import WorkshopTask

for ident in os.environ["TRIAL_TASKS"].split(","):
    task = WorkshopTask.objects.get(pk=ident)
    actions = list(task.actions.order_by("created_at"))
    calls = []
    for action in actions:
        if action.name != "reason":
            continue
        response = action.receipt.get("response", {})
        body = response.get("body", {})
        usage = body.get("usage") or {}
        cost = usage.get("cost")
        calls.append(
            {
                "sequence": action.sequence,
                "state": action.state,
                "seconds": (action.updated_at - action.created_at).total_seconds()
                if action.state == "completed"
                else None,
                "model": body.get("model"),
                "tier": body.get("service_tier"),
                "usage": usage,
                "reported_cost_usd": cost,
                "transport": response.get("transport"),
                "error": response.get("error") or body.get("error"),
                "tool_calls": [
                    {
                        "name": item["name"],
                        "summary": json.loads(item["arguments"]).get("summary"),
                        "code_chars": len(json.loads(item["arguments"]).get("code", "")),
                    }
                    for item in body.get("output", [])
                    if item.get("type") == "function_call"
                ],
            }
        )
    publication = next(
        (
            action
            for action in actions
            if action.name == "publish_workshop" and action.state == "completed"
        ),
        None,
    )
    print(
        json.dumps(
            {
                "task": str(task.pk),
                "briefing_metrics": actions[0].result.get("briefing_metrics") if actions else None,
                "staging_metrics": actions[0].result.get("staging_metrics") if actions else None,
                "runtime": actions[0].result.get("runtime", {}).get("identity")
                if actions
                else None,
                "runtime_contracts_sha256": hashlib.sha256(
                    json.dumps(
                        actions[0].result.get("runtime_contracts", {}) if actions else {},
                        sort_keys=True,
                    ).encode()
                ).hexdigest(),
                "source_briefing_bytes": len(
                    json.dumps(
                        actions[0].result.get("source_briefing", {}) if actions else {}
                    ).encode()
                ),
                "created_at": task.created_at,
                "updated_at": task.updated_at,
                "dataset": str(task.dataset_id),
                "state": task.state,
                "elapsed_seconds": (task.updated_at - task.created_at).total_seconds()
                if task.state in {"completed", "blocked", "failed", "cancelled"}
                else None,
                "publication_seconds": (publication.updated_at - task.created_at).total_seconds()
                if publication
                else None,
                "actions": [
                    {
                        "name": a.name,
                        "state": a.state,
                        "seconds": (a.updated_at - a.created_at).total_seconds()
                        if a.state == "completed"
                        else None,
                    }
                    for a in actions
                ],
                "model_seconds": sum(call["seconds"] for call in calls)
                if calls and all(call["seconds"] is not None for call in calls)
                else None,
                "known_model_seconds": sum(
                    call["seconds"] for call in calls if call["seconds"] is not None
                ),
                "reported_cost_lower_bound_usd": sum(
                    call["reported_cost_usd"]
                    for call in calls
                    if call["reported_cost_usd"] is not None
                ),
                "reported_cost_usd": sum(call["reported_cost_usd"] for call in calls)
                if calls and all(call["reported_cost_usd"] is not None for call in calls)
                else None,
                "calls": calls,
                "result": task.result,
                "publication": publication.result if publication else None,
            },
            default=str,
        )
    )
