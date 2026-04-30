import os
import sys

DEFAULT_BASE_URL = "https://api.overmindlab.ai"


def get_api_settings(
    overmind_api_key: str | None = None,
    base_url: str | None = None,
) -> tuple[str, str]:
    overmind_api_key = overmind_api_key or os.getenv("OVERMIND_API_KEY")
    base_url = base_url or os.getenv("OVERMIND_API_URL") or DEFAULT_BASE_URL

    if not overmind_api_key:
        print("OVERMIND_API_KEY is not set")
        sys.exit(1)

    return overmind_api_key, base_url.rstrip("/")
