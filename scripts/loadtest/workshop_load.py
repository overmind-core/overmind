"""Load driver for the Data Workshop: the experiments E0–E8 from scripts/loadtest/README.md.

Traffic looks like the real clients: MCP JSON-RPC ``tools/call`` (Claude Code, Codex),
chunked uploads (``overmind dataset upload --json``) and the Console's REST and SSE calls.
Every request carries ``User-Agent: overmind-loadtest/1``.

With ``--local`` a sampler also reads the server directly (Postgres, Redis, docker stats),
which production does not allow; there the client view plus CloudWatch is the record.

Each run writes ``<out>/<experiment>-<timestamp>/result.json`` and ``summary.md``.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import platform
import statistics
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parents[2]
USER_AGENT = "overmind-loadtest/1"
BUSY = ("landing", "diagnosing", "running")
QUEUES = ("interactive", "landing", "batch", "control", "io", "io_traces")
HEAVY_CELL = (
    "df['_width'] = sum(df[c].astype(str).str.len() for c in df.columns)\n"
    "df = df.sort_values('_width').drop(columns='_width')\n"
)


def gib(value: str) -> float:
    units = {"KiB": 1 / 1024**2, "MiB": 1 / 1024, "GiB": 1.0, "B": 1 / 1024**3}
    for unit, scale in units.items():
        if value.endswith(unit):
            return round(float(value.removesuffix(unit)) * scale, 3)
    return 0.0


def load_env(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    pairs = (line.split("=", 1) for line in path.read_text().splitlines() if "=" in line)
    return {key.strip(): value.strip() for key, value in pairs}


def pct(values: list[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(round(q * (len(ordered) - 1))))]


def stats(values: list[float]) -> dict[str, Any]:
    return {
        "n": len(values),
        "p50": pct(values, 0.5),
        "p95": pct(values, 0.95),
        "p99": pct(values, 0.99),
        "max": max(values) if values else None,
        "mean": statistics.fmean(values) if values else None,
    }


@dataclass
class Sample:
    t: float
    kind: str
    status: int
    seconds: float
    error: str = ""


@dataclass
class Run:
    experiment: str
    args: dict[str, Any]
    started: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    samples: list[Sample] = field(default_factory=list)
    stages: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    steps: list[dict[str, Any]] = field(default_factory=list)
    server: list[dict[str, Any]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def stage(self, name: str, record: dict[str, Any]) -> None:
        self.stages.setdefault(name, []).append(record)


class Client:
    def __init__(self, base: str, key: str, project: str, run: Run) -> None:
        self.project = project
        self.run = run
        self.http = httpx.AsyncClient(
            base_url=base.rstrip("/"),
            headers={"X-Api-Key": key, "User-Agent": USER_AGENT},
            timeout=httpx.Timeout(120, connect=15),
            limits=httpx.Limits(max_connections=2000, max_keepalive_connections=200),
        )
        self.rpc_id = 0

    async def close(self) -> None:
        await self.http.aclose()

    async def call(self, kind: str, method: str, url: str, **kwargs) -> httpx.Response | None:
        start = time.monotonic()
        try:
            response = await self.http.request(method, url, **kwargs)
        except httpx.HTTPError as exc:
            self.run.samples.append(
                Sample(time.time(), kind, 0, time.monotonic() - start, type(exc).__name__)
            )
            return None
        error = "" if response.status_code < 400 else response.text[:200]
        self.run.samples.append(
            Sample(time.time(), kind, response.status_code, time.monotonic() - start, error)
        )
        return response

    async def mcp(self, tool: str, arguments: dict[str, Any]) -> dict[str, Any] | None:
        self.rpc_id += 1
        body = {
            "jsonrpc": "2.0",
            "id": self.rpc_id,
            "method": "tools/call",
            "params": {"name": tool, "arguments": arguments},
        }
        start = time.monotonic()
        try:
            response = await self.http.post(
                "/api/mcp/",
                json=body,
                headers={"Accept": "application/json, text/event-stream"},
            )
        except httpx.HTTPError as exc:
            self.run.samples.append(
                Sample(time.time(), f"mcp:{tool}", 0, time.monotonic() - start, type(exc).__name__)
            )
            return None
        seconds = time.monotonic() - start
        result: dict[str, Any] = {}
        error = ""
        try:
            result = response.json().get("result") or {}
            if result.get("isError"):
                structured = result.get("structuredContent") or {}
                error = str((structured.get("error") or {}).get("code") or "tool_error")
        except ValueError:
            error = response.text[:200]
        status = response.status_code if response.status_code >= 400 or not error else 422
        self.run.samples.append(Sample(time.time(), f"mcp:{tool}", status, seconds, error))
        return result

    async def upload(self, path: Path, intent: str = "train") -> dict[str, Any]:
        """The CLI's sequence: reserve, chunks, inspect, create."""
        timing: dict[str, Any] = {"file": path.name, "bytes": path.stat().st_size}
        t0 = time.monotonic()
        reserved = await self.call(
            "upload:reserve", "POST", "/api/uploads/", json={"filename": path.name}
        )
        if reserved is None or reserved.status_code >= 400:
            timing["error"] = "reserve failed"
            return timing
        upload_id = reserved.json()["upload_id"]
        chunk_bytes = int(reserved.json()["chunk_bytes"])
        sent = 0
        with path.open("rb") as source:
            while chunk := source.read(chunk_bytes):
                response = await self.call(
                    "upload:chunk",
                    "PUT",
                    f"/api/uploads/{upload_id}/chunk/",
                    params={"offset": sent},
                    content=chunk,
                    headers={"Content-Type": "application/octet-stream"},
                )
                if response is None or response.status_code >= 400:
                    timing["error"] = "chunk failed"
                    return timing
                sent = int(response.json()["received"])
        timing["upload_s"] = time.monotonic() - t0
        inspected = await self.call(
            "upload:inspect", "POST", f"/api/uploads/{upload_id}/inspect/", json={"size": sent}
        )
        if inspected is None or inspected.status_code >= 400:
            timing["error"] = "inspect failed"
            return timing
        created = await self.call(
            "dataset:create",
            "POST",
            "/api/datasets/",
            json={
                "project": self.project,
                "name": f"loadtest {path.stem}",
                "source": {"upload_id": upload_id, "filename": path.name},
                "intent": intent,
            },
        )
        if created is None or created.status_code >= 400:
            timing["error"] = "create failed"
            return timing
        timing["dataset_id"] = created.json()["id"]
        timing["created_s"] = time.monotonic() - t0
        timing["t0"] = t0
        return timing

    async def dataset(self, dataset_id: str) -> dict[str, Any] | None:
        response = await self.call("rest:dataset", "GET", f"/api/datasets/{dataset_id}/")
        if response is None or response.status_code >= 400:
            return None
        return response.json()

    async def follow(
        self, dataset_id: str, t0: float, until: str, timeout: float, *, uploaded: bool = False
    ) -> dict[str, Any]:
        """Poll once a second; record when the dataset leaves ``landing`` and when it is idle."""
        seen: dict[str, Any] = {"landing": True} if uploaded else {}
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            body = await self.dataset(dataset_id)
            state = (body or {}).get("state")
            now = time.monotonic() - t0
            if state == "landing":
                seen["landing"] = True
            if state and state != "landing" and seen.get("landing") and "landed_s" not in seen:
                seen["landed_s"] = now
            if state == "error":
                seen["error"] = (body or {}).get("error") or "error"
                return seen
            if until == "landed" and "landed_s" in seen:
                seen.pop("landing", None)
                return seen
            if state and state not in BUSY:
                seen.pop("landing", None)
                seen["ready_s"] = now
                return seen
            await asyncio.sleep(1)
        seen["error"] = f"timeout after {timeout}s"
        return seen

    async def ready_datasets(self, limit: int) -> list[str]:
        response = await self.call(
            "rest:list", "GET", "/api/datasets/", params={"project": self.project, "limit": 200}
        )
        if response is None or response.status_code >= 400:
            return []
        body = response.json()
        rows = body.get("results", body) if isinstance(body, dict) else body
        return [row["id"] for row in rows if row.get("state") == "idle"][:limit]


