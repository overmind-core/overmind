import argparse
import hashlib
import json
import tempfile
import uuid
from pathlib import Path

import modal

from modal_shared.serving.artifacts import atomic_json
from modal_shared.training_release import identity


def launch(args):
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=False)
    env = json.loads(Path(args.config).read_text())
    if env.get("TRAINING_OBJECTIVE") != "decision_cross_entropy":
        raise ValueError("Recovery qualification requires native decision training")
    release = identity(Path(__file__).resolve().parents[1])
    observed = modal.Function.from_name(
        release["app"], "release_identity", environment_name=args.environment
    ).remote()
    if observed != release:
        raise ValueError("The deployed worker does not match this qualification checkout")
    volume = modal.Volume.from_name("overmind-sft", environment_name=args.environment)
    upload = modal.Function.from_name(
        release["app"], "upload_dataset", environment_name=args.environment
    )
    train = modal.Function.from_name(
        release["app"], "sft_" + env["UNSLOTH_IMAGE"], environment_name=args.environment
    )
    receipt = {
        "runtime": {**release, "environment": args.environment},
        "source_run": args.source_run,
        "preparation": args.preparation,
        "environment": args.environment,
        "config": env,
        "gpu": args.gpu,
        "inputs": {},
        "calls": {},
    }
    with tempfile.TemporaryDirectory(prefix="decision-recovery-") as temporary:
        files = []
        selections = {}
        report = json.loads(
            b"".join(volume.read_file(f"preparations/{args.preparation}/report.json"))
        )
        if not report.get("ready") or not report.get("artifact_sha256"):
            raise ValueError("Recovery qualification requires a ready preparation artifact")
        receipt["artifact_sha256"] = report["artifact_sha256"]
        for name in ("selected-data.keys", "selected-val.keys"):
            destination = Path(temporary) / name
            digest = hashlib.sha256()
            with destination.open("wb") as stream:
                for chunk in volume.read_file(f"runs/{args.source_run}/{name}"):
                    stream.write(chunk)
                    digest.update(chunk)
            receipt["inputs"][name] = digest.hexdigest()
            if destination.stat().st_size % 32:
                raise ValueError("The source run has a truncated row selection")
            selections[name.removeprefix("selected-").removesuffix(".keys")] = {
                "rows": destination.stat().st_size // 32,
                "sha256": digest.hexdigest(),
            }
            files.append(destination)
        for mode, timeout in (("control", 86400), ("interrupted", args.timeout)):
            run_id = f"decision-recovery-{uuid.uuid4().hex}-{mode}"
            with volume.batch_upload() as batch:
                for path in files:
                    batch.put_file(path, f"/runs/{run_id}/{path.name}")
            upload.remote(
                run_id=run_id,
                preparation_id=args.preparation,
                artifact_sha256=report["artifact_sha256"],
                selections=selections,
            )
            call = train.with_options(
                gpu=args.gpu,
                timeout=timeout,
                retries=modal.Retries(max_retries=10, initial_delay=0),
            ).spawn(run_id=run_id, env=env, gpu_type=args.gpu, gpu_count=1)
            receipt["calls"][mode] = {
                "run_id": run_id,
                "call_id": call.object_id,
                "timeout": timeout,
            }
            atomic_json(output / "launch.json", receipt)
            print(json.dumps({mode: receipt["calls"][mode]}), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-run", required=True)
    parser.add_argument("--preparation", required=True)
    parser.add_argument("--config", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--environment", required=True)
    parser.add_argument("--gpu", default="H100")
    parser.add_argument("--timeout", type=int, default=540)
    launch(parser.parse_args())
