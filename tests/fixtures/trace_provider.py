"""Synthetic Langfuse HTTP fixture for the local import/review browser check."""

import argparse
import json
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

NOW = datetime.now(UTC)
ROWS = []
for family, count, tool in [
    ("Answer questions", 80, "search_knowledge_base"),
    ("Review refunds", 30, "lookup_payment"),
    ("Route tickets", 10, "classify_ticket"),
]:
    for number in range(count):
        trace = f"{tool}-{number}"
        start = (NOW - timedelta(days=number % 6 + 1)).isoformat()
        for suffix, parent, kind, name in [
            ("root", None, "AGENT", family),
            ("tool", "root", "TOOL", tool),
        ]:
            ROWS.append(
                {
                    "id": f"{trace}-{suffix}",
                    "traceId": trace,
                    "parentObservationId": f"{trace}-{parent}" if parent else None,
                    "name": name,
                    "type": kind,
                    "startTime": start,
                    "endTime": start,
                    "input": {"question": f"Synthetic customer request {number}"},
                    "output": {"answer": "Synthetic result"},
                }
            )


class Provider(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        if parsed.path == "/api/public/projects":
            body = {"data": [{"id": "synthetic-agent", "name": "Synthetic support agent"}]}
        elif parsed.path == "/api/public/v2/observations":
            rows = ROWS
            if query.get("traceId"):
                rows = [r for r in rows if r["traceId"] == query["traceId"][0]]
            else:
                for param, later in [("fromStartTime", True), ("toStartTime", False)]:
                    if query.get(param):
                        bound = datetime.fromisoformat(query[param][0].replace("Z", "+00:00"))
                        rows = [
                            r
                            for r in rows
                            if (datetime.fromisoformat(r["startTime"]) >= bound) == later
                        ]
            offset = int(query.get("cursor", [0])[0])
            limit = int(query.get("limit", [100])[0])
            body = {
                "data": rows[offset : offset + limit],
                "meta": {"cursor": str(offset + limit) if offset + limit < len(rows) else None},
            }
        else:
            self.send_error(404)
            return
        payload = json.dumps(body).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8734)
    parser.add_argument("--bind", default="127.0.0.1")
    args = parser.parse_args()
    ThreadingHTTPServer((args.bind, args.port), Provider).serve_forever()