class Sampler:
    """Localhost only: queue depth, oldest waits, connections and container usage every 2 s."""

    def __init__(self, run: Run, project: str) -> None:
        import psycopg2
        import redis

        self.run = run
        self.project = project
        self.db = psycopg2.connect("postgresql://overbae:overbae@localhost:5432/overbae")
        self.db.autocommit = True
        self.redis = redis.Redis(host="localhost", port=6379, db=0)
        self.task: asyncio.Task | None = None
        self.docker: dict[str, Any] = {}
        self.clocks: dict[str, dict[str, Any]] = {}

    def _query(self) -> dict[str, Any]:
        with self.db.cursor() as cur:
            cur.execute("select count(*) from pg_stat_activity where datname = 'overbae'")
            connections = cur.fetchone()[0]
            cur.execute(
                "select count(*), extract(epoch from now() - min(queued_at)) "
                "from overbae_datasetimport where state = 'queued'"
            )
            imports_waiting, import_oldest = cur.fetchone()
            cur.execute(
                "select id, state, workshop_queued_at, workshop_started_at "
                "from overbae_dataset where project_id = %s",
                (self.project,),
            )
            rows = cur.fetchall()
        now = datetime.now(UTC)
        turn_waits = [
            (now - queued).total_seconds()
            for _, state, queued, started in rows
            if queued is not None and started is None
        ]
        for dataset_id, _state, queued, started in rows:
            clock = self.clocks.setdefault(str(dataset_id), {})
            if queued is not None and started is not None and queued.isoformat() not in clock:
                clock[queued.isoformat()] = (started - queued).total_seconds()
        states: dict[str, int] = {}
        for _, state, _, _ in rows:
            states[state] = states.get(state, 0) + 1
        return {
            "t": time.time(),
            "pg_connections": connections,
            "imports_waiting": imports_waiting,
            "import_oldest_s": float(import_oldest or 0),
            "turns_waiting": len(turn_waits),
            "turn_oldest_s": max(turn_waits, default=0.0),
            "dataset_states": states,
            "queue_depth": {q: self.redis.llen(q) for q in QUEUES},
        }

    def _docker(self) -> None:
        out = subprocess.run(
            ["docker", "stats", "--no-stream", "--format", "{{json .}}"],
            capture_output=True,
            text=True,
            timeout=30,
        ).stdout
        for line in out.splitlines():
            row = json.loads(line)
            if not row["Name"].startswith("overmind-dev-"):
                continue
            name = row["Name"].removeprefix("overmind-dev-").removesuffix("-1")
            cpu = float(row["CPUPerc"].rstrip("%") or 0)
            mem = row["MemUsage"].split("/")[0].strip()
            peak = self.docker.setdefault(name, {"cpu_max": 0.0, "mem_max_gib": 0.0})
            peak["cpu_max"] = max(peak["cpu_max"], cpu)
            peak["mem_max_gib"] = max(peak["mem_max_gib"], gib(mem))
            peak["mem_last"] = mem

    async def _loop(self) -> None:
        tick = 0
        while True:
            self.run.server.append(await asyncio.to_thread(self._query))
            if tick % 3 == 0:
                await asyncio.to_thread(self._docker)
            tick += 1
            await asyncio.sleep(2)

    def start(self) -> None:
        self.task = asyncio.create_task(self._loop())

    async def stop(self) -> dict[str, Any]:
        if self.task:
            self.task.cancel()
        waits = [w for clocks in self.clocks.values() for w in clocks.values()]
        return {"containers": self.docker, "turn_queue_wait_s": stats(waits)}

    def latest(self) -> dict[str, Any]:
        return self.run.server[-1] if self.run.server else {}


