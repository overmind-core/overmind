from urllib.parse import urlsplit

from django.http import HttpResponse, HttpResponseRedirect
from django.utils.html import format_html
from drf_spectacular.utils import extend_schema
from rest_framework import serializers
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from overbae.api.auth_registration import AuthTokensResponseSerializer, tokens_for
from overbae.services import chatgpt


def set_authorization_cookie(response, browser):
    response.set_cookie(
        chatgpt.COOKIE,
        browser,
        max_age=600,
        httponly=True,
        samesite="Lax",
        path=chatgpt.CALLBACK_PATH,
    )


class ChatGPTAccountSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    email = serializers.CharField(allow_blank=True)
    label = serializers.CharField()
    connected = serializers.BooleanField()
    plan_enabled = serializers.BooleanField()


class WorkshopFundingSerializer(serializers.Serializer):
    enabled = serializers.BooleanField()
    funding_source = serializers.ChoiceField(choices=["platform", "chatgpt"])
    account_id = serializers.UUIDField(allow_null=True)
    model = serializers.CharField(allow_blank=True)
    usage_url = serializers.URLField()
    accounts = ChatGPTAccountSerializer(many=True)


class WorkshopFundingRequestSerializer(serializers.Serializer):
    funding_source = serializers.ChoiceField(choices=["platform", "chatgpt"])
    account_id = serializers.UUIDField(required=False, allow_null=True)
    model = serializers.CharField(required=False, max_length=255, allow_blank=True)


class ChatGPTStartRequestSerializer(serializers.Serializer):
    account_id = serializers.UUIDField(required=False, allow_null=True)


class ChatGPTStartSerializer(serializers.Serializer):
    authorization_url = serializers.URLField()


class ChatGPTLoginSerializer(serializers.Serializer):
    enabled = serializers.BooleanField()
    remembered_email = serializers.CharField(allow_blank=True)


class ChatGPTLoginRequestSerializer(serializers.Serializer):
    email = serializers.EmailField(required=False, allow_blank=True, default="")
    use_another = serializers.BooleanField(required=False, default=False)


class ChatGPTLoginHandoffSerializer(serializers.Serializer):
    email = serializers.EmailField()
    requires_password = serializers.BooleanField()


class ChatGPTLoginConfirmSerializer(serializers.Serializer):
    password = serializers.CharField(
        required=False, allow_blank=True, max_length=128, default="", write_only=True
    )


def remember_account(response, account):
    response.set_signed_cookie(
        chatgpt.REMEMBER_COOKIE,
        str(account.pk),
        max_age=365 * 86400,
        httponly=True,
        samesite="Lax",
        path="/api/chatgpt/",
    )


class ChatGPTAccountRequestSerializer(serializers.Serializer):
    account_id = serializers.UUIDField()


class ChatGPTModelSerializer(serializers.Serializer):
    id = serializers.CharField()
    name = serializers.CharField()


class ChatGPTDisconnectSerializer(serializers.Serializer):
    revocation_confirmed = serializers.BooleanField()


class ChatGPTView(APIView):
    def finalize_response(self, request, response, *args, **kwargs):
        response = super().finalize_response(request, response, *args, **kwargs)
        response["Cache-Control"] = "no-store"
        return response

    def handle_exception(self, exc):
        if isinstance(exc, chatgpt.ChatGPTError):
            exc = ValidationError({"detail": str(exc)})
        return super().handle_exception(exc)

    def validate_console(self, request):
        # The state cookie and OAuth callback must share the same loopback host.
        chatgpt.loopback_uri(request.build_absolute_uri(chatgpt.CALLBACK_PATH), callback=True)
        origin = request.headers.get("Origin", "")
        chatgpt.loopback_uri(origin)
        if urlsplit(origin).netloc != urlsplit(chatgpt.settings_url()).netloc:
            raise chatgpt.ChatGPTError("Start ChatGPT sign-in from this installation's Console.")

    def authorization_response(self, url, browser):
        response = Response(ChatGPTStartSerializer({"authorization_url": url}).data)
        set_authorization_cookie(response, browser)
        return response


class WorkshopFundingView(ChatGPTView):
    @extend_schema(tags=["ChatGPT"], responses=WorkshopFundingSerializer)
    def get(self, request):
        return Response(WorkshopFundingSerializer(chatgpt.status(request.user)).data)

    @extend_schema(
        tags=["ChatGPT"],
        request=WorkshopFundingRequestSerializer,
        responses=WorkshopFundingSerializer,
    )
    def patch(self, request):
        payload = WorkshopFundingRequestSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        return Response(
            WorkshopFundingSerializer(chatgpt.choose(request.user, **payload.validated_data)).data
        )


