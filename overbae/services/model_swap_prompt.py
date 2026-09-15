"""Copy-paste prompt that points a capability's code at a newly fine-tuned model."""

from __future__ import annotations

from collections import Counter

from django.db.models.expressions import RawSQL

from overbae.api.span_ordering import llm_model_sql
from overbae.models import Capability, DeployedModel, FinetuningJob, Span

CAPABILITY_ALIAS_PREFIX = "overmind/"
OBSERVED_MODEL_WINDOW = 500
OVERMIND_INFERENCE_BASE_URL = "https://api.overmindlab.ai/api/v1"
OVERMIND_API_KEY_ENV = "OVERMIND_API_KEY"


def _job_deployed_model(job: FinetuningJob) -> DeployedModel | None:
    try:
        return job.deployed_model
    except DeployedModel.DoesNotExist:
        return None


def capability_alias(capability_id) -> str:
    return f"{CAPABILITY_ALIAS_PREFIX}{capability_id}"


def observed_capability_model(capability: Capability) -> str:
    """The model most often seen in this capability's LLM spans, or ``""``."""
    recent = list(
        Span.objects.filter(capability_id=capability.pk, span_type=Span.SpanType.LLM_CALL)
        .annotate(_model=RawSQL(f"TRIM({llm_model_sql()})", []))
        .exclude(_model=None)
        .exclude(_model="")
        .order_by("-start_time_ns", "_model")
        .values_list("_model", flat=True)[:OBSERVED_MODEL_WINDOW]
    )
    if not recent:
        return ""
    aliased = next((model for model in recent if model.startswith(CAPABILITY_ALIAS_PREFIX)), "")
    return aliased or Counter(recent).most_common(1)[0][0]


def current_capability_model(job: FinetuningJob, capability: Capability | None = None) -> str:
    capability = capability or (job.capability if job.capability_id else None)
    if capability is None:
        return job.base_model
    return observed_capability_model(capability) or capability.model or job.base_model


def resolve_swap_capability(job: FinetuningJob) -> tuple[Capability | None, str | None]:
    """The capability whose code this prompt retargets — ``(capability, error)``."""
    if job.capability_id:
        return job.capability, None

    candidates = list(Capability.objects.filter(project_id=job.project_id).current()[:2])
    if len(candidates) == 1:
        return candidates[0], None
    if not candidates:
        return None, "This training run is not linked to a capability and its project has none."
    return None, "This training run is not linked to a capability and its project has several."


def alias_target_deployment(
    job: FinetuningJob, capability: Capability
) -> tuple[DeployedModel | None, str | None]:
    """The deployment the alias will answer with — ``(deployed, error)``."""
    deployed = _job_deployed_model(job)
    if deployed is None:
        return None, "This training run has no deployed model."
    if deployed.status != DeployedModel.Status.READY:
        return None, f"{deployed.model_id} is not ready to serve (status: {deployed.status})."
    if str(deployed.project_id) != str(capability.project_id):
        return None, "The deployed model belongs to a different project than the capability."
    return deployed, None


