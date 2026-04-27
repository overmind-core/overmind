from .customer_lookup import lookup_customer
from .kb_search import search_kb
from .public_docs import search_public_docs

TOOL_FNS = {
    "search_kb": search_kb,
    "lookup_customer": lookup_customer,
    "search_public_docs": search_public_docs,
}

TOOL_SCHEMAS = [
    {
        "name": "search_kb",
        "description": "Search the knowledge base.",
        "input_schema": {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
    },
    {
        "name": "lookup_customer",
        "description": "Look up a customer by id.",
        "input_schema": {
            "type": "object",
            "properties": {"customer_id": {"type": "string"}},
            "required": ["customer_id"],
        },
    },
    {
        "name": "search_public_docs",
        "description": "Search public docs on the web.",
        "input_schema": {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
    },
]

__all__ = ["TOOL_FNS", "TOOL_SCHEMAS"]
