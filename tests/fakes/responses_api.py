import json
import threading
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from uuid import uuid4


def response(name="python", arguments=None, text="Inspecting the values."):
    return {
        "id": "resp_" + uuid4().hex,
        "object": "response",
        "created_at": 1,
        "status": "completed",
        "model": "test",
        "service_tier": "default",
        "output": [
            {
                "id": "rs_" + uuid4().hex,
                "type": "reasoning",
                "summary": [],
                "encrypted_content": "opaque-reasoning",
            },
            {
                "id": "msg_" + uuid4().hex,
                "type": "message",
                "role": "assistant",
                "phase": "commentary",
                "status": "completed",
                "content": [
                    {"type": "output_text", "text": text, "annotations": [], "logprobs": []}
                ],
            },
            {
                "id": "fc_" + uuid4().hex,
                "call_id": "call_" + uuid4().hex,
                "type": "function_call",
                "name": name,
                "arguments": json.dumps(arguments or {"code": "print(3)"}),
                "status": "completed",
            },
        ],
        "usage": {
            "input_tokens": 10,
            "output_tokens": 5,
            "total_tokens": 15,
            "input_tokens_details": {"cached_tokens": 2, "cache_write_tokens": 8},
            "output_tokens_details": {"reasoning_tokens": 1},
        },
    }


class ResponsesAPI:
    def __init__(self, *replies):
        self.replies = deque(replies)
        self.requests = []
        self.received = threading.Event()

    def __enter__(self):
        api = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                api.requests.append(request)
                api.received.set()
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Connection", "close")
                self.end_headers()

                def send(event):
                    self.wfile.write(("data: " + json.dumps(event) + "\n\n").encode())
                    self.wfile.flush()

                try:
                    reply = api.replies.popleft()
                    if callable(reply):
                        reply = reply(send, request)
                    if reply is None:
                        api.stopped.wait(5)
                        return
                    if reply.get("type") == "error":
                        send(reply)
                        return
                    send({"type": "response.created", "response": {"id": reply["id"]}})
                    send(
                        {"type": "response.reasoning.delta", "delta": "Private provider reasoning"}
                    )
                    for item in reply["output"]:
                        if item["type"] == "message":
                            for content in item["content"]:
                                send(
                                    {"type": "response.output_text.delta", "delta": content["text"]}
                                )
                        if item["type"] == "function_call":
                            send(
                                {
                                    "type": "response.output_item.added",
                                    "item": {**item, "arguments": ""},
                                }
                            )
                            send(
                                {
                                    "type": "response.function_call_arguments.delta",
                                    "delta": item["arguments"],
                                }
                            )
                            send(
                                {
                                    "type": "response.function_call_arguments.done",
                                    "arguments": item["arguments"],
                                }
                            )
                    send({"type": "response.completed", "response": reply})
                except (BrokenPipeError, ConnectionResetError):
                    pass

        self.stopped = threading.Event()
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.server.server_port}/v1"
        return self

    def __exit__(self, *args):
        self.stopped.set()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
