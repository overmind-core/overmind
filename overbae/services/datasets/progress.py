from __future__ import annotations

import time


class ProgressThrottle:
    """Coalesce counters without delaying stage boundaries or terminal updates."""

    def __init__(self, publish, interval=2.0):
        self.publish = publish
        self.interval = interval
        self.last_at = float("-inf")
        self.boundary = None

    def __call__(self, detail):
        now = time.monotonic()
        boundary = (detail.get("filename"), detail.get("stage"), detail.get("completed"))
        terminal = detail.get("stage") in {"complete", "failed", "cancelled"} or (
            detail.get("ocr_pages_total") is not None
            and detail.get("ocr_pages_completed") == detail["ocr_pages_total"]
        )
        if terminal or boundary != self.boundary or now - self.last_at >= self.interval:
            self.publish(detail)
            self.last_at, self.boundary = now, boundary
