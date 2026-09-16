"""Datasets: landing, the chain of cells, rows with diff marks, export, the
agent chat and the event stream. Consumers use a cell; the active one by default."""

from __future__ import annotations

import json
import logging

from django.conf import settings
from django.db.models import Prefetch
from django.http import StreamingHttpResponse
from django.shortcuts import get_object_or_404
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema, extend_schema_view
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.renderers import JSONRenderer
from rest_framework.response import Response

from overbae.api.dataset_serializers import (
    CellCreateSerializer,
    CellSerializer,
    CellWriteSerializer,
    ChatSerializer,
    ColumnStatSerializer,
    DatasetCreateSerializer,
    DatasetPairSerializer,
    DatasetSerializer,
    DatasetSplitCreateSerializer,
    DetailSerializer,
    RowsPageSerializer,
)
from overbae.api.scoping import project_ids_for
from overbae.models import Capability, Cell, Dataset, Project
from overbae.services.datasets import diff as diff_svc
from overbae.services.datasets import dispatch, files, lifecycle, paths, selection, store
from overbae.services.datasets import export as export_svc
from overbae.services.datasets.notebook import events

logger = logging.getLogger(__name__)

_ROW_PAGE_MAX = 500


class ServerSentEventRenderer(JSONRenderer):
    media_type = "text/event-stream"
    format = "sse"


def _error(exc: lifecycle.DatasetError, http_status: int = 409) -> Response:
    return Response({"detail": exc.detail, "code": exc.code}, status=http_status)


def _agent_owns(dataset: Dataset) -> Response | None:
    if dataset.state != Dataset.State.DIAGNOSING:
        return None
    return _error(lifecycle.DatasetError("The agent is working. Wait for it.", code=dataset.state))


_CELL_PARAM = OpenApiParameter(
    "cell", OpenApiTypes.UUID, OpenApiParameter.QUERY, description="a cell id; default = active"
)


