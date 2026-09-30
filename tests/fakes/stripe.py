from __future__ import annotations

import hashlib
import hmac
import itertools
import json
import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qsl, urlparse

_ids = itertools.count(1)


def _json(body: Any, status: int = 200) -> tuple[int, dict, bytes]:
    return status, {"content-type": "application/json"}, json.dumps(body).encode()


def _nest(pairs: list[tuple[str, str]]) -> dict[str, Any]:
    tree: dict[str, Any] = {}
    for key, value in pairs:
        parts = key.replace("]", "").split("[")
        node = tree
        for part, following in zip(parts, parts[1:] + [None], strict=True):
            if following is None:
                node[part] = value
            else:
                node = node.setdefault(part, {})
    return _lists(tree)


def _lists(node: Any) -> Any:
    if not isinstance(node, dict):
        return node
    if node and all(key.isdigit() for key in node):
        return [_lists(node[key]) for key in sorted(node, key=int)]
    return {key: _lists(value) for key, value in node.items()}


@dataclass
class StripeAPI:
    host: str = "https://api.stripe.com"
    webhook_secret: str = "whsec_fake"
    sessions: list[dict[str, Any]] = field(default_factory=list)
    customers: dict[str, dict[str, Any]] = field(default_factory=dict)
    subscriptions: dict[str, dict[str, Any]] = field(default_factory=dict)
    down: bool = False

    def subscription(self, **fields: Any) -> dict[str, Any]:
        sub = {
            "id": f"sub_fake{next(_ids)}",
            "object": "subscription",
            "status": "active",
            "cancel_at_period_end": False,
            **fields,
        }
        self.subscriptions[sub["id"]] = sub
        return sub

    def signed(self, event: dict[str, Any]) -> tuple[str, str]:
        payload = json.dumps({"object": "event", **event})
        stamp = int(time.time())
        signature = hmac.new(
            self.webhook_secret.encode(), f"{stamp}.{payload}".encode(), hashlib.sha256
        ).hexdigest()
        return payload, f"t={stamp},v1={signature}"

    def handle(self, method: str, url: str, body, headers=None) -> tuple[int, dict, bytes] | None:
        if not url.startswith(self.host):
            return None
        if self.down:
            return _json({"error": {"message": "Stripe is unavailable."}}, status=503)
        path = urlparse(url).path
        raw = body.decode() if isinstance(body, bytes) else body or ""
        form = _nest(parse_qsl(raw, keep_blank_values=True))
        if method == "POST" and path == "/v1/customers":
            customer = {"id": f"cus_fake{next(_ids)}", "object": "customer", **form}
            self.customers[customer["id"]] = customer
            return _json(customer)
        if method == "GET" and path.startswith("/v1/customers/"):
            customer = self.customers.get(path.rsplit("/", 1)[-1])
            return _json(customer) if customer else _json({"error": {}}, 404)
        if method == "POST" and path == "/v1/checkout/sessions":
            session_id = f"cs_fake{next(_ids)}"
            session = {
                "id": session_id,
                "object": "checkout.session",
                "url": f"https://checkout.stripe.test/{session_id}",
                **form,
            }
            self.sessions.append(session)
            return _json(session)
        if path.startswith("/v1/subscriptions/"):
            sub = self.subscriptions.get(path.rsplit("/", 1)[-1])
            if sub is None:
                return _json({"error": {"message": "No such subscription"}}, 404)
            if method == "POST":
                sub.update(
                    {k: v == "true" if v in ("true", "false") else v for k, v in form.items()}
                )
            return _json(sub)
        return _json({"error": {"message": f"not found: {path}"}}, 404)
