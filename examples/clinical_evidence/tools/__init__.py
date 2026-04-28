from .exa_guidelines import exa_search_guidelines
from .jina_fetch import jina_fetch_url
from .pubmed import pubmed_fetch_abstract, pubmed_search

TOOL_FNS = {
    "pubmed_search": pubmed_search,
    "pubmed_fetch_abstract": pubmed_fetch_abstract,
    "exa_search_guidelines": exa_search_guidelines,
    "jina_fetch_url": jina_fetch_url,
}

TOOL_SCHEMAS = [
    {
        "name": "pubmed_search",
        "description": "Search PubMed and get a list of PMIDs.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "max_results": {"type": "integer", "default": 5},
            },
            "required": ["query"],
        },
    },
    {
        "name": "pubmed_fetch_abstract",
        "description": "Fetch an abstract from PubMed.",
        "input_schema": {
            "type": "object",
            "properties": {"pmid": {"type": "string"}},
            "required": ["pmid"],
        },
    },
    {
        "name": "exa_search_guidelines",
        "description": "Search the web for clinical guidelines.",
        "input_schema": {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
    },
    {
        "name": "jina_fetch_url",
        "description": "Fetch a URL and return the readable text.",
        "input_schema": {
            "type": "object",
            "properties": {"url": {"type": "string"}},
            "required": ["url"],
        },
    },
]

__all__ = ["TOOL_FNS", "TOOL_SCHEMAS"]
