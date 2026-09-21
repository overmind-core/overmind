import hashlib
import json
import subprocess
import sys
from pathlib import Path


def processor_fingerprint(assets: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(p for p in assets.rglob("*") if p.suffix in {".py", ".jinja"}):
        digest.update(str(path.relative_to(assets)).encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def preparation_failure(code: str) -> dict:
    messages = {
        "worker_out_of_date": (
            "The training preprocessing worker is out of date. "
            "Deploy the current SFT worker and retry training."
        ),
        "process_failed": (
            "Training preprocessing failed before compatibility could be checked. "
            "Inspect the preprocessing worker logs and retry."
        ),
        "invalid_report": (
            "Training preprocessing returned an invalid report. "
            "Inspect the preprocessing worker logs and retry."
        ),
    }
    if code not in messages:
        code = "process_failed"
    return {"ready": False, "error_code": code, "error": messages[code], "retryable": True}


def validate_preparation_report(report) -> dict:
    if not isinstance(report, dict) or type(report.get("ready")) is not bool:
        return preparation_failure("invalid_report")
    if report.get("error_code") or report.get("error"):
        # Only fixed, user-facing diagnostics leave the worker through the report.
        return preparation_failure(str(report.get("error_code", "process_failed")))
    return report


def run_preparation_process(assets: Path, request_path: Path, destination: Path) -> dict:
    destination.mkdir(parents=True, exist_ok=True)
    report_path = destination / "report.json"
    # A retry must not accept the previous attempt's report after a subprocess failure.
    report_path.unlink(missing_ok=True)
    with (
        (destination / "preprocess_stdout.log").open("a", encoding="utf-8") as log,
        subprocess.Popen(  # noqa: S603
            [
                sys.executable,
                "-u",
                str(assets / "prepare_training.py"),
                str(request_path),
                str(destination),
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            errors="replace",
        ) as process,
    ):
        assert process.stdout is not None
        for line in process.stdout:
            print(line, end="", flush=True)
            log.write(line)
            log.flush()
        process.wait()
    if process.returncode:
        report = preparation_failure("process_failed")
    else:
        try:
            report = validate_preparation_report(json.loads(report_path.read_text()))
        except (OSError, ValueError):
            report = preparation_failure("invalid_report")
    report_path.write_text(json.dumps(report))
    return report
