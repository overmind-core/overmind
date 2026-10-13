import json
import logging
import math
import re
import sqlite3
from collections import defaultdict
from contextlib import closing

import modal
from django.core.cache import cache
from django.utils import timezone

from modal_shared.decision_metrics import score_decision
from modal_shared.serving.artifacts import atomic_json, digest_file
from overbae.core.errors import InputValidationError
from overbae.services.decision_benchmark_scoring import METRICS, records
from overbae.services.evaluation_inputs import artifact_lock
from overbae.services.native_evaluation import calibrated, directory, participants, provider_stage

logger = logging.getLogger(__name__)


def connect(path, *, readonly=False):
    db = (
        sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5)
        if readonly
        else sqlite3.connect(path, timeout=30)
    )
    db.row_factory = sqlite3.Row
    return db


def initialise(db, source):
    db.executescript("""
        CREATE TABLE IF NOT EXISTS samples (
            key TEXT PRIMARY KEY, ordinal INTEGER, hash TEXT, benchmark TEXT,
            input TEXT, reference TEXT, error TEXT NOT NULL DEFAULT ''
        );
        CREATE INDEX IF NOT EXISTS sample_order ON samples(ordinal);
        CREATE INDEX IF NOT EXISTS sample_hash ON samples(hash);
        CREATE INDEX IF NOT EXISTS sample_benchmark ON samples(benchmark);
        CREATE TABLE IF NOT EXISTS predictions (
            arm TEXT, key TEXT, raw TEXT, adjusted TEXT, metrics TEXT, adjusted_metrics TEXT,
            error TEXT NOT NULL DEFAULT '', PRIMARY KEY(arm,key)
        );
        CREATE TABLE IF NOT EXISTS receipts (id TEXT PRIMARY KEY, checksum TEXT);
    """)
    if db.execute("SELECT 1 FROM receipts WHERE id='suite'").fetchone():
        return
    manifest = json.loads((source / "manifest.json").read_text())
    for name, checksum in manifest["files"].items():
        if digest_file(source / name) != checksum:
            raise ValueError("Frozen suite checksum differs")
    for row in records(source / "inputs.jsonl"):
        db.execute(
            "INSERT INTO samples(key,ordinal,hash,input) VALUES(?,?,?,?)",
            (row["key"], int(row["key"]), row["input_sha256"], json.dumps(row["decision"])),
        )
    for row in records(source / "references.jsonl"):
        changed = db.execute(
            "UPDATE samples SET benchmark=?,reference=? WHERE key=? AND hash=?",
            (row["benchmark"], json.dumps(row["reference"]), row["key"], row["input_sha256"]),
        )
        if changed.rowcount != 1:
            raise ValueError("Reference identity differs from the frozen input")
    for row in records(source / "failures.jsonl"):
        db.execute(
            "INSERT INTO samples(key,ordinal,benchmark,input,reference,error) VALUES(?,?,?,?,?,?)",
            (
                str(row["source_row"]),
                row["source_row"],
                row["benchmark"],
                json.dumps(row.get("input")),
                json.dumps(row.get("reference")),
                row["error"],
            ),
        )
    db.execute("INSERT INTO receipts VALUES('suite',?)", (digest_file(source / "manifest.json"),))


def ingest(db, plan, arm, prediction):
    row = db.execute("SELECT * FROM samples WHERE key=?", (prediction["key"],)).fetchone()
    if row is None or row["hash"] != prediction.get("input_sha256") or row["error"]:
        raise ValueError("Prediction does not identify a compatible frozen input")
    prediction = dict(prediction)
    floor = plan.config.get("logarithm_floor")
    metrics, error = {}, ""
    try:
        if floor is not None:
            prediction["log_probabilities"] = [
                math.log(max(p, floor)) for p in prediction["probabilities"]
            ]
        request, reference = json.loads(row["input"]), json.loads(row["reference"])
        if prediction.get("kind") != request["kind"]:
            raise ValueError("Prediction kind differs from the frozen input")
        metrics = score_decision(request, reference, prediction)
    except (ValueError, TypeError, KeyError) as exc:
        error = str(exc)
    raw = json.dumps(prediction, sort_keys=True, allow_nan=False)
    previous = db.execute(
        "SELECT raw FROM predictions WHERE arm=? AND key=?", (arm, row["key"])
    ).fetchone()
    if previous:
        if previous["raw"] != raw:
            raise ValueError("Recorded prediction changed for this model and input")
        return
    db.execute(
        "INSERT INTO predictions(arm,key,raw,metrics,error) VALUES(?,?,?,?,?)",
        (arm, row["key"], raw, json.dumps(metrics), error),
    )


