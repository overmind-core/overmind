import asyncio
import hashlib
import json
import logging
import os
import threading
import time
import uuid

import modal

STORE = "overmind-operational-events"
logger = logging.getLogger(__name__)


def pool_key(cls_name, model_path, max_model_len, enable_lora, max_lora_rank):
    identity = [cls_name, model_path, max_model_len, enable_lora, max_lora_rank]
    return "pool:" + hashlib.sha256(json.dumps(identity).encode()).hexdigest()


class Journal:
    def __init__(self, stream):
        self.stream = stream
        self.writer = uuid.uuid4().hex
        self.sequence = 0
        self.dropped = 0
        self.publication_failures = 0
        self.previous = None
        self.previous_initialized = False
        self.pending = []
        self.lock = threading.Lock()
        self.store = modal.Dict.from_name(STORE, create_if_missing=True)

    def emit(self, stage, *, completed=None, total=None, unit=None, **facts):
        # Telemetry loss must not turn a completed GPU operation into a repeated job.
        try:
            with self.lock:
                sequence = self.sequence + 1
                value = {
                    "stage": stage,
                    "completed": completed,
                    "total": total,
                    "unit": unit,
                    "source_at": time.time(),
                    "facts": {"worker_id": os.environ.get("MODAL_TASK_ID"), **facts},
                }
                self.sequence = sequence
                self.pending.append((sequence, value))
                if len(self.pending) > 1000:
                    self.pending.pop(0)
                    self.dropped += 1

                async def publish():
                    if not self.previous_initialized:
                        previous = await self.store.get.aio(self.stream, None)
                        if previous and previous.get("writer") != self.writer:
                            self.previous = previous.get("writer")
                        self.previous_initialized = True
                    head = {
                        "writer": self.writer,
                        "sequence": sequence,
                        "dropped": self.dropped,
                        "publication_failures": self.publication_failures,
                        "previous": self.previous,
                    }
                    for index, pending in self.pending:
                        await self.store.put.aio(f"{self.writer}:{index}", pending)
                    await self.store.put.aio(f"{self.writer}:head", head)
                    await self.store.put.aio(self.stream, head)

                async def bounded():
                    await asyncio.wait_for(publish(), timeout=3)

                asyncio.run(bounded())
                self.pending.clear()
        except Exception:
            self.publication_failures += 1
            logger.warning("Operational event publication unavailable", exc_info=False)
