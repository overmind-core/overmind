from django.core.paginator import Paginator
from django.utils.functional import cached_property
from rest_framework.pagination import PageNumberPagination as DRFPagination

# Past this, a list says "at least" instead of scanning millions of rows to count them.
# Absolute totals come from the hourly rollups.
COUNT_CAP = 10_000


class CappedCountPaginator(Paginator):
    @cached_property
    def count(self) -> int:
        return self.object_list.order_by().values("pk")[: COUNT_CAP + 1].count()


class PageNumberPagination(DRFPagination):
    page_size = 25
    page_size_query_param = "page_size"
    max_page_size = 100


class TraceListPagination(PageNumberPagination):
    django_paginator_class = CappedCountPaginator


class EvalPagination(DRFPagination):
    """Cap raised so the frontend can pull a run's samples × variants × metrics
    score rows in one request."""

    page_size = 100
    page_size_query_param = "page_size"
    max_page_size = 10_000