def ingest_file(db, plan, arm, path, receipt_id, checksum):
    previous = db.execute("SELECT checksum FROM receipts WHERE id=?", (receipt_id,)).fetchone()
    if previous:
        if previous["checksum"] != checksum:
            raise ValueError("Recorded prediction chunk changed")
        return
    if digest_file(path) != checksum:
        raise ValueError("Prediction chunk checksum differs")
    for prediction in records(path):
        ingest(db, plan, arm, prediction)
    db.execute("INSERT INTO receipts VALUES(?,?)", (receipt_id, checksum))


def collect_progress(plan, stage, call):
    role, participant, preparing, group = provider_stage(plan, stage)
    if preparing or participant["kind"] == "external" or not call.get("id"):
        return
    arm = participant["key"]
    if re.fullmatch(r"fc-[A-Za-z0-9_-]+", call["id"]) is None:
        raise ValueError("Invalid provider call identity")
    evaluation_id = f"{plan.id}-{role}" + ("" if group == "prepare" else "-" + group)
    remote = f"/decision-evaluations/{evaluation_id}/progress/{call['id']}"
    volume = modal.Volume.from_name(
        "overmind-sft", environment_name=plan.config["runtime"]["environment"]
    )
    manifest = json.loads(b"".join(volume.read_file(remote + "/manifest.json")))
    prepared = plan.calls[f"{role}_{group}"]["receipt"]
    if (
        manifest["call_id"] != call["id"]
        or manifest["runtime_fingerprint"] != plan.config["runtime"]["training"]
        or any(manifest[k] != prepared[k] for k in ("artifact_identity", "input_sha256"))
        or manifest["expected"] != prepared["ready_decisions"]
    ):
        raise ValueError("Progress identity differs from the pinned provider call")
    if (
        manifest["completed"] != sum(chunk["rows"] for chunk in manifest["chunks"])
        or not 0 <= manifest["completed"] <= manifest["expected"]
    ):
        raise ValueError("Progress counters disagree with the retained chunks")
    source = directory(plan, role)
    with artifact_lock(source / "monitor"), closing(connect(source / "monitor.sqlite")) as db, db:
        initialise(db, source)
        for index, chunk in enumerate(manifest["chunks"]):
            receipt_id = f"{arm}:{call['id']}:{index}"
            previous = db.execute(
                "SELECT checksum FROM receipts WHERE id=?", (receipt_id,)
            ).fetchone()
            if previous:
                if previous["checksum"] != chunk["sha256"]:
                    raise ValueError("Previously collected progress changed")
                continue
            target = source / "progress" / arm / call["id"] / f"{index}.jsonl"
            target.parent.mkdir(parents=True, exist_ok=True)
            partial = target.with_suffix(".partial")
            with partial.open("wb") as output:
                for data in volume.read_file(remote + f"/{index}.jsonl"):
                    output.write(data)
            if (
                digest_file(partial) != chunk["sha256"]
                or sum(1 for _ in records(partial)) != chunk["rows"]
            ):
                raise ValueError("Progress chunk checksum or row count differs")
            partial.replace(target)
            ingest_file(db, plan, arm, target, receipt_id, chunk["sha256"])
        db.commit()
        write_summary(db, plan, role, source)
    call["progress"] = {k: v for k, v in manifest.items() if k != "chunks"}
    call.pop("progress_error", None)


def retain_partial(plan, stage):
    role, participant, preparing, _ = provider_stage(plan, stage)
    if preparing:
        return {"partial": True}
    source = directory(plan, role)
    arm = participant["key"]
    target = source / f"{arm}.jsonl"
    with artifact_lock(source / "monitor"), closing(connect(source / "monitor.sqlite")) as db, db:
        initialise(db, source)
        if participant["kind"] == "external":
            for record in plan.requests.filter(
                role=role, participant=arm, state="completed"
            ).iterator():
                for row in db.execute(
                    "SELECT key FROM samples WHERE hash=?", (record.input_sha256,)
                ).fetchall():
                    ingest(db, plan, arm, {**record.prediction, "key": row["key"]})
        with target.with_suffix(".partial").open("w") as output:
            for row in db.execute(
                "SELECT raw FROM predictions WHERE arm=? AND raw IS NOT NULL ORDER BY key", (arm,)
            ):
                output.write(row["raw"] + "\n")
        target.with_suffix(".partial").replace(target)
    return {"partial": True, "predictions_sha256": digest_file(target)}


