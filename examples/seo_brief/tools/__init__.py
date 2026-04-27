from .exa_serp import exa_search_serp
from .jina_fetch import jina_fetch_url

TOOL_FNS = {
    "exa_search_serp": exa_search_serp,
    "jina_fetch_url": jina_fetch_url,
}


TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "exa_search_serp",
            "description": "Search the web for a query.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "num_results": {"type": "integer", "default": 5},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "jina_fetch_url",
            "description": "Fetch a URL and return the readable text.",
            "parameters": {
                "type": "object",
                "properties": {"url": {"type": "string"}},
                "required": ["url"],
            },
        },
    },
]

__all__ = ["TOOL_FNS", "TOOL_SCHEMAS"]