def breached(run: Run, sampler: Sampler | None, since: float) -> str:
    """The global stop conditions; an empty string means carry on."""
    recent = [s for s in run.samples if s.t >= max(since, time.time() - 60)]
    if len(recent) >= 20:
        failed = sum(1 for s in recent if s.status == 0 or s.status >= 500)
        if failed / len(recent) > 0.02:
            return f"{failed}/{len(recent)} requests failed in the last minute"
    if sampler:
        last = sampler.latest()
        if last.get("turn_oldest_s", 0) > 300:
            return "an agent turn has waited more than 5 minutes"
        if last.get("import_oldest_s", 0) > 300:
            return "an import has waited more than 5 minutes"
    return ""


async def ensure_ready(client: Client, run: Run, count: int, fixture: Path) -> list[str]:
    ids = await client.ready_datasets(count)
    missing = count - len(ids)
    if missing > 0:
        run.notes.append(f"uploaded {missing} datasets to read from")
        timings = await asyncio.gather(*(client.upload(fixture) for _ in range(missing)))
        created = [t for t in timings if "dataset_id" in t]
        follows = await asyncio.gather(
            *(
                client.follow(t["dataset_id"], t["t0"], "ready", 1800, uploaded=True)
                for t in created
            )
        )
        ids += [t["dataset_id"] for t, f in zip(created, follows, strict=True) if "ready_s" in f]
    return ids


