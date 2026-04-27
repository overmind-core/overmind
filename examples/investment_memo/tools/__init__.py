from .exa_news import exa_search_news
from .jina_fetch import jina_fetch_url
from .sec_edgar import sec_edgar_latest_10k, sec_edgar_latest_10q

TOOL_FNS = {
    "exa_search_news": exa_search_news,
    "jina_fetch_url": jina_fetch_url,
    "sec_edgar_latest_10k": sec_edgar_latest_10k,
    "sec_edgar_latest_10q": sec_edgar_latest_10q,
}


TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "exa_search_news",
            "description": "Search news and web for a query.",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string"}},
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
    {
        "type": "function",
        "function": {
            "name": "sec_edgar_latest_10k",
            "description": "Get the latest 10-K filing summary for a ticker.",
            "parameters": {
                "type": "object",
                "properties": {"ticker": {"type": "string"}},
                "required": ["ticker"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "sec_edgar_latest_10q",
            "description": "Get the latest 10-Q filing summary for a ticker.",
            "parameters": {
                "type": "object",
                "properties": {"ticker": {"type": "string"}},
                "required": ["ticker"],
            },
        },
    },
]

__all__ = ["TOOL_FNS", "TOOL_SCHEMAS"]
