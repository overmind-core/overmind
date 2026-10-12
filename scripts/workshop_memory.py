"""Peak memory of the Data Workshop pipeline at a given upload size.

Drives the real entry points (landing, the agent's tools, the row viewer) on a
synthetic chat dataset in a throwaway database, and samples the RSS of this
process and of the cell sandbox it spawns. No LLM or network calls.

    uv run python scripts/workshop_memory.py generate 500        # writes ~500 MB of JSONL
    systemd-run --user --scope -p MemoryMax=10G -p MemorySwapMax=0 \\
        uv run python scripts/workshop_memory.py run traces_500m.jsonl

Needs the compose Postgres (tests.settings). Inputs live in
~/.cache/overmind-workshop-memory unless WORKSHOP_MEMORY_DIR is set; results
append to results.jsonl there.
"""

from __future__ import annotations

import json
import os
import random
import sys
import tempfile
import threading
import time
from contextlib import contextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = Path(os.environ.get("WORKSHOP_MEMORY_DIR", Path.home() / ".cache/overmind-workshop-memory"))
GIB = 1024**3

FILTER = "df = df[df['score'] > 0.2]\n"
NORMALISE = "df['language'] = df['language'].str.upper()\n"
AUDIT = (
    "df = pd.DataFrame({'source_row': df['source_row'], "
    "'task_alignment': df['messages'].map(lambda m: isinstance(m, list) and len(m) > 1)})\n"
)


def _words(rng: random.Random, n: int) -> str:
    return " ".join(
        "".join(rng.choice("aeioutnshrdl") for _ in range(rng.randint(2, 9))) for _ in range(n)
    )


def generate(megabytes: int) -> Path:
    """Chat/SFT rows shaped like real workshop uploads: a system, user and
    assistant transcript with some tool calls, plus scalar and metadata columns."""
    DATA.mkdir(parents=True, exist_ok=True)
    path = DATA / f"traces_{megabytes}m.jsonl"
    rng = random.Random(megabytes)
    target = megabytes * 1024**2
    written = 0
    with path.open("w", encoding="utf-8") as fh:
        index = 0
        while written < target:
            turns = [
                {"role": "system", "content": _words(rng, 200)},
                {"role": "user", "content": _words(rng, rng.randint(40, 400))},
            ]
            if rng.random() < 0.3:
                turns.append(
                    {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [
                            {
                                "id": f"call_{index}",
                                "type": "function",
                                "function": {
                                    "name": "lookup",
                                    "arguments": json.dumps({"q": _words(rng, 5)}),
                                },
                            }
                        ],
                    }
                )
                turns.append(
                    {"role": "tool", "tool_call_id": f"call_{index}", "content": _words(rng, 80)}
                )
            turns.append({"role": "assistant", "content": _words(rng, rng.randint(40, 300))})
            row = {
                "id": f"ex-{index:08d}",
                "trace_id": f"{rng.getrandbits(128):032x}",
                "conversation_id": f"conv-{rng.randint(0, 999_999):07d}",
                "created_at": f"2026-01-{rng.randint(1, 28):02d}T12:00:00Z",
                "language": rng.choice(["en", "de", "fr", "es"]),
                "label": rng.choice(["billing", "refund", "other"]),
                "score": round(rng.random(), 4),
                "messages": turns,
                "tools": None,
                "metadata": {"source": "synthetic", "tier": rng.randint(1, 3)},
            }
            line = json.dumps(row) + "\n"
            fh.write(line)
            written += len(line)
            index += 1
    return path


def _rss(pid: int | str) -> int:
    try:
        with open(f"/proc/{pid}/status", encoding="ascii") as fh:
            for line in fh:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1]) * 1024
    except OSError:
        pass
    return 0


def _children(pid: int) -> list[int]:
    found = []
    for entry in os.listdir("/proc"):
        if not entry.isdigit():
            continue
        try:
            with open(f"/proc/{entry}/stat", encoding="ascii") as fh:
                fields = fh.read().rsplit(")", 1)[1].split()
        except OSError:
            continue
        if int(fields[1]) == pid:
            found.append(int(entry))
    return found