def refresh(plan):
    for role in plan.config["suites"]:
        source = directory(plan, role)
        if not (source / "manifest.json").exists():
            continue
        with (
            artifact_lock(source / "monitor"),
            closing(connect(source / "monitor.sqlite")) as db,
            db,
        ):
            initialise(db, source)
            for participant in participants(plan):
                arm = participant["key"]
                call = plan.calls.get(f"{role}_{arm}", {})
                path = source / f"{arm}.jsonl"
                if call.get("state") == "completed" and path.exists():
                    provider = source / f"{arm}-provider.jsonl"
                    receipt = call.get("receipt") or {}
                    if (
                        provider.exists()
                        and receipt.get("predictions_sha256")
                        and not receipt.get("partial")
                    ):
                        ingest_file(
                            db,
                            plan,
                            arm,
                            provider,
                            arm + ":complete",
                            receipt["predictions_sha256"],
                        )
                    else:
                        checksum = (
                            plan.config.get("reuse", {})
                            .get(arm, {})
                            .get("stages", {})
                            .get(role, {})
                            .get("predictions_sha256")
                        )
                        if receipt.get("partial"):
                            checksum = receipt["predictions_sha256"]
                        if participant["kind"] != "external" and not checksum:
                            raise ValueError("Prediction receipt is unavailable")
                        ingest_file(
                            db, plan, arm, path, arm + ":complete", checksum or digest_file(path)
                        )
                elif participant["kind"] == "external":
                    for record in plan.requests.filter(
                        role=role, participant=arm, prediction__isnull=False
                    ).iterator():
                        for row in db.execute(
                            "SELECT key FROM samples WHERE hash=?", (record.input_sha256,)
                        ).fetchall():
                            ingest(db, plan, arm, {**record.prediction, "key": row["key"]})
                failure = path.with_suffix(".failures.jsonl")
                if failure.exists():
                    for item in records(failure):
                        db.execute(
                            "INSERT OR IGNORE INTO predictions(arm,key,error) VALUES(?,?,?)",
                            (
                                arm,
                                item["key"],
                                item.get("error")
                                or item.get("reason")
                                or "Input incompatible with this model",
                            ),
                        )
            for arm, policy in plan.calibration.get("arms", {}).items():
                after = ""
                while pending := db.execute(
                    "SELECT p.key,p.raw,s.input,s.reference FROM predictions p JOIN samples s USING(key) WHERE p.arm=? AND p.key>? AND p.error='' AND p.adjusted IS NULL ORDER BY p.key LIMIT 256",
                    (arm, after),
                ).fetchall():
                    for prediction in pending:
                        adjusted = calibrated(json.loads(prediction["raw"]), policy)
                        metrics = score_decision(
                            json.loads(prediction["input"]),
                            json.loads(prediction["reference"]),
                            adjusted,
                        )
                        db.execute(
                            "UPDATE predictions SET adjusted=?,adjusted_metrics=? WHERE arm=? AND key=?",
                            (json.dumps(adjusted), json.dumps(metrics), arm, prediction["key"]),
                        )
                    after = pending[-1]["key"]
            db.commit()
            write_summary(db, plan, role, source)


