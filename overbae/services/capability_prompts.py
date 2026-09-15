"""Materialize the capability page's flow-derived prompts into ``Prompt`` rows.

Flow prompts live on ``Capability.improvement_metadata``, not the ``Prompt`` table, so
an analyzed capability can render prompts on its page while the eval wizard's picker
sees zero rows.
"""

from __future__ import annotations

from django.db.models import QuerySet

from overbae.models import Capability, Prompt
from overbae.services.codebase.flow import build_capability_flow

_EXCERPT_LABEL_SUFFIX = " (excerpt — partial prompt)"


def _flow_prompt_sections(capability: Capability) -> list[dict[str, str]]:
    """System prompt + task prompts, falling back to the ``*_excerpt`` fields the
    capability page falls back to when the verbatim prompt was never captured.
    """
    flow = build_capability_flow(capability)
    capability_model = str(flow.get("model") or "").strip()
    sections: list[dict[str, str]] = []

    system_prompt = str(flow.get("system_prompt") or "").strip()
    is_excerpt = False
    if not system_prompt:
        system_prompt = str(flow.get("system_prompt_excerpt") or "").strip()
        is_excerpt = bool(system_prompt)
    if system_prompt:
        label = "System prompt"
        if is_excerpt:
            label += _EXCERPT_LABEL_SUFFIX
        sections.append({"label": label, "system_prompt": system_prompt, "model": capability_model})

    for index, mode in enumerate(flow.get("modes") or []):
        text = str(mode.get("prompt") or "").strip()
        is_excerpt = False
        if not text:
            text = str(mode.get("prompt_excerpt") or "").strip()
            is_excerpt = bool(text)
        if not text:
            continue
        name = str(mode.get("name") or "").strip()
        label = f"{name} task" if name else f"Task {index + 1}"
        if is_excerpt:
            label += _EXCERPT_LABEL_SUFFIX
        model = str(mode.get("model") or "").strip() or capability_model
        sections.append({"label": label, "system_prompt": text, "model": model})

    return sections


def sync_capability_prompts(capability: Capability) -> QuerySet[Prompt]:
    """Ensure flow-derived prompts exist as ``Prompt`` rows; return all rows for the capability.

    Idempotent: get-or-created on ``(capability, label, system_prompt)``, so a prompt a
    past run referenced is never mutated. ``version=0`` sorts flow prompts after
    real versioned snapshots.
    """
    for section in _flow_prompt_sections(capability):
        Prompt.objects.get_or_create(
            capability=capability,
            label=section["label"],
            system_prompt=section["system_prompt"],
            defaults={"model": section["model"], "version": 0},
        )
    return capability.prompts.order_by("-version", "created_at")