class Sampler:
    def __init__(self) -> None:
        self.pid = os.getpid()
        self.worker = self.sandbox = 0
        self._stop = threading.Event()
        threading.Thread(target=self._loop, daemon=True).start()

    def _loop(self) -> None:
        while not self._stop.wait(0.02):
            self.worker = max(self.worker, _rss(self.pid))
            for child in _children(self.pid):
                self.sandbox = max(self.sandbox, _rss(child))

    @contextmanager
    def step(self, name: str, results: list[dict]):
        self.worker = _rss(self.pid)
        self.sandbox = 0
        start, started = self.worker, time.monotonic()
        outcome = "ok"
        try:
            yield
        except Exception as exc:  # noqa: BLE001 — a failing step is a result, not a crash
            outcome = f"error: {str(exc)[:200]}"
        time.sleep(0.05)
        results.append(
            {
                "step": name,
                "worker_peak_gib": round(self.worker / GIB, 2),
                "worker_delta_gib": round((self.worker - start) / GIB, 2),
                "sandbox_peak_gib": round(self.sandbox / GIB, 2),
                "seconds": round(time.monotonic() - started, 1),
                "outcome": outcome,
            }
        )
        print(json.dumps(results[-1]), flush=True)


def _tool(tools, name: str, args: dict) -> None:
    out = getattr(tools, name)(args)
    if isinstance(out, dict) and out.get("ok") is False:
        raise RuntimeError(out.get("error") or json.dumps(out)[:200])


def run(filename: str) -> None:
    sys.path.insert(0, str(ROOT))
    sys.path.insert(0, str(ROOT / "tests"))
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "tests.settings")
    import django

    django.setup()
    from django.conf import settings
    from django.test.utils import setup_databases, teardown_databases

    media = tempfile.mkdtemp(prefix="workshop-memory-")
    settings.MEDIA_ROOT = media
    settings.DATABASES["default"]["NAME"] = "workshop_memory"
    state = setup_databases(verbosity=0, interactive=False)
    try:
        from factories import make_project, make_user

        from overbae.models import Dataset
        from overbae.services.datasets import land, paths, store
        from overbae.services.datasets.notebook.agent import Tools

        source = DATA / filename
        results: list[dict] = []
        sampler = Sampler()
        user = make_user()
        project = make_project(member=user)
        dataset = Dataset.objects.create(project=project, name="memory", intent="pending")
        with sampler.step("land", results):
            land.commit(dataset, land.read_file(source, filename=filename))
        dataset.refresh_from_db()
        if dataset.source is None:
            raise SystemExit("landing failed")
        tools = Tools(dataset.id, user, emit=lambda _event: None)
        tools.automatic = True
        with sampler.step("status (profile)", results):
            _tool(tools, "status", {})
        with sampler.step("query", results):
            _tool(tools, "query", {"sql": "SELECT label, count(*) FROM t GROUP BY 1"})
        with sampler.step("inspect", results):
            _tool(tools, "inspect", {"script": "print(len(df))"})
        with sampler.step("add_cell filter", results):
            _tool(tools, "add_cell", {"title": "Filter", "script": FILTER})
        with sampler.step("add_cell normalise", results):
            _tool(tools, "add_cell", {"title": "Normalise", "script": NORMALISE})
        with sampler.step("prepare_examples (reshape)", results):
            _tool(tools, "prepare_examples", {})
        with sampler.step("record_quality_review", results):
            _tool(
                tools,
                "record_quality_review",
                {
                    "checks": [{"name": "task_alignment", "evidence": "transcript present"}],
                    "script": AUDIT,
                },
            )
        dataset.refresh_from_db()
        active = dataset.active_cell
        path = paths.cell_path(dataset.id, active.id)
        with sampler.step("row viewer (last row)", results):
            store.read_rows(path, [store.row_count(path) - 1])
        with sampler.step("diff (source to active)", results):
            _tool(tools, "diff", {})
        record = {
            "input": filename,
            "bytes": source.stat().st_size,
            "rows": dataset.source.rows,
            "commit": os.popen(f"git -C {ROOT} rev-parse --short HEAD").read().strip(),
            "dirty": bool(os.popen(f"git -C {ROOT} status --porcelain overbae").read().strip()),
            "steps": results,
        }
        with (DATA / "results.jsonl").open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record) + "\n")
    finally:
        teardown_databases(state, verbosity=0)


if __name__ == "__main__":
    command, arg = sys.argv[1], sys.argv[2]
    if command == "generate":
        print(generate(int(arg)))
    elif command == "run":
        run(arg)
    else:
        raise SystemExit(__doc__)