async def upload_and_follow(
    client: Client, run: Run, fixture: Path, until: str, label: str
) -> None:
    timing = await client.upload(fixture)
    if "dataset_id" in timing:
        timing.update(
            await client.follow(timing["dataset_id"], timing.pop("t0"), until, 3600, uploaded=True)
        )
    timing.pop("t0", None)
    run.stage(label, timing)


# Experiments ------------------------------------------------------------------------------


async def calibrate(client: Client, run: Run, args, fixtures: Path, sampler) -> None:
    for repeat in range(args.repeats):
        fixture = fixtures / f"chat_{args.size}mb.jsonl"
        timing = await client.upload(fixture)
        if "dataset_id" not in timing:
            run.stage("calibrate", timing)
            continue
        dataset_id = timing["dataset_id"]
        timing.update(
            await client.follow(dataset_id, timing.pop("t0"), "ready", 3600, uploaded=True)
        )
        start = time.monotonic()
        await client.mcp("inspect_dataset", {"dataset": dataset_id})
        await client.mcp("query_dataset", {"dataset": dataset_id, "sql": "select count(*) from t"})
        await client.call(
            "rest:rows", "GET", f"/api/datasets/{dataset_id}/rows/", params={"limit": 50}
        )
        timing["reads_s"] = time.monotonic() - start
        chat_start = time.monotonic()
        await client.call(
            "rest:chat",
            "POST",
            f"/api/datasets/{dataset_id}/chat/",
            json={"message": "Check the data."},
        )
        await asyncio.sleep(1)
        follow = await client.follow(dataset_id, chat_start, "ready", 3600)
        timing["chat_turn_s"] = follow.get("ready_s")
        cell = await client.call(
            "rest:add_cell",
            "POST",
            f"/api/datasets/{dataset_id}/cells/",
            json={"title": "Order by width", "script": HEAVY_CELL},
        )
        if cell is not None and cell.status_code < 400:
            run_start = time.monotonic()
            await client.call("rest:run", "POST", f"/api/datasets/{dataset_id}/run/")
            await asyncio.sleep(1)
            timing["cell_run_s"] = (await client.follow(dataset_id, run_start, "ready", 3600)).get(
                "ready_s"
            )
        run.stage("calibrate", {"repeat": repeat, **timing})


async def reads(client: Client, run: Run, args, fixtures: Path, sampler) -> None:
    ids = await ensure_ready(client, run, args.datasets, fixtures / "chat_1mb.jsonl")
    if not ids:
        run.notes.append("no ready dataset to read from")
        return
    operations = [
        lambda d: client.mcp("list_datasets", {}),
        lambda d: client.mcp("inspect_dataset", {"dataset": d}),
        lambda d: client.mcp("query_dataset", {"dataset": d, "sql": "select * from t limit 20"}),
        lambda d: client.call("rest:rows", "GET", f"/api/datasets/{d}/rows/", params={"limit": 50}),
        lambda d: client.call("rest:columns", "GET", f"/api/datasets/{d}/columns/"),
    ]
    weights = [12, 31, 18, 20, 19]
    plan = [op for op, w in zip(operations, weights, strict=True) for _ in range(w)]
    for rps in [float(r) for r in args.rps.split(",")]:
        step_start = time.time()
        stop = ""
        tasks = []
        index = 0
        while time.time() - step_start < args.step_seconds:
            op = plan[index % len(plan)]
            tasks.append(asyncio.create_task(op(ids[index % len(ids)])))
            index += 1
            await asyncio.sleep(1 / rps)
            if index % max(1, int(rps * 10)) == 0 and (stop := breached(run, sampler, step_start)):
                break
        await asyncio.gather(*tasks)
        run.steps.append(step_record(run, f"{rps} rps", step_start, stop))
        if stop:
            break