def build_model_swap_prompt(
    *,
    capability_name: str,
    source_path: str,
    old_model: str,
    new_model: str,
    is_alias: bool = True,
) -> str:
    """Instructions a coding agent pastes locally to retarget the capability."""
    if is_alias:
        headline = (
            f"You are pointing the AI agent '{capability_name}' in this repository at its "
            f"permanent Overmind model alias, served by Overmind's OpenAI-compatible "
            f"inference API."
        )
        identifier_note = (
            f'Permanent model identifier: "{new_model}"\n'
            f"This identifier is STABLE AND PERMANENT. It names the capability, not a model: "
            f"Overmind resolves it server-side to whichever fine-tuned model is live for "
            f"'{capability_name}' right now, and it will NOT change when new models are "
            f"fine-tuned. This is a one-time change to the model line — it never needs "
            f"updating again.\n"
        )
        stability_rules = (
            f"6. Because the identifier never changes, write it as a plain value. Do NOT "
            f"name a specific fine-tune, model version, training run, or date in a "
            f"comment beside it, and do NOT introduce a constant whose name implies it "
            f"needs future maintenance (no LATEST_MODEL, CURRENT_MODEL, MODEL_VERSION, "
            f"or similar). A short comment naming the capability — e.g. "
            f"`# {capability_name} — Overmind alias, retargeted from the dashboard` — is "
            f'welcome; anything that reads as "update me later" is wrong.\n'
        )
    else:
        headline = (
            f"You are pinning the AI agent '{capability_name}' in this repository to one exact "
            f"fine-tuned model served by Overmind's OpenAI-compatible inference API."
        )
        identifier_note = (
            f'Exact model identifier to pin: "{new_model}"\n'
            f"This names one specific deployment. It is a deliberate hard pin, so it will "
            f"need a new change whenever the capability should run a different model.\n"
        )
        stability_rules = (
            "6. This is an intentional pin to one exact model, so a comment naming the "
            "model or the training run is appropriate here.\n"
        )

    return (
        f"{headline}\n\n"
        f"Capability source location hint: {source_path or '(unknown — search the repo)'}\n"
        f'Model identifier currently in the code: "{old_model}"\n'
        f"{identifier_note}"
        f"Overmind inference base URL: {OVERMIND_INFERENCE_BASE_URL}\n"
        f"Overmind API key env var: {OVERMIND_API_KEY_ENV} (an 'ovr_...' key, sent as "
        f"'Authorization: Bearer')\n\n"
        "Task — make the capability's LLM calls run through Overmind:\n"
        f"1. Find where this capability configures its LLM client and model (model name "
        f"string, config value, or env-var default).\n"
        f'2. Set the model parameter of the capability\'s LLM calls to "{new_model}".\n'
        f'3. The "currently in the code" identifier above is derived from this capability\'s '
        f"observed traffic and may be stale or missing. Whatever model string you "
        f"actually find at that call site is the one to replace — do not stop because "
        f'"{old_model}" is not present verbatim.\n'
        f"4. Point the OpenAI(-compatible) client at Overmind: in Python, "
        f'OpenAI(base_url="{OVERMIND_INFERENCE_BASE_URL}", '
        f'api_key=os.environ["{OVERMIND_API_KEY_ENV}"]); in TypeScript, '
        f'new OpenAI({{ baseURL: "{OVERMIND_INFERENCE_BASE_URL}", '
        f"apiKey: process.env.{OVERMIND_API_KEY_ENV} }}); or the equivalent for the "
        f"client this codebase uses.\n"
        f"5. Authenticate with the Overmind API key read from the "
        f"{OVERMIND_API_KEY_ENV} environment variable (reuse it if the codebase "
        f"already reads it, e.g. for Overmind tracing — it is the same key). Never "
        f"hardcode a key.\n"
        f"{stability_rules}"
        f"7. If the codebase calls Overmind's tracing SDK (overmind.init), leave that "
        f"intact — only the inference client changes.\n"
        f"8. If the call site uses a non-OpenAI provider SDK (e.g. an Anthropic "
        f"client), convert that call site to the OpenAI SDK pointed at Overmind, "
        f"preserving the existing message construction and behavior.\n"
        f"9. The endpoint is OpenAI-compatible (POST /api/v1/chat/completions): "
        f"streaming and standard parameters (temperature, max_tokens, top_p, "
        f"frequency_penalty, presence_penalty) work unchanged, so preserve all "
        f"existing call parameters — only the model, base URL, and API key source "
        f"change.\n"
        f"10. Update any model-name constants, config files, or env samples (e.g. "
        f".env.example) that still carry the old model identifier, and add "
        f"{OVERMIND_API_KEY_ENV} to the env sample if one exists and it is missing.\n"
        f"11. Keep the diff minimal and focused (client config + model id). Do not "
        f"refactor, rename, reformat, or touch unrelated code, and do not create new "
        f"files."
    )


def model_swap_prompt_for_job(
    job: FinetuningJob, *, pin: bool = False
) -> tuple[dict | None, str | None]:
    """Build the copy-paste prompt for a succeeded finetune. ``(payload, error)``."""
    if job.status != job.Status.SUCCEEDED:
        return None, "Only successfully trained models can be shipped to the codebase."

    capability, capability_error = resolve_swap_capability(job)
    if capability is None:
        return None, capability_error

    deployed, deployed_error = alias_target_deployment(job, capability)
    if deployed is None:
        return None, deployed_error

    new_model = deployed.model_id if pin else capability_alias(capability.id)

    old_model = current_capability_model(job, capability)
    prompt = build_model_swap_prompt(
        capability_name=capability.name,
        source_path=capability.source_path or "",
        old_model=old_model,
        new_model=new_model,
        is_alias=not pin,
    )
    return {
        "prompt": prompt,
        "pin": pin,
        "capability_id": str(capability.id),
        "capability_name": capability.name,
        "old_model": old_model,
        "new_model": new_model,
    }, None
