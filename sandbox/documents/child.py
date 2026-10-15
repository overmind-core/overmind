import json
import os
import resource
import sys
import traceback
from pathlib import Path


def main(argv: list[str]) -> None:
    limits, document, output, extraction = argv
    filename = os.environ["SANDBOX_FILENAME"]
    cpu_seconds, file_bytes, processes, descriptors = json.loads(limits)
    resource.setrlimit(resource.RLIMIT_CPU, (cpu_seconds, cpu_seconds))
    resource.setrlimit(resource.RLIMIT_FSIZE, (file_bytes, file_bytes))
    resource.setrlimit(resource.RLIMIT_NOFILE, (descriptors, descriptors))
    if processes:
        resource.setrlimit(resource.RLIMIT_NPROC, (processes, processes))
    # -I keeps the working directory off sys.path; import the code next to this file.
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from sandbox.documents.extract import DocumentError, extract

    def progress(value):
        with open("progress.jsonl", "a", encoding="utf-8") as report:
            report.write(json.dumps(value) + "\n")

    try:
        rows, metadata = extract(Path(document), filename=filename, on_progress=progress)
    except DocumentError as exc:
        sys.stderr.write(str(exc))
        raise SystemExit(3) from None
    except Exception:  # noqa: BLE001 — the caller sees one sentence; the trace goes to stdout
        traceback.print_exc(file=sys.stdout)
        sys.stderr.write("The document could not be read.")
        raise SystemExit(3) from None
    Path(output).write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
    Path(extraction).write_text(json.dumps(metadata), encoding="utf-8")


if __name__ == "__main__":
    main(sys.argv[1:])
