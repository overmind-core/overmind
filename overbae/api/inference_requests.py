from django.shortcuts import get_object_or_404
from drf_spectacular.utils import extend_schema
from rest_framework import serializers
from rest_framework.response import Response
from rest_framework.views import APIView

from overbae.api.credit_gate import require_credits
from overbae.api.scoping import project_ids_for
from overbae.core.errors import InputValidationError
from overbae.models import APIToken, DeployedModel, InferenceRequest
from overbae.services import inference_requests, systemone


class DecisionInputSerializer(serializers.Serializer):
    state = serializers.CharField(allow_blank=True, trim_whitespace=False, max_length=128_000)
    question = serializers.CharField(trim_whitespace=False, max_length=32_000)
    kind = serializers.ChoiceField(choices=["choice", "noul", "score"])
    options = serializers.ListField(
        child=serializers.CharField(trim_whitespace=False), min_length=2, max_length=255
    )

    def to_internal_value(self, data):
        if not isinstance(data, dict) or set(data) != set(self.fields):
            raise serializers.ValidationError(
                "Decision requests accept only state, question, kind and options."
            )
        return super().to_internal_value(data)


class InferenceMessageSerializer(serializers.Serializer):
    role = serializers.ChoiceField(choices=["system", "user", "assistant", "tool"])
    content = serializers.CharField(max_length=32_000, trim_whitespace=False)


class InferenceInputSerializer(serializers.Serializer):
    request_key = serializers.CharField(max_length=128)
    messages = InferenceMessageSerializer(many=True, required=False, min_length=1, max_length=50)
    decisions = DecisionInputSerializer(many=True, required=False, min_length=1, max_length=16)
    max_tokens = serializers.IntegerField(required=False, min_value=1)
    temperature = serializers.FloatField(required=False, min_value=0, max_value=2)


class InferenceReceiptSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    status = serializers.CharField()
    request_key = serializers.CharField()
    model_id = serializers.CharField()
    result = serializers.JSONField()
    error_code = serializers.CharField(allow_null=True)
    deadline = serializers.DateTimeField()
    retry_safe = serializers.BooleanField()
    is_cold = serializers.BooleanField()


class DeploymentInferenceView(APIView):
    @extend_schema(request=InferenceInputSerializer, responses={202: InferenceReceiptSerializer})
    def post(self, request, deployment_id):
        deployed = get_object_or_404(
            DeployedModel,
            pk=deployment_id,
            project_id__in=project_ids_for(request.user, request.auth),
        )
        serializer = InferenceInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        inputs = dict(serializer.validated_data)
        key = inputs.pop("request_key")
        try:
            receipt = inference_requests.recover_existing(deployed, key, inputs)
            if receipt is None:
                require_credits(request.user)
                receipt = inference_requests.submit(
                    deployed,
                    request.user,
                    key,
                    inputs,
                    source="application" if isinstance(request.auth, APIToken) else "playground",
                )
        except InputValidationError as exc:
            raise serializers.ValidationError(exc.detail) from exc
        return Response(
            InferenceReceiptSerializer(inference_requests.describe(receipt)).data, status=202
        )


def _dedicated_systemone(request, model, names, decisions):
    from django.utils import timezone

    from overbae.services.dedicated import lookup, serve_decision
    from overbae.services.deployed_chat import record_inference_call

    row = lookup(model, project_ids_for(request.user, request.auth))
    if row is None:
        return Response(
            {
                "error": {
                    "message": "No decision deployment matches this model.",
                    "type": "invalid_request",
                }
            },
            status=404,
        )
    started = timezone.now()
    try:
        result = serve_decision(row, decisions)
    except InputValidationError as exc:
        code = 503 if "not online" in exc.detail else 400
        return Response({"error": {"message": exc.detail, "type": "invalid_request"}}, status=code)
    except Exception:
        return Response(
            {"error": {"message": "Decision inference failed.", "type": "server_error"}},
            status=502,
        )
    elapsed_ms = (timezone.now() - started).total_seconds() * 1000
    record_inference_call(
        row.deployed_model,
        result.get("usage"),
        elapsed_ms,
        user=request.user,
        source="application" if isinstance(request.auth, APIToken) else "playground",
        requested_model=model,
        bill=False,
    )
    usage = result.get("usage") or {}
    return Response(
        {
            "model": model,
            "answers": systemone.answer(
                names, [item["kind"] for item in decisions], result["predictions"]
            ),
            "usage": {
                "input_tokens": usage.get("prompt_tokens") or 0,
                "output_tokens": usage.get("completion_tokens") or 0,
                "cost": None,
            },
            "id": str(row.id),
            "provider": "Overmind",
        }
    )


class SystemOneView(APIView):
    @extend_schema(request=None, responses={200: None})
    def post(self, request):
        body = request.data if isinstance(request.data, dict) else {}
        try:
            model, _state, names, decisions = systemone.parse(body)
            if str(model).startswith("dedicated/"):
                return _dedicated_systemone(request, model, names, decisions)
            deployment = systemone.resolve(model, project_ids_for(request.user, request.auth))
        except InputValidationError as exc:
            return Response(
                {"error": {"message": exc.detail, "type": "invalid_request"}},
                status=400,
            )
        if deployment is None and not systemone.catalog_model(model):
            return Response(
                {
                    "error": {
                        "message": "No decision deployment matches this model.",
                        "type": "invalid_request",
                    }
                },
                status=404,
            )
        require_credits(request.user)
        try:
            if deployment is not None:
                status_code, payload = systemone.call_deployment(
                    deployment, request.user, request.auth, names, decisions
                )
            else:
                status_code, payload = systemone.call_openrouter(request.user, body)
        except InputValidationError as exc:
            return Response(
                {"error": {"message": exc.detail, "type": "invalid_request"}},
                status=400,
            )
        return Response(payload, status=status_code)


class InferenceRequestView(APIView):
    @extend_schema(responses=InferenceReceiptSerializer)
    def get(self, request, request_id):
        receipt = get_object_or_404(
            InferenceRequest,
            pk=request_id,
            project_id__in=project_ids_for(request.user, request.auth),
        )
        return Response(InferenceReceiptSerializer(inference_requests.describe(receipt)).data)
