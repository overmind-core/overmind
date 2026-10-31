"""robots.txt for the API host: crawlers stay out, except for /.well-known/.

MCP OAuth discovery and app-verification fetches live under /.well-known/.
"""

from django.http import HttpResponse
from django.views.decorators.http import require_safe

# Allow comes first so first-match and longest-match parsers read the file the same way.
ROBOTS_TXT = "User-agent: *\nAllow: /.well-known/\nDisallow: /\n"


@require_safe
def robots_txt(request):
    """Plain Django on purpose: DRF's ``@api_view(["GET"])`` answers HEAD with 405 and
    ``Accept: text/plain`` with 406, and neither is acceptable for a crawler fetch."""
    return HttpResponse(ROBOTS_TXT, content_type="text/plain; charset=utf-8")
