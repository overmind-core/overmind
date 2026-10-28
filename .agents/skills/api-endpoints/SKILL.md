---
name: api-endpoints
description: End-to-end workflow for adding or changing a backend API endpoint — which module the serializer and view belong in, URL registration, OpenAPI client regeneration, and typed consumption from the frontend. Use when adding, changing, or removing a DRF endpoint or serializer.
---

# Adding or changing an API endpoint

The frontend consumes the backend exclusively through the generated typescript-fetch client at `frontend/src/openapi/`. Every API change runs the full loop below.

## 1. Serializer and view go in the surface's own module

`overbae/api/` is one module per surface — `eval_views.py`/`eval_serializers.py`, `datasets.py`/`dataset_serializers.py`, `optimizer.py`, `billing.py`, `otlp.py`, and so on. `views.py` and `serializers.py` are the legacy monoliths; a new surface gets its own module rather than growing them.

Never return a raw dict from a view — request and response shapes are serializers. Keep business logic in `services/` or on the model; annotate with `@extend_schema` where drf-spectacular needs help.

Expected input failures may use `core.errors.InputValidationError` with an authored public `detail`. Never build that detail from a caught exception, provider response or stored diagnostic. Return the authored detail for known failures; log unexpected exceptions server-side and return a fixed message. Apply the same boundary to MCP adapters.

## 2. Register the route

`overbae/urls.py`, on the `DefaultRouter` for viewsets:

```python
router.register(r"widgets", WidgetViewSet, basename="widget")
```

or in `urlpatterns` for plain views.

## 3. Regenerate the client

```bash
make generate_api_client
```

Settings refuse to load when any key in `_REQUIRED_KEYS` (`overbae/settings.py`: sandbox, OpenRouter, Modal, inference, AWS, Hugging Face) is unset. If `.env` lacks some, prefix the command with dummy values for those keys — the schema step calls none of these services.

Runs `manage.py spectacular` → dockerized `openapi-generator-cli` → rewrites `frontend/src/openapi/{apis,models}`.

The Compose frontend uses polling because Docker bind mounts can miss files recreated by generation. A method present on disk but missing at runtime can be a stale Vite transform: check the JavaScript served at `/src/openapi/apis/<Api>.ts`. Recreate the frontend with `docker compose up -d --no-deps frontend` after changing its watcher environment; restarting alone does not apply environment changes.

## 4. Register the API class in `client.ts`

```typescript
import { WidgetsApi } from "./openapi";

export class API {
  widgets: WidgetsApi;
  constructor(private cfg: Configuration) {
    this.widgets = new WidgetsApi(this.cfg);
  }
}
```

## 5. Consume through the client, never raw fetch

```typescript
import api from "@/client";
import type { Widget } from "@/openapi";

const widget: Widget = await api.widgets.widgetsRetrieve({ id });
```

Hardcoded URLs and bare `fetch`/`axios` bypass auth middleware and typing. Response types come from `frontend/src/openapi/models/` — never redeclare a type that already exists there.

## Rules

- Never hand-edit anything under `frontend/src/openapi/` — the `protect_generated` hook blocks it in Claude Code, Codex and Cursor, and the next regeneration would overwrite it anyway.
- A DRF view docstring becomes the OpenAPI operation `description`, so editing one changes the generated client. Prefer an explicit `@extend_schema(summary=...)`.