async def sse(client: Client, run: Run, args, fixtures: Path, sampler) -> None:
    ids = await ensure_ready(client, run, args.datasets, fixtures / "chat_1mb.jsonl")
    if not ids:
        run.notes.append("no ready dataset to stream")
        return

    async def watch(dataset_id: str, hold: float) -> dict[str, Any]:
        record: dict[str, Any] = {"pings": 0, "events": 0}
        start = time.monotonic()
        last = start
        try:
            async with client.http.stream(
                "GET", f"/api/datasets/{dataset_id}/events/", timeout=httpx.Timeout(hold + 60)
            ) as response:
                record["status"] = response.status_code
                async for line in response.aiter_lines():
                    now = time.monotonic()
                    if line.startswith("data:"):
                        record["events"] += 1
                        record.setdefault("first_event_s", now - start)
                        if "replay.done" in line:
                            record["replay_done_s"] = now - start
                    elif line.startswith(": ping"):
                        record["pings"] += 1
                        record["max_gap_s"] = max(record.get("max_gap_s", 0), now - last)
                    last = now
                    if now - start >= hold:
                        break
        except httpx.HTTPError as exc:
            record["error"] = type(exc).__name__
        record["held_s"] = time.monotonic() - start
        return record

    for count in [int(c) for c in args.streams.split(",")]:
        step_start = time.time()
        streams = [watch(ids[i % len(ids)], args.hold) for i in range(count)]
        probe_task = asyncio.create_task(probe_reads(client, ids[0], args.hold))
        records = await asyncio.gather(*streams)
        await probe_task
        for record in records:
            run.stage(f"sse {count}", record)
        first = [r["first_event_s"] for r in records if "first_event_s" in r]
        failed = sum(1 for r in records if r.get("error") or r.get("status", 200) >= 400)
        step = step_record(run, f"{count} streams", step_start, "")
        step.update({"first_event_s": stats(first), "streams_failed": failed})
        run.steps.append(step)


async def probe_reads(client: Client, dataset_id: str, seconds: float) -> None:
    """One read every 2 s while streams are open: does the API still answer?"""
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        await client.call(
            "probe:rows", "GET", f"/api/datasets/{dataset_id}/rows/", params={"limit": 10}
        )
        await asyncio.sleep(2)


async def burst(client: Client, run: Run, args, fixtures: Path, sampler) -> None:
    fixture = fixtures / f"chat_{args.size}mb.jsonl"
    for count in [int(c) for c in args.parallel.split(",")]:
        step_start = time.time()
        label = f"burst {count} x {args.size}MB"
        await asyncio.gather(
            *(upload_and_follow(client, run, fixture, args.until, label) for _ in range(count))
        )
        stop = breached(run, sampler, step_start)
        step = step_record(run, label, step_start, stop)
        records = run.stages.get(label, [])
        step["landed_s"] = stats([r["landed_s"] for r in records if "landed_s" in r])
        step["ready_s"] = stats([r["ready_s"] for r in records if "ready_s" in r])
        step["errors"] = [r["error"] for r in records if "error" in r]
        run.steps.append(step)
        if stop:
            break
        await settle(client, run)


async def chat(client: Client, run: Run, args, fixtures: Path, sampler) -> None:
    counts = [int(c) for c in args.parallel.split(",")]
    ids = await ensure_ready(client, run, max(counts), fixtures / "chat_1mb.jsonl")

    async def one(dataset_id: str, label: str) -> None:
        start = time.monotonic()
        response = await client.call(
            "rest:chat",
            "POST",
            f"/api/datasets/{dataset_id}/chat/",
            json={"message": "Check the data."},
        )
        record: dict[str, Any] = {"status": response.status_code if response is not None else 0}
        await asyncio.sleep(1)
        record.update(await client.follow(dataset_id, start, "ready", 3600))
        run.stage(label, record)

    for count in counts:
        step_start = time.time()
        label = f"chat {count}"
        await asyncio.gather(*(one(dataset_id, label) for dataset_id in ids[:count]))
        step = step_record(run, label, step_start, breached(run, sampler, step_start))
        step["turn_s"] = stats([r["ready_s"] for r in run.stages.get(label, []) if "ready_s" in r])
        run.steps.append(step)


