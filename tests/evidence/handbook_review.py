import sys

from common import read, write


def inspect(records):
    for row in records:
        yield {
            **row,
            "needs_review": row["_overmind_provenance"]["extraction"]["method"] == "tesseract-ocr",
            "document_group": row["_overmind_document_id"],
        }


write(inspect(read(sys.argv[1])), sys.argv[2])
