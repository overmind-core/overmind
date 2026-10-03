import logging
import re


class RedactChatGPTCallback(logging.Filter):
    def filter(self, record):
        path = "/api/chatgpt/callback/"
        message = record.getMessage()
        if path in message:
            record.msg = re.sub(r"(/api/chatgpt/callback/)\?[^\s\"']+", r"\1?[redacted]", message)
            record.args = ()
        request = getattr(record, "request", None)
        if getattr(request, "path", None) == path:
            record.request = f"{request.method} {path} [query redacted]"
        return True
