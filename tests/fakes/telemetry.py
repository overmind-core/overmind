"""One local HTTP endpoint standing in for both the Sentry and PostHog ingest APIs."""

from __future__ import annotations

import gzip
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class TelemetrySink:
    def __init__(self):
        self.posthog_events: list[dict] = []
        self.sentry_envelopes: list[str] = []
        sink = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):  # noqa: N802
                body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
                if self.headers.get("Content-Encoding") == "gzip":
                    body = gzip.decompress(body)
                if self.path.endswith("/envelope/"):
                    sink.sentry_envelopes.append(body.decode())
                else:
                    sink.posthog_events.extend(json.loads(body)["batch"])
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"{}")

            def log_message(self, *_):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.sentry_dsn = f"http://public@127.0.0.1:{self.server.server_address[1]}/1"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def events(self, name: str) -> list[dict]:
        return [event for event in self.posthog_events if event["event"] == name]

    def close(self) -> None:
        self.server.shutdown()
