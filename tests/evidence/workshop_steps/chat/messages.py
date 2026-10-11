import json
import sys

from common import read, write


def normalize(records):
    for row in records:
        messages = row["messages"]
        if isinstance(messages, str):
            messages = json.loads(messages)
        if not isinstance(messages, list):
            raise ValueError("messages must be an array")
        yield {**row, "messages": messages}


write(normalize(sorted(read(sys.argv[1]), key=lambda row: row["source_row"])), sys.argv[2])