def write_summary(db, plan, role, source):
    arms = []
    suite_counts = db.execute(
        "SELECT benchmark,COUNT(*) expected,SUM(error!='') incompatible FROM samples GROUP BY benchmark"
    ).fetchall()
    fields = {"raw": "metrics", "calibrated": "adjusted_metrics"}
    columns = ",".join(
        f"{aggregate}(json_extract(p.{field},'$.{name}')) FILTER (WHERE p.error='') {mode}_{name}_{stat}"
        for mode, field in fields.items()
        for name in sorted(METRICS)
        for aggregate, stat in (("AVG", "mean"), ("COUNT", "count"))
    )
    for participant in participants(plan):
        arm = participant["key"]
        benchmarks = []
        recorded = {
            row["benchmark"]: dict(row)
            for row in db.execute(
                f"SELECT benchmark,COUNT(p.raw) received,SUM(p.error='') scored,SUM(p.error!='' AND p.raw IS NOT NULL) invalid,SUM(p.raw IS NULL) incompatible,{columns} FROM predictions p JOIN samples s USING(key) WHERE arm=? GROUP BY benchmark",
                (arm,),
            )
        }
        for benchmark in suite_counts:
            counts = recorded.get(benchmark["benchmark"], {})
            benchmarks.append(
                {
                    "name": benchmark["benchmark"],
                    "expected": benchmark["expected"],
                    "incompatible": benchmark["incompatible"] + counts.get("incompatible", 0),
                    "received": counts.get("received", 0),
                    "scored": counts.get("scored", 0),
                    "invalid": counts.get("invalid", 0),
                    **{
                        mode: {
                            name: {
                                "mean": counts[f"{mode}_{name}_mean"],
                                "decisions": counts[f"{mode}_{name}_count"],
                            }
                            for name in sorted(METRICS)
                            if counts.get(f"{mode}_{name}_count")
                        }
                        for mode in fields
                    },
                }
            )
        arms.append(
            {
                "key": arm,
                "name": participant["name"],
                "benchmarks": benchmarks,
                **{
                    name: sum(b[name] for b in benchmarks)
                    for name in ("expected", "received", "scored", "invalid", "incompatible")
                },
            }
        )
    write_label_metrics(db, plan)
    db.commit()
    atomic_json(
        source / "monitor.json",
        {
            "role": role,
            "updated_at": timezone.now().isoformat(),
            "state": "ready",
            "evaluation_state": plan.state,
            "error": "",
            "arms": arms,
            "label_metrics_ready": True,
        },
    )


def write_label_metrics(db, plan):
    db.execute("""CREATE TABLE IF NOT EXISTS label_metrics (
        benchmark TEXT, label_index INTEGER, label TEXT, expected INTEGER, models TEXT,
        PRIMARY KEY(benchmark, label_index)
    )""")
    db.execute("DELETE FROM label_metrics")
    # Label indexes are comparable only within a benchmark's fixed option order.
    for suite in db.execute("""SELECT benchmark, MIN(json_extract(input,'$.options')) options
        FROM samples WHERE error='' GROUP BY benchmark
        HAVING COUNT(DISTINCT json_extract(input,'$.options'))=1""").fetchall():
        benchmark = suite["benchmark"]
        expected = dict(
            db.execute(
                """SELECT target.key, COUNT(*) FROM samples s,
            json_each(s.reference, '$.probabilities') target
            WHERE s.benchmark=? AND s.error='' AND target.value=1 GROUP BY target.key""",
                (benchmark,),
            ).fetchall()
        )
        if not expected:
            continue
        counts = db.execute(
            """SELECT p.arm, target.key actual,
            json_extract(p.metrics,'$.predicted_option') predicted, COUNT(*) n
            FROM predictions p JOIN samples s USING(key),
            json_each(s.reference,'$.probabilities') target
            WHERE s.benchmark=? AND s.error='' AND p.error=''
            AND json_extract(p.metrics,'$.accuracy') IS NOT NULL AND target.value=1
            GROUP BY p.arm, actual, predicted""",
            (benchmark,),
        ).fetchall()
        totals = defaultdict(lambda: {"support": 0, "predicted": 0, "correct": 0})
        for count in counts:
            totals[count["arm"], count["actual"]]["support"] += count["n"]
            totals[count["arm"], count["predicted"]]["predicted"] += count["n"]
            if count["actual"] == count["predicted"]:
                totals[count["arm"], count["actual"]]["correct"] += count["n"]
        for index, label in enumerate(json.loads(suite["options"])):
            models = {}
            for participant in participants(plan):
                arm = participant["key"]
                support, predicted, correct = (
                    totals[arm, index][name] for name in ("support", "predicted", "correct")
                )
                models[arm] = {
                    "support": support,
                    "predicted": predicted,
                    "correct": correct,
                    "precision": correct / predicted if predicted else None,
                    "recall": correct / support if support else None,
                    "f1": 2 * correct / (support + predicted) if support + predicted else None,
                }
            db.execute(
                "INSERT INTO label_metrics VALUES(?,?,?,?,?)",
                (
                    benchmark,
                    index,
                    label if isinstance(label, str) else json.dumps(label),
                    expected.get(index, 0),
                    json.dumps(models),
                ),
            )


