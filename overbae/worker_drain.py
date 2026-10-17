from __future__ import annotations

import argparse
import time

from overbae.celery import app


def drain_mixed_io(timeout: int = 4200) -> None:
    inspector = app.control.inspect(timeout=5)
    queues = inspector.active_queues()
    if queues is None:
        raise RuntimeError("No worker replies; cannot establish a safe IO drain")
    mixed = {
        worker: {queue["name"] for queue in consumed}
        for worker, consumed in queues.items()
        if {"io", "io_traces"}.issubset({queue["name"] for queue in consumed})
    }
    if not mixed:
        print("No mixed IO consumers remain", flush=True)
        return
    completed = False
    try:
        for worker, consumed in mixed.items():
            for queue in consumed:
                reply = app.control.cancel_consumer(
                    queue, destination=[worker], reply=True, timeout=5
                )
                if not reply or any(
                    "error" in response for item in reply for response in item.values()
                ):
                    raise RuntimeError(f"Consumer cancellation was not acknowledged by {worker}")
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            targeted = app.control.inspect(destination=list(mixed), timeout=5)
            active, reserved = targeted.active(), targeted.reserved()
            if (
                active is None
                or reserved is None
                or any(worker not in active or worker not in reserved for worker in mixed)
            ):
                raise RuntimeError("A draining worker disappeared; its work must be reconciled")
            count = sum(len(active[worker]) + len(reserved[worker]) for worker in mixed)
            print(f"Mixed IO drain: {count} acknowledged or reserved tasks remain", flush=True)
            if count == 0:
                completed = True
                return
            time.sleep(5)
        raise TimeoutError("Mixed IO work did not drain; producer rollout must stop")
    finally:
        for worker, consumed in mixed.items():
            # Keep IO available if replacement fails; the dedicated worker owns traces.
            for queue in consumed - ({"io_traces"} if completed else set()):
                reply = app.control.add_consumer(queue, destination=[worker], reply=True, timeout=5)
                if not reply or any(
                    "error" in response for item in reply for response in item.values()
                ):
                    raise RuntimeError(f"Consumer restoration was not acknowledged by {worker}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--timeout", type=int, default=4200)
    drain_mixed_io(parser.parse_args().timeout)


if __name__ == "__main__":
    main()
