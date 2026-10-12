from pathlib import Path

import httpx
import pandas as pd

from sandbox import tables
from sandbox.protocol import HeaderReader, header_bytes


def run(url: str, token: str, script: str, frame: Path, *, mode: str = "cell"):
    def body():
        yield header_bytes(
            {"mode": mode, "script": script, "input_bytes": tables.uncompressed_bytes(frame)}
        )
        yield frame.read_bytes()

    with httpx.stream(
        "POST",
        f"{url}/v1/run",
        content=body(),
        headers={"Authorization": f"Bearer {token}"},
        timeout=120,
    ) as response:
        reader, payload = HeaderReader(), b""
        for chunk in response.iter_bytes():
            payload += reader.feed(chunk)
        return response.status_code, reader.finish(), payload


def test_a_wrong_token_is_refused(sandbox_server, tmp_path):
    url, _ = sandbox_server
    frame = tmp_path / "in.parquet"
    tables.write_frame(frame, pd.DataFrame({"text": ["a"]}))

    response = httpx.post(
        f"{url}/v1/run",
        content=header_bytes({"mode": "cell", "script": "df = df", "input_bytes": 1})
        + frame.read_bytes(),
        headers={"Authorization": "Bearer wrong"},
    )

    assert response.status_code == 401


def test_children_that_outgrow_the_reservation_are_stopped_together(sandbox_server, tmp_path):
    url, token = sandbox_server
    frame = tmp_path / "in.parquet"
    tables.write_frame(frame, pd.DataFrame({"text": ["a"]}))
    script = """
import multiprocessing, time

def hold():
    block = b"x" * (600 * 1024**2)
    time.sleep(60)

workers = [multiprocessing.Process(target=hold) for _ in range(3)]
for worker in workers:
    worker.start()
for worker in workers:
    worker.join()
"""

    status, header, _ = run(url, token, script, frame)

    assert status == 200
    assert header["ok"] is False
    assert header["error"] == "The cell used more than 1.5 GiB of memory and was stopped."
    assert header["seconds"] < 60
