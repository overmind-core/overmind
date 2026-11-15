from django.middleware.gzip import GZipMiddleware


class CompressionMiddleware(GZipMiddleware):
    """Gzip for API responses, except event streams: gzip holds bytes back until a block
    fills, and a stream's event must reach the page when it is sent."""

    def process_response(self, request, response):
        if response.get("Content-Type", "").startswith("text/event-stream"):
            return response
        return super().process_response(request, response)
