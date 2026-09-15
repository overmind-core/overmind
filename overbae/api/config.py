from rest_framework.pagination import PageNumberPagination as DRFPagination


class PageNumberPagination(DRFPagination):
    page_size = 25
    page_size_query_param = "page_size"
    max_page_size = 100


class EvalPagination(DRFPagination):
    """Cap raised so the frontend can pull a run's samples × variants × metrics
    score rows in one request."""

    page_size = 100
    page_size_query_param = "page_size"
    max_page_size = 10_000