def inspect_labels(db, response, *, search, participant, ordering, offset, limit):
    field = ordering.removeprefix("-")
    direction = "DESC" if ordering.startswith("-") else "ASC"
    values = []
    if field in {"benchmark", "label", "expected"}:
        expression = field
    else:
        arm, metric = field.rsplit(":", 1)
        expression = "json_extract(models, ?)"
        values.append(f'$."{arm}".{metric}')
    where = "(instr(lower(label),lower(?))>0 OR instr(lower(benchmark),lower(?))>0)"
    response["count"] = db.execute(
        f"SELECT COUNT(*) FROM label_metrics WHERE {where}", (search, search)
    ).fetchone()[0]
    for row in db.execute(
        f"SELECT *, {expression} sort_value FROM label_metrics WHERE {where} "
        f"ORDER BY sort_value IS NULL, sort_value {direction}, benchmark, label_index LIMIT ? OFFSET ?",
        [*values, search, search, limit, offset],
    ):
        models = json.loads(row["models"])
        response["labels"].append(
            {
                "benchmark": row["benchmark"],
                "label": row["label"],
                "index": row["label_index"],
                "expected": row["expected"],
                "models": {participant: models[participant]} if participant else models,
            }
        )


def sample_filter(search, outcome, arms):
    clauses, values = [], []
    if search:
        clauses.append("""(s.key = ? OR instr(lower(COALESCE(s.benchmark,'')), lower(?)) > 0
            OR EXISTS (SELECT 1 FROM json_tree(s.input)
                WHERE type='text' AND instr(lower(atom), lower(?)) > 0))""")
        values.extend([search, search, search])
    if outcome != "all":
        scoped = f"p.key=s.key AND p.arm IN ({','.join('?' for _ in arms)})"
        if outcome == "missing":
            clauses.append(
                f"s.error='' AND (SELECT COUNT(*) FROM predictions p WHERE {scoped} "
                "AND (p.raw IS NOT NULL OR p.error!='')) < ?"
            )
            values.extend([*arms, len(arms)])
        elif outcome == "unscored":
            clauses.append(
                f"(s.error!='' OR EXISTS (SELECT 1 FROM predictions p WHERE {scoped} AND p.error!=''))"
            )
            values.extend(arms)
        else:
            clauses.append(
                f"s.error='' AND EXISTS (SELECT 1 FROM predictions p WHERE {scoped} "
                "AND p.error='' AND json_extract(p.metrics, '$.accuracy')=?)"
            )
            values.extend([*arms, int(outcome == "correct")])
    return " AND ".join(clauses) or "1", values


