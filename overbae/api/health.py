from django.db import connection
from drf_spectacular.utils import extend_schema
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response


@api_view(["GET"])
@permission_classes([AllowAny])
@extend_schema(exclude=True)
def health_check(request):
    with connection.cursor() as cursor:
        cursor.execute("SELECT 1")
    return Response({"status": "healthy"})