async def collide(client: Client, run: Run, args, fixtures: Path, sampler) -> None:
    """Message the agent while its automatic turn runs, as the MCP callers in production did."""
    fixture = fixtures / "chat_1mb.jsonl"
    for attempt in range(args.tries):
        timing = await client.upload(fixture)
        if "dataset_id" not in timing:
            continue
        dataset_id = timing["dataset_id"]
        state = "landing"
        while state == "landing":
            state = ((await client.dataset(dataset_id)) or {}).get("state", "error")
            await asyncio.sleep(0.5)
        result = await client.mcp(
            "message_dataset_agent", {"dataset": dataset_id, "message": "Also drop duplicates."}
        )
        structured = (result or {}).get("structuredContent") or {}
        run.stage(
            "collide",
            {
                "attempt": attempt,
                "state_when_sent": state,
                "is_error": bool((result or {}).get("isError")),
                "code": (structured.get("error") or {}).get("code"),
            },
        )
        await client.follow(dataset_id, time.monotonic(), "ready", 3600)


async def sandbox(client: Client, run: Run, args, fixtures: Path, sampler) -> None:
    counts = [int(c) for c in args.parallel.split(",")]
    ids = await ensure_ready(client, run, max(counts), fixtures / f"chat_{args.size}mb.jsonl")

    async def one(dataset_id: str, label: str) -> None:
        cell = await client.call(
            "rest:add_cell",
            "POST",
            f"/api/datasets/{dataset_id}/cells/",
            json={"title": "Order by width", "script": HEAVY_CELL},
        )
        if cell is None or cell.status_code >= 400:
            run.stage(label, {"error": cell.text[:200] if cell is not None else "no response"})
            return
        start = time.monotonic()
        await client.call("rest:run", "POST", f"/api/datasets/{dataset_id}/run/")
        await asyncio.sleep(1)
        run.stage(label, await client.follow(dataset_id, start, "ready", 3600))

    for count in counts:
        step_start = time.time()
        label = f"sandbox {count} x {args.size}MB"
        await asyncio.gather(*(one(dataset_id, label) for dataset_id in ids[:count]))
        step = step_record(run, label, step_start, breached(run, sampler, step_start))
        step["run_s"] = stats([r["ready_s"] for r in run.stages.get(label, []) if "ready_s" in r])
        step["errors"] = [r["error"] for r in run.stages.get(label, []) if "error" in r]
        run.steps.append(step)


# Hackathon week, 5–8 Oct 2026, per hour on average (PostHog): the 1x profile.
MIX_PER_HOUR = {
    "upload": 4.2,
    "mcp:list_datasets": 1.2,
    "mcp:inspect_dataset": 3.1,
    "mcp:query_dataset": 1.8,
    "chat": 0.6,
    "rest:rows": 3.6,
    "rest:columns": 3.6,
    "sse": 3.6,
}


async def mix(client: Client, run: Run, args, fixtures: Path, sampler) -> None:
    ids = await ensure_ready(client, run, args.datasets, fixtures / "chat_1mb.jsonl")
    fixture = fixtures / "chat_10mb.jsonl"
    actions = {
        "upload": lambda d: upload_and_follow(client, run, fixture, "ready", "mix upload"),
        "mcp:list_datasets": lambda d: client.mcp("list_datasets", {}),
        "mcp:inspect_dataset": lambda d: client.mcp("inspect_dataset", {"dataset": d}),
        "mcp:query_dataset": lambda d: client.mcp(
            "query_dataset", {"dataset": d, "sql": "select * from t limit 20"}
        ),
        "chat": lambda d: client.mcp(
            "message_dataset_agent", {"dataset": d, "message": "Check the data."}
        ),
        "rest:rows": lambda d: client.call("rest:rows", "GET", f"/api/datasets/{d}/rows/"),
        "rest:columns": lambda d: client.call("rest:columns", "GET", f"/api/datasets/{d}/columns/"),
        "sse": lambda d: hold_stream(client, d, 120),
    }
    for multiplier in [float(m) for m in args.multiplier.split(",")]:
        step_start = time.time()
        rates = {name: per_hour * multiplier / 3600 for name, per_hour in MIX_PER_HOUR.items()}
        due = dict.fromkeys(rates, 0.0)
        tasks = []
        stop = ""
        tick = 0
        while time.time() - step_start < args.step_seconds:
            for name, rate in rates.items():
                due[name] += rate
                while due[name] >= 1:
                    due[name] -= 1
                    tasks.append(asyncio.create_task(actions[name](ids[tick % len(ids)])))
            tick += 1
            await asyncio.sleep(1)
            if tick % 30 == 0 and (stop := breached(run, sampler, step_start)):
                break
        await asyncio.gather(*tasks)
        run.steps.append(step_record(run, f"mix {multiplier}x", step_start, stop))
        if stop:
            break


