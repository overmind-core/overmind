import json
from pathlib import Path

from modal_shared.training_monitoring import fingerprint
from modal_shared.training_monitoring_runtime import Monitor


class RowSelection:
    def __init__(self, source, indices):
        self.source, self.indices = source, indices

    def __len__(self):
        return len(self.indices)

    def read(self, indices):
        return self.source.read([self.indices[index] for index in indices])


class NativeTrainingMonitor:
    def __init__(
        self, root, policy, train, development, *, total_steps, evaluate, retain, preserve, attempt
    ):
        self.root, self.train, self.development = Path(root), train, development
        self.evaluate_rows, self.retain, self.preserve = evaluate, retain, preserve
        self.monitor = Monitor(
            root,
            policy,
            train.monitoring_rows,
            development.monitoring_rows if development else [],
            total_steps=total_steps,
            attempt=attempt,
            identity_rows=True,
        )
        self.step, self.final = 0, False

    def evaluate(self, indices, split):
        source = self.development if split == "development" else self.train
        filename = (
            "decision-after.jsonl"
            if self.final and split == "development"
            else (
                "decision-before.jsonl"
                if self.step == 0 and split == "development"
                else f"native-monitor-{self.monitor.attempt}-{self.step}-{split}.jsonl"
            )
        )
        result = self.evaluate_rows(RowSelection(source, indices), self.root / filename)
        if split == "development":
            with (self.root / filename).open() as stream:
                records = [json.loads(line) for line in stream]
            result["_evidence_examples"] = [
                {"status": "completed", "index": index, **record}
                for index, record in zip(indices, records, strict=True)
            ]
        return result

    def save_checkpoint(self, step, metrics):
        result = self.retain(step)
        manifest = {"path": result["path"], "native_artifact_identity": result["artifact_identity"]}
        return {
            "identity": fingerprint(manifest),
            "manifest": manifest,
            "state": "available",
            "metrics": metrics,
            "verification": {
                **result["reload_verification"],
                "reload_verified": True,
                "method": "native_probability_replay",
                "resume_supported": False,
                "scope": "retained inference artifact; optimizer recovery is a separate latest-state artifact",
            },
        }

    def check(self, step, *, final=False):
        self.step, self.final = step, final
        with self.preserve():
            return self.monitor.check(
                step,
                evaluate=self.evaluate,
                save_checkpoint=self.save_checkpoint if self.retain else None,
                final=final,
            )

    def retain_at_step(self, step):
        key = f"{self.monitor.attempt}:{step}"
        if any(item["key"] == key for item in self.monitor.data["checkpoints"]):
            return
        with self.preserve():
            checkpoint = self.save_checkpoint(step, {})
        checkpoint.update(key=key, attempt=self.monitor.attempt, step=step)
        self.monitor.data["checkpoints"].append(checkpoint)
        self.monitor.select()
        self.monitor.publish()
