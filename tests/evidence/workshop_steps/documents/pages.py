import collections
import sys

from common import read, write


def assemble(records):
    pages = collections.defaultdict(list)
    for row in records:
        pages[(row["document_group"], row["page"])].append(row)
    for (document, page), members in sorted(pages.items()):
        members.sort(key=lambda row: row["source_row"])
        yield {
            "document_group": document,
            "page": page,
            "text": "\n".join(row["text"] for row in members),
            "_overmind_parent_rows": [row["source_row"] for row in members],
            "evidence_rows": len(members),
        }


write(assemble(read(sys.argv[1])), sys.argv[2])
