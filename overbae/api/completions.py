"""OpenAI-compatible chat completions and models endpoints.

Errors keep OpenAI's ``{"error": {...}}`` shape, never DRF's ``detail``, so
third-party SDKs keep working — that is why this module gates credits itself
instead of calling ``credit_gate``. Non-finetuned model ids route to OpenRouter;
deployed fine-tunes route to Modal vLLM.
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from decimal import Decimal
from typing import Any

import requests as _requests
from django.conf import settings
from django.db.models import F
from django.http import StreamingHttpResponse
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response

from modal_shared.context_budget import reserve_output
from overbae.api.scoping import project_ids_for
from overbae.api.streaming import (
    JSON_IDLE_PING,
    SSE_IDLE_PING,
    AsyncStream,
    iter_keeping_idle_alive,
)
from overbae.core.model_registry import inference_models, is_inference_model
from overbae.models import (
    APIToken,
    BillingService,
    Capability,
    DeployedModel,
    User,
)
from overbae.services.billing_ledger import InsufficientCredits, charge_credits, ensure_credits
from overbae.services.deployed_chat import is_cold_start, record_inference_call
from overbae.services.inference_client import (
    ContextBudgetError,
    InferenceClient,
    InferenceClientError,
    get_inference_client,
)
from overbae.services.model_catalog import estimate_cost, resolve_bare_openrouter_slug

logger = logging.getLogger(__name__)


def _credits_required_response() -> Response:
    return Response(
        {
            "error": {
                "message": "Insufficient credits.",
                "type": "insufficient_credits",
            }
        },
        status=status.HTTP_402_PAYMENT_REQUIRED,
    )


def _charge_frontier_usage(user: User, model_id: str, usage: dict | None) -> None:
    """Prefers OpenRouter's own ``usage.cost`` so a routed model is billed exactly
    what OpenRouter charged, else catalog pricing. A model with no known price is
    not charged at all, never charged zero. Never raises."""
    if not usage:
        return
    try:
        reported = usage.get("cost")
        cost = (
            reported
            if isinstance(reported, (int, float)) and reported > 0
            else estimate_cost(
                model_id,
                int(usage.get("prompt_tokens") or 0),
                int(usage.get("completion_tokens") or 0),
            )
        )
        if not cost:
            return
        charge_credits(
            user,
            Decimal(str(cost)),
            BillingService.INFERENCE,
            idempotency_key=f"inference:{user.pk}:{uuid.uuid4()}",
            metadata={"model_id": model_id, "usage": usage},
        )
    except Exception:
        logger.exception("Failed to charge frontier inference for user_id=%s", user.pk)


def _process_stream_chunk(raw: str, captured: dict[str, Any], include_usage: bool) -> str | None:
    """Capture the final usage + per-request ``metrics`` from a streamed SSE line.

    ``--enable-force-include-usage`` puts ``usage`` on *every* vLLM chunk, so
    usage alone does not mark a chunk as terminal. Returns the line to forward,
    or None to drop it.
    """
    stripped = raw.strip()
    if not stripped.startswith("data:"):
        return raw
    payload = stripped[len("data:") :].strip()
    if payload == "[DONE]":
        return raw
    try:
        obj = json.loads(payload)
    except (json.JSONDecodeError, TypeError):
        return raw
    if not isinstance(obj, dict):
        return raw

    metrics = obj.get("metrics")
    if metrics:
        captured["metrics"] = metrics

    usage = obj.get("usage")
    if usage:
        # Last writer wins: the counts are cumulative, so the final chunk holds
        # the totals billing needs.
        captured["usage"] = usage
        if not include_usage:
            # Content chunks carry forced usage too, so strip those rather than
            # drop them; only a usage-only terminal chunk is safe to drop.
            if not obj.get("choices"):
                return None
            obj.pop("usage", None)
            obj.pop("metrics", None)
            return f"data: {json.dumps(obj)}\n\n"

    # Strip our injected metrics so the client only sees standard OpenAI fields.
    if metrics is not None:
        obj.pop("metrics", None)
        return f"data: {json.dumps(obj)}\n\n"
    return raw


OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
_OPENROUTER_TIMEOUT = 120

# Model-comparison runs send this header to reach ANY OpenRouter-served slug;
OPTIMISER_ROUTING_HEADER = "X-Overmind-Optimiser"


def _openrouter_headers() -> dict:
    return {
        "Authorization": f"Bearer {settings.OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://overmindlab.ai",
    }


def _frontier_non_stream(
    model_id: str, payload: dict
) -> tuple[dict | None, str | None, int | None]:
    try:
        resp = _requests.post(
            OPENROUTER_URL,
            headers=_openrouter_headers(),
            json=payload,
            timeout=_OPENROUTER_TIMEOUT,
        )
    except _requests.exceptions.RequestException as exc:
        return None, f"OpenRouter request failed: {exc}", None
    if not resp.ok:
        # The body stays server-side: it can carry request ids and URLs.
        logger.error("OpenRouter %s for model %s: %s", resp.status_code, model_id, resp.text[:400])
        return (
            None,
            f"The model provider returned an error (HTTP {resp.status_code}).",
            resp.status_code,
        )
    try:
        return resp.json(), None, None
    except ValueError:
        logger.error("OpenRouter non-JSON 200 for model %s: %r", model_id, resp.text[:200])
        return None, "The model provider returned an invalid response. Try again.", None


def _frontier_stream(payload: dict):
    try:
        resp = _requests.post(
            OPENROUTER_URL,
            headers=_openrouter_headers(),
            json=payload,
            timeout=_OPENROUTER_TIMEOUT,
            stream=True,
        )
        if not resp.ok:
            yield f'data: {{"error": {{"message": "OpenRouter returned {resp.status_code}", "type": "server_error"}}}}\n\n'
            yield "data: [DONE]\n\n"
            return
        for line in resp.iter_lines():
            if line:
                decoded = line.decode("utf-8") if isinstance(line, bytes) else line
                yield decoded + "\n\n"
    except _requests.exceptions.RequestException as exc:
        logger.error("OpenRouter streaming error: %s", exc)
        yield 'data: {"error": {"message": "OpenRouter request failed.", "type": "server_error"}}\n\n'
        # Terminate so SSE clients waiting for the sentinel don't hang.
        yield "data: [DONE]\n\n"


# Modal GPU cold-starts can take up to 5 minutes without a memory snapshot.
INFERENCE_TIMEOUT_S = 600

_inference_client: InferenceClient | None = None


def _get_client() -> InferenceClient:
    global _inference_client
    if _inference_client is None:
        _inference_client = InferenceClient(timeout=INFERENCE_TIMEOUT_S)
    return _inference_client


def _apply_family_infer_defaults(
    extra: dict,
    *,
    model_id: str,
    base_model_id: str = "",
    max_tokens: int | None = None,
) -> int | None:
    """Fill reasoning defaults from FamilySpec when the client omitted them.

    gpt-oss Harmony rejects ``reasoning_effort=none``; we use low effort and
    ``include_reasoning=false`` so CoT stays out of the response and short
    max_tokens budgets still reach the final channel.

    Returns the possibly-raised ``max_tokens`` — some families spend a chunk
    of the budget on a preamble that can't be switched fully off (see
    ``FamilySpec.min_max_tokens``), and below that floor content and
    reasoning both come back null.
    """
    from modal_shared.modelfam import resolve  # noqa: PLC0415 — keep completions light

    spec = resolve(base_model_id or model_id)
    if spec.suppress_reasoning_output and "include_reasoning" not in extra:
        extra["include_reasoning"] = False
    if spec.default_reasoning_effort and "reasoning_effort" not in extra:
        extra["reasoning_effort"] = spec.default_reasoning_effort
    if spec.default_chat_template_kwargs:
        ctk = dict(extra.get("chat_template_kwargs") or {})
        for key, value in spec.default_chat_template_kwargs:
            ctk.setdefault(key, value)
        extra["chat_template_kwargs"] = ctk
    if spec.min_max_tokens and max_tokens is not None and max_tokens < spec.min_max_tokens:
        max_tokens = spec.min_max_tokens
    return max_tokens


CAPABILITY_ALIAS_PREFIX = "overmind/"


def _resolve_alias(request: Request, model_id: str) -> tuple[DeployedModel | None, Response | None]:
    """Resolve ``overmind/<capability-uuid>`` to the capability's active deployment.

    ``(deployment, None)`` on a hit, ``(None, None)`` when ``model_id`` is not an
    alias at all, ``(None, error_response)`` when it names nothing routable.
    """
    if not model_id.startswith(CAPABILITY_ALIAS_PREFIX):
        return None, None

    try:
        capability_id = uuid.UUID(model_id[len(CAPABILITY_ALIAS_PREFIX) :])
    except ValueError:
        return None, Response(
            {
                "error": {
                    "message": (
                        f"Invalid capability alias '{model_id}'. The alias is "
                        "overmind/<capability-uuid> — copy it from the capability's Models tab."
                    ),
                    "type": "invalid_request_error",
                }
            },
            status=status.HTTP_400_BAD_REQUEST,
        )

    # Absent, leftover, deleted, and other-tenant aliases all read the same 404
    # so the alias never confirms existence across a project boundary.
    capability = Capability.objects.filter(
        id=capability_id, project_id__in=project_ids_for(request.user, request.auth)
    ).first()
    if capability is not None and capability.status == Capability.Status.CURRENT:
        capability = Capability.objects.select_related("active_model").get(pk=capability.pk)
    else:
        return None, Response(
            {
                "error": {
                    "message": f"Unknown capability alias '{model_id}'.",
                    "type": "invalid_request_error",
                }
            },
            status=status.HTTP_404_NOT_FOUND,
        )

    deployed = capability.active_model
    # ``active_model`` is an unconstrained FK, so a mis-linked capability can name
    # another project's deployment. Answering "no active model" is the only reply
    # that neither routes it nor confirms it exists.
    if deployed is None or deployed.project_id != capability.project_id:
        return None, Response(
            {
                "error": {
                    "message": (
                        f"Capability '{capability.slug}' has no active model. "
                        "Pick one on the capability's Models tab."
                    ),
                    "type": "invalid_request_error",
                }
            },
            status=status.HTTP_404_NOT_FOUND,
        )

    if deployed.status != DeployedModel.Status.READY:
        # Name both ids: the alias alone hides which deployment is stuck.
        return None, Response(
            {
                "error": {
                    "message": (
                        f"{model_id} → {deployed.model_id} is not ready "
                        f"(status: {deployed.status})."
                    ),
                    "type": "server_error",
                }
            },
            status=status.HTTP_503_SERVICE_UNAVAILABLE,
        )

    return deployed, None


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def chat_completions(request: Request) -> Response | StreamingHttpResponse:
    data = request.data
    if not isinstance(data, dict):
        return Response(
            {
                "error": {
                    "message": "Request body must be a JSON object.",
                    "type": "invalid_request_error",
                }
            },
            status=status.HTTP_400_BAD_REQUEST,
        )

    model_id = data.get("model", "")
    messages = data.get("messages", [])
    stream = bool(data.get("stream", False))
    try:
        temperature = float(data.get("temperature", 1.0) or 0.0)
        max_tokens = reserve_output(data)["max_tokens"]
    except (TypeError, ValueError):
        return Response(
            {
                "error": {
                    "message": "Temperature must be numeric and the output token budget a positive integer. Prompt truncation is not supported.",
                    "type": "invalid_request_error",
                }
            },
            status=status.HTTP_400_BAD_REQUEST,
        )

    if not model_id:
        return Response(
            {"error": {"message": "model is required.", "type": "invalid_request_error"}},
            status=status.HTTP_400_BAD_REQUEST,
        )
    if not messages:
        return Response(
            {"error": {"message": "messages is required.", "type": "invalid_request_error"}},
            status=status.HTTP_400_BAD_REQUEST,
        )

    try:
        ensure_credits(request.user)
    except InsufficientCredits:
        return _credits_required_response()

    extra: dict = {}
    for key in ("top_p", "frequency_penalty", "presence_penalty"):
        val = data.get(key)
        if val is not None:
            try:
                extra[key] = float(val)
            except (TypeError, ValueError):
                return Response(
                    {
                        "error": {
                            "message": f"{key} must be a number.",
                            "type": "invalid_request_error",
                        }
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )
    # Must be forwarded: without the schema, vLLM serves a tool-calling finetune
    # a request indistinguishable from plain chat and it never emits a call.
    for key in ("tools", "tool_choice"):
        val = data.get(key)
        if val is not None:
            extra[key] = val

    # Also must be forwarded: without json_object mode, reasoning models can
    # answer inside `message.reasoning` and return `message.content: null`.
    response_format = data.get("response_format")
    if response_format is not None:
        extra["response_format"] = response_format

    # Reasoning controls — gpt-oss Harmony ignores chat_template_kwargs and
    # needs these as top-level fields. Client values win; family defaults fill
    # gaps for deployed models (see _apply_family_infer_defaults).
    for key in ("include_reasoning", "reasoning_effort", "chat_template_kwargs"):
        if key in data:
            extra[key] = data[key]

    deployed, alias_error = _resolve_alias(request, model_id)
    if alias_error is not None:
        return alias_error
    if deployed is not None:
        # vLLM knows only the concrete served name, never the caller's alias.
        model_id = deployed.model_id

    is_optimiser_run = (
        isinstance(request.auth, APIToken)
        and request.headers.get(OPTIMISER_ROUTING_HEADER, "") == "1"
    )
    if deployed is None and is_optimiser_run:
        if "/" not in model_id:
            # A patched capability may send the bare slug it had hardcoded; the routing
            # branch below requires a ``provider/model`` form. Re-attach the provider
            # so the request routes instead of 404ing. Unresolvable names keep the
            # existing DeployedModel lookup / 404 path.
            model_id = resolve_bare_openrouter_slug(model_id) or model_id
        # The ``openrouter/`` namespace is a platform-side alias, not an id
        # OpenRouter serves — strip it so the slug routes upstream.
        model_id = model_id.removeprefix("openrouter/")
    if deployed is None and (
        is_inference_model(model_id) or (is_optimiser_run and "/" in model_id)
    ):
        if not getattr(settings, "OPENROUTER_API_KEY", None):
            return Response(
                {
                    "error": {
                        "message": "Frontier model inference is not configured.",
                        "type": "server_error",
                    }
                },
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        payload: dict = {
            "model": model_id,
            "messages": messages,
            "stream": stream,
            "temperature": temperature,
            **extra,
        }
        if max_tokens:
            payload["max_tokens"] = max_tokens

        if stream:
            # Always request usage so billing has it; hide it from the client
            # unless they asked.
            include_usage = bool((data.get("stream_options") or {}).get("include_usage"))
            payload["stream_options"] = {"include_usage": True}

            def event_stream():
                captured: dict[str, Any] = {"usage": None}
                try:
                    for chunk in _frontier_stream(payload):
                        forwarded = _process_stream_chunk(chunk, captured, include_usage)
                        if forwarded is not None:
                            yield forwarded
                finally:
                    _charge_frontier_usage(request.user, model_id, captured.get("usage"))

            return StreamingHttpResponse(
                AsyncStream(event_stream()),
                content_type="text/event-stream",
                headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
            )

        result, err, upstream_status = _frontier_non_stream(model_id, payload)
        if err:
            logger.error("OpenRouter error for model %s: %s", model_id, err)
            # 429 passes through so SDK retry logic works; every other upstream
            # failure is this gateway's problem → 502.
            if upstream_status == 429:
                return Response(
                    {
                        "error": {
                            "message": "The model provider is rate-limiting requests. Try again shortly.",
                            "type": "rate_limit_error",
                        }
                    },
                    status=status.HTTP_429_TOO_MANY_REQUESTS,
                )
            return Response(
                {"error": {"message": err, "type": "server_error"}},
                status=status.HTTP_502_BAD_GATEWAY,
            )
        if isinstance(result, dict):
            _charge_frontier_usage(request.user, model_id, result.get("usage"))
        return Response(result, status=status.HTTP_200_OK)

    if deployed is None:
        try:
            deployed = DeployedModel.objects.get(
                model_id=model_id,
                project_id__in=project_ids_for(request.user, request.auth),
            )
        except DeployedModel.DoesNotExist:
            return Response(
                {
                    "error": {
                        "message": f"Model '{model_id}' not found.",
                        "type": "invalid_request_error",
                    }
                },
                status=status.HTTP_404_NOT_FOUND,
            )

    if deployed.status != DeployedModel.Status.READY:
        return Response(
            {
                "error": {
                    "message": f"Model '{model_id}' is not ready (status: {deployed.status}).",
                    "type": "server_error",
                }
            },
            status=status.HTTP_503_SERVICE_UNAVAILABLE,
        )

    max_tokens = _apply_family_infer_defaults(
        extra,
        model_id=model_id,
        base_model_id=deployed.base_model_id or "",
        max_tokens=max_tokens,
    )

    client = _get_client()

    billing_user = request.user

    if stream:
        include_usage = bool((data.get("stream_options") or {}).get("include_usage"))
        cold = is_cold_start(deployed)
        t_start = time.monotonic()

        def event_stream():
            captured: dict[str, Any] = {"usage": None, "metrics": None}
            try:
                chunks = iter_keeping_idle_alive(
                    client.stream_chat_completions(
                        model_id=model_id,
                        messages=messages,
                        temperature=temperature,
                        max_tokens=max_tokens,
                        deployed=deployed,
                        # vLLM timing rides the final usage chunk, so usage must be
                        # forced on too. Both are stripped before the client sees them.
                        include_metrics=True,
                        stream_options={"include_usage": True},
                        **extra,
                    ),
                    ping=SSE_IDLE_PING,
                )
                for chunk in chunks:
                    forwarded = _process_stream_chunk(chunk, captured, include_usage)
                    if forwarded is not None:
                        yield forwarded
            except Exception as exc:
                # Broad on purpose: a chunk-parse bug would otherwise kill the
                # generator mid-stream with no error frame.
                logger.exception("Inference streaming error for model %s: %s", model_id, exc)
                message = (
                    str(exc) if isinstance(exc, ContextBudgetError) else "Inference backend error."
                )
                yield (
                    "data: "
                    + json.dumps(
                        {
                            "error": {
                                "message": message,
                                "type": "context_length_exceeded"
                                if isinstance(exc, ContextBudgetError)
                                else "server_error",
                            }
                        }
                    )
                    + "\n\n"
                )
                yield "data: [DONE]\n\n"
            finally:
                if captured["usage"] is not None:
                    latency_ms = round((time.monotonic() - t_start) * 1000, 1)
                    record_inference_call(
                        deployed,
                        captured["usage"],
                        latency_ms,
                        cold,
                        user=billing_user,
                        metrics=captured.get("metrics"),
                    )

        return StreamingHttpResponse(
            AsyncStream(event_stream()),
            content_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    cold = is_cold_start(deployed)
    t_start = time.monotonic()

    def json_stream():
        held: dict[str, Any] = {}

        def _body():
            try:
                result = client.chat_completions(
                    model_id=model_id,
                    messages=messages,
                    stream=False,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    deployed=deployed,
                    include_metrics=True,  # vLLM per-request timing (stripped before return)
                    **extra,
                )
            except Exception as exc:
                logger.exception("Inference error for model %s: %s", model_id, exc)
                yield json.dumps(
                    {
                        "error": {
                            "message": str(exc)
                            if isinstance(exc, ContextBudgetError)
                            else "Inference backend error.",
                            "type": "context_length_exceeded"
                            if isinstance(exc, ContextBudgetError)
                            else "server_error",
                        }
                    }
                )
                return
            held["result"] = result
            held["usage"] = result.get("usage") if isinstance(result, dict) else None
            held["metrics"] = result.pop("metrics", None) if isinstance(result, dict) else None
            yield json.dumps(result)

        try:
            yield from iter_keeping_idle_alive(_body(), ping=JSON_IDLE_PING)
        finally:
            if "result" in held:
                latency_ms = round((time.monotonic() - t_start) * 1000, 1)
                record_inference_call(
                    deployed,
                    held["usage"],
                    latency_ms,
                    cold,
                    user=billing_user,
                    metrics=held.get("metrics"),
                )

    return StreamingHttpResponse(
        AsyncStream(json_stream()),
        content_type="application/json",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


_VALID_STATUSES = frozenset(s.value for s in DeployedModel.Status)


def _deployed_model_to_dict(m: DeployedModel) -> dict:
    return {
        "id": m.model_id,
        "object": "model",
        "created": int(m.deployed_at.timestamp()) if m.deployed_at else int(time.time()),
        "owned_by": "overmind",
        "finetuned": True,
        "status": m.status,
        "base_model": m.base_model_id,
    }


def _capability_alias_to_dict(capability: Capability) -> dict:
    """``name`` is deliberately non-resolvable — only ``id`` ever routes — but
    without it every row in the list is an indistinguishable UUID."""
    return {
        **_deployed_model_to_dict(capability.active_model),
        "id": f"{CAPABILITY_ALIAS_PREFIX}{capability.id}",
        "name": capability.name or capability.slug,
    }


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def models_list(request: Request) -> Response:
    status_param = request.query_params.get("status", "ready")
    scope = project_ids_for(request.user, request.auth)

    qs = DeployedModel.objects.filter(project_id__in=scope).order_by("-deployed_at")

    if status_param == "all":
        pass
    elif status_param in _VALID_STATUSES:
        qs = qs.filter(status=status_param)
    else:
        return Response(
            {
                "error": {
                    "message": (
                        f"Invalid status '{status_param}'. "
                        f"Valid values: {sorted(_VALID_STATUSES) + ['all']}"
                    ),
                    "type": "invalid_request_error",
                }
            },
            status=status.HTTP_400_BAD_REQUEST,
        )

    finetuned = [_deployed_model_to_dict(m) for m in qs]

    # An alias only ever resolves to a READY deployment.
    if status_param in ("ready", "all"):
        capabilities = (
            Capability.objects.select_related("active_model")
            .current()
            .filter(
                project_id__in=scope,
                active_model__status=DeployedModel.Status.READY,
                # A mis-linked FK to another project's deployment never routes, so
                # its metadata must not ride out on an alias row either.
                active_model__project_id=F("project_id"),
            )
        )
        finetuned += [_capability_alias_to_dict(a) for a in capabilities]

    non_finetuned = (
        [
            {
                "id": m.slug,
                "object": "model",
                "created": 0,
                "owned_by": m.vendor,
                "finetuned": False,
            }
            for m in inference_models()
        ]
        if getattr(settings, "OPENROUTER_API_KEY", None)
        else []
    )

    return Response({"object": "list", "data": finetuned + non_finetuned})


@api_view(["GET", "DELETE"])
@permission_classes([IsAuthenticated])
def model_detail(request: Request, model_id: str) -> Response:
    if is_inference_model(model_id):
        if request.method == "DELETE":
            return Response(
                {
                    "error": {
                        "message": (
                            f"'{model_id}' is not a fine-tuned model and cannot be deleted. "
                            "Only models deployed through Overmind fine-tuning can be deleted."
                        ),
                        "type": "invalid_request_error",
                    }
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        return Response(
            {
                "error": {
                    "message": f"'{model_id}' is not a fine-tuned model. Use GET /api/v1/models to list all available models.",
                    "type": "invalid_request_error",
                }
            },
            status=status.HTTP_404_NOT_FOUND,
        )

    if model_id.startswith(CAPABILITY_ALIAS_PREFIX):
        if request.method == "DELETE":
            return Response(
                {
                    "error": {
                        "message": (
                            f"'{model_id}' is a capability alias and cannot be deleted. "
                            "Delete the concrete deployment by its own model id."
                        ),
                        "type": "invalid_request_error",
                    }
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        aliased, alias_error = _resolve_alias(request, model_id)
        if alias_error is not None:
            return alias_error
        return Response(_deployed_model_to_dict(aliased))

    try:
        deployed = DeployedModel.objects.get(
            model_id=model_id,
            project_id__in=project_ids_for(request.user, request.auth),
        )
    except DeployedModel.DoesNotExist:
        return Response(
            {
                "error": {
                    "message": f"Model '{model_id}' not found.",
                    "type": "invalid_request_error",
                }
            },
            status=status.HTTP_404_NOT_FOUND,
        )

    if request.method == "GET":
        return Response(_deployed_model_to_dict(deployed))

    if deployed.status in (DeployedModel.Status.DELETING, DeployedModel.Status.DELETED):
        return Response(
            {
                "error": {
                    "message": f"Model '{model_id}' is already being deleted (status: {deployed.status}).",
                    "type": "invalid_request_error",
                }
            },
            status=status.HTTP_409_CONFLICT,
        )

    DeployedModel.objects.filter(pk=deployed.pk).update(status=DeployedModel.Status.DELETING)
    try:
        get_inference_client().delete_model(model_id)
    except (InferenceClientError, _requests.exceptions.RequestException) as exc:
        logger.error("Failed to delete model %s from inference backend: %s", model_id, exc)
        DeployedModel.objects.filter(pk=deployed.pk).update(status=DeployedModel.Status.FAILED)
        return Response(
            {
                "error": {
                    "message": f"Failed to remove model from inference backend: {exc}",
                    "type": "server_error",
                }
            },
            status=status.HTTP_502_BAD_GATEWAY,
        )

    DeployedModel.objects.filter(pk=deployed.pk).update(status=DeployedModel.Status.DELETED)
    try:
        # DELETED is a soft status (the row stays), so the graph needs an explicit
        # reprojection to stop reading the deployment as live.

        deployed.refresh_from_db()
    except Exception:  # noqa: BLE001 — graph projection must never break decommission
        logger.warning("context graph deployed-model decommission projection failed", exc_info=True)
    return Response({"id": model_id, "object": "model", "deleted": True})
