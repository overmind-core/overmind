"""OpenAI-compatible stand-in for OpenRouter, so local load runs measure Overmind and not a provider.

Serves chat completions (plain and streamed), the model catalog and Jev decisions through
``tests.fakes.llm.FakeLLM``. Every agent turn makes exactly two rounds: one ``status`` tool
call, then a closing reply. ``FAKE_LLM_LATENCY`` seconds of delay before each answer stands
in for provider time.
"""

from __future__ import annotations

import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from tests.fakes.llm import FakeLLM, LLMRequest, tool_call

LATENCY = float(os.environ.get("FAKE_LLM_LATENCY", "2.0"))
PORT = int(os.environ.get("FAKE_LLM_PORT", "9000"))


def agent_round(request: LLMRequest) -> dict:
    messages = request.messages
    last_user = max((i for i, m in enumerate(messages) if m.get("role") == "user"), default=-1)
    rounds_done = sum(1 for m in messages[last_user + 1 :] if m.get("role") == "assistant")
    if rounds_done == 0:
        return {"content": None, "tool_calls": [tool_call("status", {})], "usage": {"cost": 0.0}}
    return {"content": "Checked the dataset. Nothing else to change.", "usage": {"cost": 0.0}}


llm = FakeLLM()
llm.on(lambda request: bool(request.body.get("stream")), agent_round)
counts = {"requests": 0, "in_flight": 0, "peak_in_flight": 0}
counts_lock = threading.Lock()


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _reply(self, method: str) -> None:
        if method == "GET" and self.path.rstrip("/") == "/stats":
            body = json.dumps(counts).encode()
            self._send(200, {"content-type": "application/json"}, body)
            return
        length = int(self.headers.get("content-length") or 0)
        raw = self.rfile.read(length)
        with counts_lock:
            counts["requests"] += 1
            counts["in_flight"] += 1
            counts["peak_in_flight"] = max(counts["peak_in_flight"], counts["in_flight"])
        try:
            if method == "POST":
                time.sleep(LATENCY)
            status, headers, body = llm.handle(method, f"http://fake-llm{self.path}", raw)
        finally:
            with counts_lock:
                counts["in_flight"] -= 1
        # FakeLLM keeps every request for test assertions; a long run would hold them all.
        llm.requests.clear()
        llm.unscripted.clear()
        self._send(status, headers, body)

    def _send(self, status: int, headers: dict, body: bytes) -> None:
        self.send_response(status)
        for key, value in headers.items():
            self.send_header(key, value)
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:
        self._reply("POST")

    def do_GET(self) -> None:
        self._reply("GET")

    def log_message(self, *args) -> None:
        pass


if __name__ == "__main__":
    server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    print(f"fake LLM on :{PORT}, latency {LATENCY}s", flush=True)
    server.serve_forever()
