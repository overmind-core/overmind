import json
import sys

from common import digest, read, write


def audit(records):
    for row in records:
        messages = row["messages"]
        if isinstance(messages, str):
            messages = json.loads(messages)
        if not isinstance(messages, list):
            raise ValueError("messages must be an array")
        yield {
            **row,
            "needs_review": not any(message.get("role") == "assistant" for message in messages),
            "conversation_group": digest(messages),
            "message_count": len(messages),
        }


write(audit(read(sys.argv[1])), sys.argv[2])