@extend_schema_view(
    list=extend_schema(
        summary="List datasets",
        parameters=[
            OpenApiParameter("project", OpenApiTypes.UUID, OpenApiParameter.QUERY),
            OpenApiParameter("capability", OpenApiTypes.UUID, OpenApiParameter.QUERY),
            OpenApiParameter("intent", OpenApiTypes.STR, OpenApiParameter.QUERY),
            OpenApiParameter("search", OpenApiTypes.STR, OpenApiParameter.QUERY),
        ],
    ),
    retrieve=extend_schema(summary="Get a dataset with its cells and chat"),
    partial_update=extend_schema(
        summary="Rename a dataset, set its capability or intent (until a version is used), or its active cell"
    ),
    destroy=extend_schema(summary="Delete a dataset (refused while a version is used)"),
)
class DatasetViewSet(viewsets.ModelViewSet):
    serializer_class = DatasetSerializer
    lookup_field = "id"
    lookup_value_regex = "[0-9a-f-]{36}"
    http_method_names = ["get", "post", "patch", "delete", "head", "options"]

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return Dataset.objects.none()
        qs = (
            Dataset.objects.filter(
                project_id__in=project_ids_for(self.request.user, self.request.auth)
            )
            .select_related("capability")
            .prefetch_related(Prefetch("cells", queryset=Cell.objects.order_by("position")))
        )
        if self.action != "list":
            return qs
        params = self.request.query_params
        if params.get("project"):
            qs = qs.filter(project_id=params["project"])
        if params.get("capability"):
            qs = qs.filter(capability_id=params["capability"])
        if params.get("intent") in ("train", "eval", "pending"):
            qs = qs.filter(intent=params["intent"])
        if params.get("search"):
            qs = qs.filter(name__icontains=params["search"])
        return qs.order_by("-updated_at")

    def get_serializer_context(self):
        return {**super().get_serializer_context(), "summary": self.action == "list"}

    @extend_schema(
        summary="Create a dataset from a file, pasted rows or traces",
        request=DatasetCreateSerializer,
        responses={201: DatasetSerializer},
    )
    def create(self, request, *args, **kwargs):
        body = DatasetCreateSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        data = body.validated_data
        project, capability, source = self._create_target(request, data)
        dataset = dispatch.create_dataset(
            project=project,
            user=request.user if request.user.is_authenticated else None,
            name=data["name"].strip(),
            source=source,
            intent=data.get("intent"),
            capability=capability,
        )
        return Response(DatasetSerializer(dataset).data, status=status.HTTP_201_CREATED)

    @extend_schema(
        summary="Create a train dataset and an eval dataset from one source",
        request=DatasetSplitCreateSerializer,
        responses={201: DatasetPairSerializer},
    )
    @action(detail=False, methods=["post"], url_path="split")
    def split(self, request):
        body = DatasetSplitCreateSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        data = body.validated_data
        project, capability, source = self._create_target(request, data)
        try:
            train, evaluation = dispatch.create_split(
                project=project,
                user=request.user if request.user.is_authenticated else None,
                name=data["name"].strip(),
                source=source,
                eval_percent=data["eval_percent"],
                position=data["position"],
                capability=capability,
            )
        except lifecycle.DatasetError as exc:
            raise ValidationError({"detail": exc.detail, "code": exc.code}) from exc
        return Response(
            {"train": DatasetSerializer(train).data, "eval": DatasetSerializer(evaluation).data},
            status=status.HTTP_201_CREATED,
        )

    def _create_target(self, request, data: dict) -> tuple[Project, Capability | None, dict]:
        project = get_object_or_404(
            Project, pk=data["project"], id__in=project_ids_for(request.user, request.auth)
        )
        capability = (
            get_object_or_404(Capability, pk=data["capability"], project=project)
            if data.get("capability")
            else None
        )
        return project, capability, self._source_payload(data["source"], project)

    @staticmethod
    def _source_payload(source: dict, project: Project) -> dict:
        payload = {k: v for k, v in source.items() if v not in (None, "", [])}
        if payload.get("traces") is not None:
            try:
                traces = selection.TraceSource.parse(payload["traces"])
                matched = traces.count(project.id)
            except selection.TraceSourceError as exc:
                raise ValidationError({"source": str(exc)}) from exc
            if matched == 0:
                raise ValidationError({"source": "No traces match the selection."})
            payload["traces"] = traces.spec()
        if payload.get("text"):
            try:
                payload["rows"] = files.parse_text(
                    payload.pop("text"), filename=payload.get("filename") or ""
                )
            except files.FileError as exc:
                raise ValidationError({"source": str(exc)}) from exc
            if not payload["rows"]:
                raise ValidationError({"source": "Nothing to read."})
        if payload.get("upload_id") and files.upload_received(payload["upload_id"]) == 0:
            raise ValidationError({"source": "The upload is empty or has expired."})
        return payload

    def perform_update(self, serializer):
        dataset = serializer.instance
        data = serializer.validated_data
        try:
            if "name" in data:
                lifecycle.rename(dataset, data["name"])
            if "intent" in data:
                lifecycle.set_intent(dataset, data["intent"])
            if "capability" in data:
                lifecycle.set_capability(dataset, data["capability"])
            if "active" in data:
                lifecycle.set_active(dataset, data["active"])
        except lifecycle.DatasetError as exc:
            raise ValidationError({"detail": exc.detail, "code": exc.code}) from exc

    def destroy(self, request, *args, **kwargs):
        dataset = self.get_object()
        try:
            lifecycle.delete_dataset(dataset)
        except lifecycle.DatasetError as exc:
            return _error(exc)
        return Response(status=status.HTTP_204_NO_CONTENT)

    def _cell(self, dataset: Dataset, cell_id) -> Cell:
        cell = dataset.cells.filter(pk=cell_id).first()
        if cell is None:
            raise NotFound("No such cell.")
        return cell

    def _cell_response(self, dataset: Dataset, cell: Cell, http_status: int = 200) -> Response:
        return Response(
            CellSerializer(
                cell,
                context={
                    "versions": dataset.versions(),
                    "frozen_before": dataset.frozen_before,
                    "intent": dataset.intent,
                },
            ).data,
            status=http_status,
        )

    @extend_schema(
        summary="Add a cell at the end of the chain (queued; run to execute)",
        request=CellCreateSerializer,
        responses={201: CellSerializer},
    )
    @action(detail=True, methods=["post"], url_path="cells")
    def add_cell(self, request, id=None):
        dataset = self.get_object()
        if (refused := _agent_owns(dataset)) is not None:
            return refused
        body = CellCreateSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        try:
            cell = lifecycle.add_cell(
                dataset,
                title=body.validated_data["title"],
                script=body.validated_data["script"],
                note=body.validated_data.get("note") or "",
                user=request.user if request.user.is_authenticated else None,
            )
        except lifecycle.DatasetError as exc:
            return _error(exc)
        events.publish(dataset.id, {"dataset_id": str(dataset.id), "type": "cells_changed"})
        return self._cell_response(dataset, cell, status.HTTP_201_CREATED)

    @extend_schema(
        summary="Edit a cell's title, script or note; a script change queues it and every cell after it",
        request=CellWriteSerializer,
        responses={200: CellSerializer},
    )
    @action(detail=True, methods=["patch"], url_path=r"cells/(?P<cell_id>[0-9a-f-]{36})")
    def edit_cell(self, request, id=None, cell_id=None):
        dataset = self.get_object()
        if (refused := _agent_owns(dataset)) is not None:
            return refused
        cell = self._cell(dataset, cell_id)
        body = CellWriteSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        try:
            cell = lifecycle.edit_cell(dataset, cell, **body.validated_data)
        except lifecycle.DatasetError as exc:
            return _error(exc)
        return self._cell_response(dataset, cell)

    @extend_schema(summary="Remove a cell (refused when frozen)", responses={204: None})
    @edit_cell.mapping.delete
    def remove_cell(self, request, id=None, cell_id=None):
        dataset = self.get_object()
        if (refused := _agent_owns(dataset)) is not None:
            return refused
        cell = self._cell(dataset, cell_id)
        try:
            lifecycle.remove_cell(dataset, cell)
        except lifecycle.DatasetError as exc:
            return _error(exc)
        events.publish(dataset.id, {"dataset_id": str(dataset.id), "type": "cells_changed"})
        return Response(status=status.HTTP_204_NO_CONTENT)

    @extend_schema(
        summary="Accept a proposal: it joins the chain and the run starts",
        request=None,
        responses={202: CellSerializer},
    )
    @action(detail=True, methods=["post"], url_path=r"cells/(?P<cell_id>[0-9a-f-]{36})/accept")
    def accept_cell(self, request, id=None, cell_id=None):
        dataset = self.get_object()
        cell = self._cell(dataset, cell_id)
        try:
            dispatch.run_dataset(
                dataset,
                request.user if request.user.is_authenticated else None,
                proposal=cell,
            )
        except lifecycle.DatasetError as exc:
            return _error(exc)
        cell.refresh_from_db()
        return self._cell_response(dataset, cell, status.HTTP_202_ACCEPTED)

    @extend_schema(summary="Run the chain", request=None, responses={202: DatasetSerializer})
    @action(detail=True, methods=["post"], url_path="run")
    def run(self, request, id=None):
        dataset = self.get_object()
        try:
            dataset = dispatch.run_dataset(
                dataset, request.user if request.user.is_authenticated else None
            )
        except lifecycle.DatasetError as exc:
            return _error(exc)
        return Response(DatasetSerializer(dataset).data, status=status.HTTP_202_ACCEPTED)

    @extend_schema(
        summary="Send a message to the dataset's agent",
        request=ChatSerializer,
        responses={202: DetailSerializer},
    )
    @action(detail=True, methods=["post"], url_path="chat")
    def chat(self, request, id=None):
        dataset = self.get_object()
        body = ChatSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        try:
            dispatch.message_agent(
                dataset,
                request.user if request.user.is_authenticated else None,
                body.validated_data["message"],
            )
        except lifecycle.DatasetError as exc:
            return _error(exc)
        return Response({"detail": "Sent."}, status=status.HTTP_202_ACCEPTED)

    def _frame(self, dataset: Dataset, cell: Cell):
        path = paths.cell_path(dataset.id, cell.id)
        if not cell.fingerprint or not path.exists():
            raise NotFound("That cell has no frame. Run the chain.")
        return path

    def _table(self, dataset: Dataset, request) -> tuple:
        """``?cell=`` picks a frame; default = the active cell."""
        cell_id = request.query_params.get("cell")
        cell = self._cell(dataset, cell_id) if cell_id else dataset.active_cell
        if cell is None:
            raise NotFound("Nothing has run yet.")
        return self._frame(dataset, cell), cell

    @extend_schema(
        summary="A page of rows from a cell's frame, with diff marks against the cell before",
        parameters=[
            _CELL_PARAM,
            OpenApiParameter("offset", OpenApiTypes.INT, OpenApiParameter.QUERY),
            OpenApiParameter("limit", OpenApiTypes.INT, OpenApiParameter.QUERY),
            OpenApiParameter("sort", OpenApiTypes.STR, OpenApiParameter.QUERY),
            OpenApiParameter("dir", OpenApiTypes.STR, OpenApiParameter.QUERY),
            OpenApiParameter(
                "filters",
                OpenApiTypes.STR,
                OpenApiParameter.QUERY,
                description="JSON list of {field, op, value}",
            ),
            OpenApiParameter("search", OpenApiTypes.STR, OpenApiParameter.QUERY),
            OpenApiParameter("diff", OpenApiTypes.BOOL, OpenApiParameter.QUERY),
        ],
        responses={200: RowsPageSerializer},
    )
    @action(detail=True, methods=["get"], url_path="rows")
    def rows(self, request, id=None):
        dataset = self.get_object()
        path, cell = self._table(dataset, request)
        params = request.query_params
        try:
            offset = max(0, int(params.get("offset", 0)))
            limit = min(_ROW_PAGE_MAX, max(1, int(params.get("limit", 50))))
        except ValueError:
            raise ValidationError({"detail": "offset and limit must be integers."}) from None
        filters = []
        if params.get("filters"):
            try:
                filters = json.loads(params["filters"])
            except ValueError:
                raise ValidationError({"filters": "must be a JSON list."}) from None
            if not isinstance(filters, list):
                raise ValidationError({"filters": "must be a JSON list."})
        try:
            page = store.page(
                path,
                offset=offset,
                limit=limit,
                sort=params.get("sort") or None,
                direction="desc" if params.get("dir") == "desc" else "asc",
                filters=filters,
                search=params.get("search") or "",
            )
        except store.StoreError as exc:
            raise ValidationError({"detail": str(exc)}) from exc
        marks: dict = {}
        if params.get("diff") not in (None, "", "0", "false") and cell.position > 0:
            before = (
                dataset.cells.filter(position__lt=cell.position, state=Cell.State.OK)
                .order_by("-position")
                .first()
            )
            if before is not None:
                source_rows = [
                    int(r["source_row"]) for r in page["rows"] if r.get("source_row") is not None
                ]
                marks = diff_svc.marks(self._frame(dataset, before), path, source_rows)
        return Response({**page, "offset": offset, "limit": limit, "marks": marks})

    @extend_schema(summary="One row by index", parameters=[_CELL_PARAM])
    @action(detail=True, methods=["get"], url_path=r"rows/(?P<index>\d+)")
    def row(self, request, id=None, index=None):
        dataset = self.get_object()
        path, _cell = self._table(dataset, request)
        row = store.read_row(path, int(index))
        if row is None:
            raise NotFound("No row at that index.")
        return Response({"index": int(index), "row": row})

    @extend_schema(
        summary="Per-column stats of a cell's frame",
        parameters=[_CELL_PARAM],
        responses={200: ColumnStatSerializer(many=True)},
    )
    @action(detail=True, methods=["get"], url_path="columns", pagination_class=None)
    def columns(self, request, id=None):
        dataset = self.get_object()
        path, _cell = self._table(dataset, request)
        return Response(store.column_stats(path))

    @extend_schema(
        summary="Download a cell's frame as JSONL, CSV or training lines; counts as a use",
        parameters=[
            _CELL_PARAM,
            OpenApiParameter(
                "fmt",
                OpenApiTypes.STR,
                OpenApiParameter.QUERY,
                description="jsonl (default) | csv | training",
            ),
        ],
        responses={
            (200, "application/x-ndjson"): OpenApiTypes.BINARY,
            (200, "text/csv"): OpenApiTypes.BINARY,
        },
    )
    @action(detail=True, methods=["get"], url_path="export")
    def export(self, request, id=None):
        dataset = self.get_object()
        path, cell = self._table(dataset, request)
        fmt = "csv" if request.query_params.get("fmt") == "csv" else "jsonl"
        version = dataset.versions().get(cell.id, "")
        label = f"{dataset.name or 'dataset'}-{version or 'source'}"
        chunks, content_type, ext = export_svc.stream(path, fmt)
        response = StreamingHttpResponse(chunks, content_type=content_type)
        safe = "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in label)
        response["Content-Disposition"] = f'attachment; filename="{safe}.{ext}"'
        response["X-Overmind-Cell"] = str(cell.id)
        response["X-Overmind-Version"] = version
        response["X-Overmind-Fingerprint"] = cell.fingerprint
        return response

    def get_renderers(self):
        if getattr(self, "action", None) == "events":
            return [JSONRenderer(), ServerSentEventRenderer()]
        return super().get_renderers()

    @extend_schema(summary="Dataset event stream (SSE): landing, cells, runs and chat")
    @action(detail=True, methods=["get"], url_path="events")
    def events(self, request, id=None):
        from asgiref.sync import sync_to_async

        dataset = self.get_object()
        ch = events.channel(dataset.id)

        # Must stay a native async generator: under ASGI a sync generator sends an empty body.
        async def event_stream():
            import redis.asyncio as aredis

            r = aredis.from_url(settings.CELERY_BROKER_URL, decode_responses=True)
            pubsub = r.pubsub()
            try:
                await pubsub.subscribe(ch)
                replayed_through = 0
                for evt in await sync_to_async(events.replay)(dataset.id):
                    replayed_through = max(replayed_through, int(evt.get("seq") or 0))
                    yield f"data: {json.dumps(evt, default=str)}\n\n"
                yield 'data: {"type": "replay.done"}\n\n'
                idle = 0.0
                while True:
                    message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=5.0)
                    if message is None:
                        idle += 5.0
                        if idle >= 15.0:
                            idle = 0.0
                            yield ": ping\n\n"
                        continue
                    idle = 0.0
                    data = message["data"]
                    try:
                        seq = int(json.loads(data).get("seq") or 0)
                    except (ValueError, AttributeError):
                        seq = 0
                    if seq and seq <= replayed_through:
                        continue
                    yield f"data: {data}\n\n"
            finally:
                try:
                    await pubsub.unsubscribe(ch)
                    await pubsub.aclose()
                    await r.aclose()
                except Exception:  # noqa: BLE001
                    logger.debug("events stream teardown failed", exc_info=True)

        response = StreamingHttpResponse(event_stream(), content_type="text/event-stream")
        response["Cache-Control"] = "no-cache"
        response["X-Accel-Buffering"] = "no"
        return response
