from django.contrib.auth import get_user_model
from rest_framework.permissions import AllowAny
from rest_framework_simplejwt.exceptions import InvalidToken
from rest_framework_simplejwt.serializers import TokenRefreshSerializer
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView

User = get_user_model()


class PublicTokenObtainPairView(TokenObtainPairView):
    permission_classes = [AllowAny]


class PublicTokenRefreshSerializer(TokenRefreshSerializer):
    # simplejwt looks the user up unguarded; a swept guest's refresh token must be a 401, not a 500.
    def validate(self, attrs):
        try:
            return super().validate(attrs)
        except User.DoesNotExist as exc:
            raise InvalidToken("No account for this token.") from exc


class PublicTokenRefreshView(TokenRefreshView):
    permission_classes = [AllowAny]
    serializer_class = PublicTokenRefreshSerializer
    guest_allowed = True