def inspect(
    plan,
    *,
    role="final",
    offset=0,
    limit=25,
    key=None,
    search="",
    participant=None,
    outcome="all",
    view="samples",
    ordering="label",
):
    if role not in plan.config["suites"]:
        raise InputValidationError("Select a frozen calibration or final suite")
    arms = [p["key"] for p in participants(plan)]
    if view not in {"samples", "labels"}:
        raise InputValidationError("Select samples or labels")
    if view == "labels" and (key is not None or outcome != "all"):
        raise InputValidationError("Label metrics cannot be combined with sample keys or outcomes")
    if ordering.removeprefix("-") not in {"label", "benchmark", "expected"} | {
        f"{arm}:{metric}" for arm in arms for metric in ("precision", "recall", "f1")
    }:
        raise InputValidationError(
            "Select a label column or a participant's precision, recall or f1"
        )
    if participant is not None and participant not in arms:
        raise InputValidationError("Select a participant in this evaluation")
    if outcome not in {"all", "correct", "incorrect", "missing", "unscored"}:
        raise InputValidationError("Select a supported sample outcome")
    if key is not None and (search or participant is not None or outcome != "all"):
        raise InputValidationError("Select an exact sample key or sample filters, not both")
    source = directory(plan, role)
    path = source / "monitor.json"
    summary = (
        json.loads(path.read_text())
        if path.exists()
        else {"role": role, "state": "pending", "updated_at": None, "arms": [], "error": ""}
    )
    if (
        not path.exists()
        or (view == "labels" and not summary.get("label_metrics_ready"))
        or (plan.state == "completed" and summary.get("evaluation_state") != "completed")
    ) and plan.calls.get("verify_inputs", {}).get("state") == "completed":
        request_refresh(plan)
    calls = []
    for candidate in participants(plan):
        stage = f"{role}_{candidate['key']}"
        call = plan.calls.get(stage, {})
        _, _, _, group = provider_stage(plan, stage)
        preparation = plan.calls.get(f"{role}_{group}", {}) if group else {}
        state = call.get("state") or preparation.get("state") or "pending"
        progress = call.get("progress", {})
        phase = progress.get("stage") or (
            "predicting" if call.get("id") else "preparing_inputs" if preparation else "waiting"
        )
        if state == "completed":
            phase = "completed" if call else "waiting_for_worker"
        elif state == "retrying":
            phase = "recovering"
        calls.append(
            {
                "key": candidate["key"],
                "name": candidate["name"],
                "state": state
                if call
                else "pending"
                if preparation.get("state") == "completed"
                else state,
                "stage": phase,
                "progress": progress,
                "observed_at": call.get("observed_at"),
                "error": call.get("error") or preparation.get("error", ""),
                "progress_error": call.get("progress_error", ""),
            }
        )
    response = {
        **summary,
        "evaluation": str(plan.pk),
        "evaluation_state": plan.state,
        "provisional": plan.state != "completed",
        "calibration_fitted": bool(plan.calibration),
        "calls": calls,
        "count": 0,
        "offset": offset,
        "limit": limit,
        "samples": [],
        "labels": [],
        "view": view,
    }
    dbpath = source / "monitor.sqlite"
    if not path.exists() or not dbpath.exists():
        return response
    with closing(connect(dbpath, readonly=True)) as db:
        db.execute("BEGIN")
        if view == "labels":
            if not summary.get("label_metrics_ready"):
                response["state"] = "pending"
                return response
            inspect_labels(
                db,
                response,
                search=search.strip(),
                participant=participant,
                ordering=ordering,
                offset=offset,
                limit=limit,
            )
            return response
        where, values = sample_filter(
            search.strip(), outcome, [participant] if participant else arms
        )
        response["count"] = db.execute(
            f"SELECT COUNT(*) FROM samples s WHERE {where}", values
        ).fetchone()[0]
        selected = (
            db.execute("SELECT * FROM samples WHERE key=?", (key,))
            if key is not None
            else db.execute(
                f"SELECT s.* FROM samples s WHERE {where} ORDER BY s.ordinal LIMIT ? OFFSET ?",
                [*values, limit, offset],
            )
        )
        for row in selected.fetchall():
            predictions = {
                p["arm"]: {
                    "raw": json.loads(p["raw"]) if p["raw"] else None,
                    "calibrated": json.loads(p["adjusted"]) if p["adjusted"] else None,
                    "metrics": json.loads(p["metrics"]) if p["metrics"] else {},
                    "calibrated_metrics": json.loads(p["adjusted_metrics"])
                    if p["adjusted_metrics"]
                    else {},
                    "error": p["error"],
                }
                for p in db.execute("SELECT * FROM predictions WHERE key=?", (row["key"],))
            }
            response["samples"].append(
                {
                    "key": row["key"],
                    "index": row["ordinal"],
                    "benchmark": row["benchmark"],
                    "input": json.loads(row["input"]) if row["input"] else None,
                    "reference": json.loads(row["reference"]) if row["reference"] else None,
                    "error": row["error"],
                    "predictions": predictions,
                }
            )
    return response


def refresh_safely(plan):
    try:
        refresh(plan)
    except Exception:
        logger.exception("Native evaluation monitoring projection failed: %s", plan.pk)
        for role in plan.config["suites"]:
            path = directory(plan, role) / "monitor.json"
            if path.parent.exists():
                previous = (
                    json.loads(path.read_text())
                    if path.exists()
                    else {"role": role, "updated_at": None, "arms": []}
                )
                atomic_json(
                    path,
                    {
                        **previous,
                        "state": "unavailable",
                        "error": "Sample indexing could not be verified. Retained evaluation results are unchanged.",
                    },
                )


def request_refresh(plan):
    # The task imports this service; indexing is local and never invokes inference.
    from overbae.tasks.native_evaluation import refresh_monitor

    if cache.add(f"native-monitor:{plan.pk}", True, timeout=60):
        refresh_monitor.delay(str(plan.pk))
