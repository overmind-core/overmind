import json
import os
import resource
import sys
import traceback
from pathlib import Path

MANIFEST = [
    {"name": "text", "type": "string"},
    {"name": "page", "type": "integer"},
    {"name": "source_name", "type": "string"},
    {"name": "_overmind_document_id", "type": "string"},
    {"name": "_overmind_provenance", "type": "json"},
]


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
    from sandbox import tables
    from sandbox.documents.extract import DocumentError, extract

    try:
        rows, metadata = extract(Path(document), filename)
    except DocumentError as exc:
        sys.stderr.write(str(exc))
        raise SystemExit(3) from None
    except Exception:  # noqa: BLE001 — the caller sees one sentence; the trace goes to stdout
        traceback.print_exc(file=sys.stdout)
        sys.stderr.write("The document could not be read.")
        raise SystemExit(3) from None
    with tables.TableWriter(output, MANIFEST) as writer:
        writer.write_rows(rows)
    Path(extraction).write_text(json.dumps(metadata), encoding="utf-8")


if __name__ == "__main__":
    main(sys.argv[1:])