async def hold_stream(client: Client, dataset_id: str, seconds: float) -> None:
    start = time.monotonic()
    try:
        async with client.http.stream(
            "GET", f"/api/datasets/{dataset_id}/events/", timeout=httpx.Timeout(seconds + 60)
        ) as response:
            async for _ in response.aiter_lines():
                if time.monotonic() - start >= seconds:
                    break
        client.run.samples.append(Sample(time.time(), "sse:hold", response.status_code, seconds))
    except httpx.HTTPError as exc:
        client.run.samples.append(
            Sample(time.time(), "sse:hold", 0, time.monotonic() - start, type(exc).__name__)
        )


async def settle(client: Client, run: Run, timeout: float = 1800) -> None:
    """Wait until no load-test dataset is busy, so one step's tail does not load the next."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        response = await client.call(
            "rest:list", "GET", "/api/datasets/", params={"project": client.project, "limit": 200}
        )
        rows = response.json() if response is not None and response.status_code < 400 else []
        rows = rows.get("results", rows) if isinstance(rows, dict) else rows
        if not any(row.get("state") in BUSY for row in rows):
            return
        await asyncio.sleep(5)
    run.notes.append("datasets were still busy when the next step started")


def step_record(run: Run, label: str, since: float, stop: str) -> dict[str, Any]:
    window = [s for s in run.samples if s.t >= since]
    kinds: dict[str, list[Sample]] = {}
    for sample in window:
        kinds.setdefault(sample.kind, []).append(sample)
    return {
        "step": label,
        "seconds": round(time.time() - since, 1),
        "stopped": stop,
        "requests": len(window),
        "failed": sum(1 for s in window if s.status == 0 or s.status >= 500),
        "by_kind": {
            kind: {
                **stats([s.seconds for s in samples]),
                "errors": sorted({s.error for s in samples if s.error})[:5],
                "error_count": sum(1 for s in samples if s.error),
            }
            for kind, samples in sorted(kinds.items())
        },
        "server_peak": server_peak(run, since),
    }


def server_peak(run: Run, since: float) -> dict[str, Any]:
    window = [s for s in run.server if s["t"] >= since]
    if not window:
        return {}
    return {
        "pg_connections": max(s["pg_connections"] for s in window),
        "import_oldest_s": max(s["import_oldest_s"] for s in window),
        "turns_waiting": max(s["turns_waiting"] for s in window),
        "turn_oldest_s": max(s["turn_oldest_s"] for s in window),
        "queue_depth": {q: max(s["queue_depth"].get(q, 0) for s in window) for q in QUEUES},
    }


EXPERIMENTS = {
    "calibrate": calibrate,
    "reads": reads,
    "sse": sse,
    "burst": burst,
    "chat": chat,
    "collide": collide,
    "sandbox": sandbox,
    "mix": mix,
}


def summary_md(run: Run, result: dict[str, Any]) -> str:
    lines = [
        f"# {run.experiment}",
        "",
        f"- started: {run.started}",
        f"- args: `{json.dumps(run.args)}`",
    ]
    lines += [f"- note: {note}" for note in run.notes]
    for step in run.steps:
        lines += [
            "",
            f"## {step['step']}" + (f" (stopped: {step['stopped']})" if step["stopped"] else ""),
        ]
        lines += [f"- requests {step['requests']}, failed {step['failed']}, {step['seconds']} s"]
        for key in ("landed_s", "ready_s", "turn_s", "run_s", "first_event_s"):
            if key in step:
                lines.append(f"- {key}: {json.dumps(step[key])}")
        if step.get("server_peak"):
            lines.append(f"- server peak: `{json.dumps(step['server_peak'])}`")
        lines += ["", "| kind | n | p50 | p95 | p99 | errors |", "|---|---|---|---|---|---|"]
        for kind, s in step["by_kind"].items():
            fmt = lambda v: "" if v is None else f"{v:.3f}"  # noqa: E731
            lines.append(
                f"| {kind} | {s['n']} | {fmt(s['p50'])} | {fmt(s['p95'])} | {fmt(s['p99'])} | {s['error_count']} |"
            )
    for name, records in run.stages.items():
        if name in ("calibrate", "collide"):
            lines += ["", f"## {name}", "```", *(json.dumps(r) for r in records), "```"]
    if result.get("server"):
        lines += [
            "",
            "## Server (local sampler)",
            "```",
            json.dumps(result["server"], indent=1),
            "```",
        ]
    return "\n".join(lines) + "\n"


async def main() -> int:
    env_file = ".env.loadtest.prod.local" if "--prod" in sys.argv else ".env.loadtest.local"
    env = {**load_env(ROOT / env_file), **os.environ}
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("experiment", choices=sorted(EXPERIMENTS))
    parser.add_argument("--base", default=env.get("LOADTEST_BASE_URL", "http://localhost:8000"))
    parser.add_argument(
        "--local", action="store_true", help="sample Postgres, Redis and docker stats"
    )
    parser.add_argument("--prod", action="store_true", help="use .env.loadtest.prod.local")
    parser.add_argument("--fixtures", type=Path, default=ROOT / "scripts/loadtest/.fixtures")
    parser.add_argument("--out", type=Path, default=ROOT / "scripts/loadtest/runs")
    parser.add_argument("--repeats", type=int, default=5)
    parser.add_argument("--size", default="10")
    parser.add_argument("--rps", default="1,5,10,25,50")
    parser.add_argument("--step-seconds", type=float, default=180)
    parser.add_argument("--datasets", type=int, default=5)
    parser.add_argument("--streams", default="10,50,100,200")
    parser.add_argument("--hold", type=float, default=600)
    parser.add_argument("--parallel", default="1,2,4,8,16")
    parser.add_argument("--until", choices=("landed", "ready"), default="ready")
    parser.add_argument("--tries", type=int, default=10)
    parser.add_argument("--multiplier", default="10,50,100")
    args = parser.parse_args()

    key = env.get("LOADTEST_API_KEY")
    project = env.get("LOADTEST_PROJECT_ID")
    if not key or not project:
        print(
            "Set LOADTEST_API_KEY and LOADTEST_PROJECT_ID (see bootstrap_local.py).",
            file=sys.stderr,
        )
        return 2
    if args.local and "localhost" not in args.base and "127.0.0.1" not in args.base:
        print("--local reads the local database; use it only against localhost.", file=sys.stderr)
        return 2

    recorded = {k: v for k, v in vars(args).items() if k not in ("fixtures", "out")}
    run = Run(args.experiment, recorded)
    client = Client(args.base, key, project, run)
    sampler = Sampler(run, project) if args.local else None
    if sampler:
        sampler.start()
    try:
        await EXPERIMENTS[args.experiment](client, run, args, args.fixtures, sampler)
    finally:
        server = await sampler.stop() if sampler else {}
        await client.close()

    sha = subprocess.run(
        ["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, cwd=ROOT
    ).stdout.strip()
    result = {
        "experiment": run.experiment,
        "command": " ".join(sys.argv),
        "git_sha": sha,
        "environment": {"base": args.base, "local_sampler": args.local, "host": platform.node()},
        "started": run.started,
        "finished": datetime.now(UTC).isoformat(),
        "steps": run.steps,
        "stages": run.stages,
        "notes": run.notes,
        "server": server,
        "server_samples": run.server,
        "samples": [asdict(s) for s in run.samples],
    }
    folder = args.out / f"{run.experiment}-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "result.json").write_text(json.dumps(result, indent=1, default=str))
    (folder / "summary.md").write_text(summary_md(run, result))
    print(folder / "summary.md")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
