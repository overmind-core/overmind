import sys

from common import read, write

write(
    (
        {
            **row,
            "needs_review": not bool(row["text"].strip()),
            "document_group": row.get("_overmind_document_id") or row.get("source_name"),
        }
        for row in read(sys.argv[1])
    ),
    sys.argv[2],
)