class ChatGPTStartView(ChatGPTView):
    @extend_schema(
        tags=["ChatGPT"], request=ChatGPTStartRequestSerializer, responses=ChatGPTStartSerializer
    )
    def post(self, request):
        self.validate_console(request)
        payload = ChatGPTStartRequestSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        url, browser = chatgpt.start(request.user, **payload.validated_data)
        return self.authorization_response(url, browser)


class ChatGPTLoginView(ChatGPTView):
    authentication_classes = []
    permission_classes = [AllowAny]

    @extend_schema(tags=["ChatGPT"], responses=ChatGPTLoginSerializer)
    def get(self, request):
        account = chatgpt.login_account(
            request.get_signed_cookie(chatgpt.REMEMBER_COOKIE, default="")
        )
        return Response(
            ChatGPTLoginSerializer(
                {
                    "enabled": chatgpt.enabled(),
                    "remembered_email": account.email if account else "",
                }
            ).data
        )

    @extend_schema(
        tags=["ChatGPT"], request=ChatGPTLoginRequestSerializer, responses=ChatGPTStartSerializer
    )
    def post(self, request):
        self.validate_console(request)
        payload = ChatGPTLoginRequestSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        account = (
            None
            if payload.validated_data["use_another"]
            else chatgpt.login_account(
                request.get_signed_cookie(chatgpt.REMEMBER_COOKIE, default=""),
                payload.validated_data["email"],
            )
        )
        url, browser = chatgpt.start(account=account)
        return self.authorization_response(url, browser)


class ChatGPTLoginSessionView(ChatGPTView):
    authentication_classes = []
    permission_classes = [AllowAny]

    @extend_schema(tags=["ChatGPT"], responses=ChatGPTLoginHandoffSerializer)
    def get(self, request):
        self.validate_console(request)
        return Response(
            ChatGPTLoginHandoffSerializer(
                chatgpt.login_handoff(request.COOKIES.get(chatgpt.COOKIE, ""))
            ).data
        )

    @extend_schema(
        tags=["ChatGPT"],
        request=ChatGPTLoginConfirmSerializer,
        responses=AuthTokensResponseSerializer,
    )
    def post(self, request):
        self.validate_console(request)
        payload = ChatGPTLoginConfirmSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        user, account = chatgpt.redeem_login(
            request.COOKIES.get(chatgpt.COOKIE, ""), **payload.validated_data
        )
        response = Response(tokens_for(user))
        if account:
            remember_account(response, account)
        response.delete_cookie(chatgpt.COOKIE, path=chatgpt.CALLBACK_PATH, samesite="Lax")
        return response


class ChatGPTCallbackView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]

    @extend_schema(exclude=True)
    def get(self, request):
        signing_in = False
        retrying = False
        try:
            account, signing_in = chatgpt.finish(
                request.query_params, request.COOKIES.get(chatgpt.COOKIE, "")
            )
            response = HttpResponseRedirect(
                chatgpt.console_url("/login") + "?chatgpt=complete"
                if signing_in
                else chatgpt.settings_url()
            )
            if account:
                remember_account(response, account)
            if signing_in:
                set_authorization_cookie(response, request.COOKIES.get(chatgpt.COOKIE, ""))
        except chatgpt.ChatGPTAuthorizationRetryError as exc:
            retrying = True
            response = HttpResponseRedirect(exc.url)
            set_authorization_cookie(response, exc.browser)
        except chatgpt.ChatGPTError as exc:
            response = HttpResponse(
                format_html(
                    '<p>{}</p><a href="{}">Return to Overmind</a>',
                    str(exc),
                    chatgpt.console_url("/login"),
                ),
                status=400,
            )
        if not signing_in and not retrying:
            response.delete_cookie(chatgpt.COOKIE, path=chatgpt.CALLBACK_PATH, samesite="Lax")
        response["Cache-Control"] = "no-store"
        response["Referrer-Policy"] = "no-referrer"
        response["Content-Security-Policy"] = "default-src 'none'; frame-ancestors 'none'"
        return response


class ChatGPTModelsView(ChatGPTView):
    @extend_schema(
        tags=["ChatGPT"],
        parameters=[ChatGPTAccountRequestSerializer],
        responses=ChatGPTModelSerializer(many=True),
    )
    def get(self, request):
        payload = ChatGPTAccountRequestSerializer(data=request.query_params)
        payload.is_valid(raise_exception=True)
        return Response(
            ChatGPTModelSerializer(
                chatgpt.models(request.user, payload.validated_data["account_id"]), many=True
            ).data
        )


class ChatGPTDisconnectView(ChatGPTView):
    @extend_schema(
        tags=["ChatGPT"],
        request=ChatGPTAccountRequestSerializer,
        responses=ChatGPTDisconnectSerializer,
    )
    def post(self, request):
        payload = ChatGPTAccountRequestSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        return Response(
            ChatGPTDisconnectSerializer(
                chatgpt.disconnect(request.user, payload.validated_data["account_id"])
            ).data
        )
