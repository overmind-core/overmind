import json
import math
import os
import time
from pathlib import Path

from modal_shared.training_monitoring import (
    MonitoringSchedule,
    classification_metrics,
    contract_metrics,
    fingerprint,
    freeze_plan,
    paired_generation_metrics,
    row_identity,
)
from modal_shared.training_telemetry import record_stage


def atomic_json(path, value):
    path = Path(path)
    temporary = path.with_suffix(".pending")
    with temporary.open("w") as stream:
        json.dump(value, stream, allow_nan=False)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def freeze_run_files(run_dir, policy):
    root = Path(run_dir)
    rows = {}
    for name in ("data", "val"):
        path = root / f"{name}.jsonl"
        with path.open() if path.exists() else open(os.devnull) as stream:
            rows[name] = [row_identity(json.loads(line)) for line in stream]
    plan = freeze_plan(policy, rows["data"], rows["val"])
    atomic_json(root / "monitoring-plan.json", plan)
    return plan


class Monitor:
    def __init__(
        self, run_dir, policy, train, development, *, total_steps, attempt, identity_rows=False
    ):
        self.root = Path(run_dir)
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / "monitoring.json"
        self.policy, self.attempt = policy, attempt
        plan = freeze_plan(
            policy,
            train if identity_rows else [row_identity(row) for row in train],
            development if identity_rows else [row_identity(row) for row in development],
        )
        self.probes, identity = plan["probes"], plan["identity"]
        frozen = self.root / "monitoring-plan.json"
        if frozen.exists() and json.loads(frozen.read_text()) != plan:
            raise ValueError("The pre-dispatch frozen monitoring policy or samples changed")
        self.data = {
            "identity": identity,
            "policy": policy,
            "policy_fingerprint": fingerprint(policy),
            "probes": self.probes,
            "checks": [],
            "checkpoints": [],
            "schedule_decisions": [],
            "selected_checkpoint": None,
            "monitoring_seconds": 0.0,
            "optimizer_seconds": 0.0,
            "stop_reason": None,
        }
        if self.path.exists():
            self.data = json.loads(self.path.read_text())
            if self.data["identity"] != identity:
                raise ValueError("The frozen training monitoring policy or sample changed")
            for check in self.data["checks"]:
                if check["state"] == "running":
                    check.update(
                        state="interrupted",
                        error={
                            "code": "worker_restarted",
                            "message": "Worker restarted during validation",
                        },
                    )
        self.schedule = MonitoringSchedule(policy, total_steps, state=self.data.get("schedule"))
        self.publish()

    def publish(self):
        self.data["schedule"] = self.schedule.state()
        atomic_json(self.path, self.data)
        if os.environ.get("TRAINING_MONITOR_LOGS") == "1":
            receipt = {key: value for key, value in self.data.items() if key != "probes"}
            print("BT_MONITORING " + json.dumps(receipt, allow_nan=False), flush=True)

    def summary(self):
        return self.data

    def observe_step(self, seconds):
        self.schedule.observe_step(seconds)
        self.data["optimizer_seconds"] += seconds

    def observe_metrics(self, logs, *, clipping_threshold, loss_filtering=False):
        health = self.data.setdefault(
            "numerical_health",
            {
                "logged_updates": 0,
                "nonfinite_loss_observations": 0,
                "nonfinite_gradient_observations": 0,
                "clipping_threshold_exceeded_observations": 0,
                "basis": "logged training intervals, not every optimizer update",
                "skipped_updates": None,
                "skipped_updates_reason": "not exposed by this trainer callback",
            },
        )
        health["logged_updates"] += 1
        health["loss_filtering_enabled"] = loss_filtering
        loss, gradient = float(logs["loss"]), logs.get("grad_norm")
        health["nonfinite_loss_observations"] += int(not math.isfinite(loss))
        if gradient is not None:
            gradient = float(gradient)
            health["nonfinite_gradient_observations"] += int(not math.isfinite(gradient))
            if math.isfinite(gradient) and clipping_threshold is not None:
                health["clipping_threshold_exceeded_observations"] += int(
                    gradient > clipping_threshold
                )
        health["gradient_norm_available"] = gradient is not None
        self.publish()

    def check(self, step, *, evaluate, generate=None, save_checkpoint=None, final=False):
        stream = "final_development" if final else "development"
        key = f"{self.attempt}:{stream}:{step}"
        existing = next((check for check in self.data["checks"] if check["key"] == key), None)
        if existing is not None:
            return existing
        started = time.monotonic()
        probe = self.probes["development"]
        indices = list(range(probe["population_rows"])) if final else probe["indices"]
        sample_fingerprint = (
            fingerprint([self.data["identity"], "full_development"])
            if final
            else probe["fingerprint"]
        )
        check = {
            "key": key,
            "attempt": self.attempt,
            "stream": stream,
            "step": step,
            "state": "running",
            "started_at": time.time(),
            "observed_at": time.time(),
            "policy_fingerprint": self.data["policy_fingerprint"],
            "sample_fingerprint": sample_fingerprint,
            "metrics": {},
            "coverage": {"expected": len(indices), "scored": 0},
            "facts": {"measurement": "teacher_forced", "full_development": final},
        }
        self.data["checks"].append(check)
        self.publish()
        record_stage(
            self.root,
            "final_validation" if final else "validation",
            completed=0,
            total=len(indices),
            unit="rows",
        )
        try:
            metrics = evaluate(indices, "development")
            examples = metrics.pop("_evidence_examples", None)
            if examples is not None:
                if len(examples) != len(indices):
                    raise ValueError("Development evidence count does not match the frozen sample")
                artifact = {"sha256": fingerprint(examples), "examples": examples}
                evidence_name = f"monitoring-{self.attempt}-{stream}-{step}.json"
                atomic_json(self.root / evidence_name, artifact)
                check["artifact"] = {"sha256": artifact["sha256"], "path": evidence_name}
                check["facts"]["evidence_kind"] = "native_probabilities"
            if "_evaluation_batches" in metrics:
                check["facts"]["evaluation_batches"] = metrics.pop("_evaluation_batches")
            loss = metrics.get("eval_loss")
            if type(loss) not in {int, float} or not math.isfinite(loss):
                raise ValueError("Development loss must be finite")
            check["metrics"] = {
                key: value
                for key, value in metrics.items()
                if value is None or type(value) not in {int, float} or math.isfinite(value)
            }
            check["coverage"]["scored"] = len(indices)
            check["observed_at"] = time.time()
            self.publish()
            train_indices = self.probes["training_reference"]["indices"]
            if train_indices and not final:
                train_metrics = evaluate(train_indices, "training_reference")
                if not math.isfinite(train_metrics["eval_loss"]):
                    raise ValueError("Training reference loss must be finite")
                check["metrics"]["training_reference_loss"] = train_metrics["eval_loss"]
                check["metrics"]["development_gap"] = loss - train_metrics["eval_loss"]
                check["facts"]["gap_basis"] = "fixed samples; evaluation mode; teacher-forced loss"
                check["observed_at"] = time.time()
                self.publish()
            generation_due = (
                final
                or step == 0
                or (self.schedule.checks + 1) % self.policy["generation_every"] == 0
            )
            if self.policy["generation"] and generation_due:
                if generate is None:
                    raise ValueError("Generation probe is configured but unavailable")
                examples = generate(self.probes["generation"]["indices"])
                artifact = {"sha256": fingerprint(examples), "examples": examples}
                evidence_name = f"monitoring-{self.attempt}-{stream}-{step}.json"
                atomic_json(self.root / evidence_name, artifact)
                check["artifact"] = {"sha256": artifact["sha256"], "path": evidence_name}
                generation_policy = self.policy["generation"]
                if generation_policy["kind"] == "classification":
                    generation_metrics = classification_metrics(
                        examples, generation_policy["labels"]
                    )
                else:
                    generation_metrics = contract_metrics(examples, generation_policy)
                check["metrics"]["generation"] = generation_metrics
                check["facts"]["generation_sample_fingerprint"] = self.probes["generation"][
                    "fingerprint"
                ]
                previous = next(
                    (
                        item
                        for item in reversed(self.data["checks"][:-1])
                        if item["state"] == "completed"
                        and item.get("artifact")
                        and item["facts"].get("generation_sample_fingerprint")
                        == self.probes["generation"]["fingerprint"]
                    ),
                    None,
                )
                if previous:
                    stored = json.loads((self.root / previous["artifact"]["path"]).read_text())
                    if fingerprint(stored["examples"]) != previous["artifact"]["sha256"]:
                        raise ValueError("Previous generation evidence checksum mismatch")
                    check["metrics"]["paired_generation"] = {
                        **paired_generation_metrics(
                            stored["examples"],
                            examples,
                            seed=self.policy["seed"],
                            labels=generation_policy.get("labels"),
                        ),
                        "baseline_check": previous["key"],
                        "candidate_check": key,
                    }
                check["observed_at"] = time.time()
                self.publish()
            if step > 0 and not final and save_checkpoint is not None:
                checkpoint = save_checkpoint(step, check["metrics"])
                checkpoint.update(attempt=self.attempt, step=step, key=f"{self.attempt}:{step}")
                self.data["checkpoints"].append(checkpoint)
                check["facts"]["checkpoint_identity"] = checkpoint["identity"]
                self.select()
            check["state"] = "completed"
            self.early_stop()
        except Exception as exc:
            check.update(state="failed", error={"code": type(exc).__name__, "message": str(exc)})
            if self.policy["failure_policy"] == "stop":
                raise
        finally:
            duration = time.monotonic() - started
            check["observed_at"] = time.time()
            check["facts"]["duration_seconds"] = duration
            check["facts"]["findings"] = self.findings(check)
            self.data["monitoring_seconds"] += duration
            if step > 0 and not final:
                self.data["schedule_decisions"].append(
                    self.schedule.completed(step=step, duration=duration)
                )
            self.publish()
            record_stage(
                self.root, "training", completed=step, total=self.schedule.total_steps, unit="steps"
            )
        return check

    def findings(self, check):
        findings = []
        if check["state"] == "failed":
            findings.append(
                {"code": "monitoring_check_failed", "message": check["error"]["message"]}
            )
        generation = check["metrics"].get("generation") or {}
        if generation.get("technical_errors") or generation.get("unscorable"):
            findings.append(
                {
                    "code": "incomplete_generation_coverage",
                    "message": "Some expected examples could not be scored",
                }
            )
        if generation.get("invalid_labels"):
            findings.append(
                {
                    "code": "invalid_generated_labels",
                    "message": "Generated outputs include undeclared labels",
                }
            )
        if generation.get("unrepresented_labels"):
            findings.append(
                {
                    "code": "unrepresented_labels",
                    "message": "The generation sample does not represent every declared label",
                }
            )
        checks = [
            item
            for item in self.data["checks"]
            if item["state"] == "completed"
            and item["stream"] == "development"
            and item["sample_fingerprint"] == check["sample_fingerprint"]
        ]
        if len(checks) >= 4 and all(
            a["metrics"]["eval_loss"] < b["metrics"]["eval_loss"]
            for a, b in zip(checks[-4:-1], checks[-3:], strict=True)
        ):
            findings.append(
                {
                    "code": "development_loss_increased",
                    "message": "Development loss increased at three consecutive checks",
                }
            )
        return [
            {
                **item,
                "rule_version": 1,
                "check": check["key"],
                "observed_at": check["observed_at"],
                "scope": "development",
                "action": "none",
            }
            for item in findings
        ]

    def select(self):
        candidates = [
            item
            for item in self.data["checkpoints"]
            if item["state"] == "available"
            and item.get("verification", {}).get("reload_verified") is True
        ]
        if not candidates:
            return
        selected = (
            min(candidates, key=lambda item: (item["metrics"]["eval_loss"], item["step"]))
            if self.policy["selection"] == "development_loss"
            else max(candidates, key=lambda item: item["step"])
        )
        self.data["selected_checkpoint"] = selected["key"]
        for item in self.data["checkpoints"]:
            item["selected"] = item["key"] == selected["key"]

    def early_stop(self):
        rule = self.policy["early_stopping"]
        if not rule:
            return
        checks = [
            check
            for check in self.data["checks"]
            if check["state"] == "completed"
            and check["stream"] == "development"
            and check["step"] > 0
        ]
        if len(checks) <= rule["warmup_checks"]:
            return
        best, stale = math.inf, 0
        for check in checks:
            loss = check["metrics"]["eval_loss"]
            if loss < best - rule["min_delta"]:
                best, stale = loss, 0
            else:
                stale += 1
        if stale >= rule["patience"]:
            self.data["stop_reason"] = {
                "code": "development_loss_patience",
                "patience": rule["patience"],
                "step": checks[-1]["step"],
                "best_loss": best,
            }
