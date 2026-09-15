"""Typed, provenanced, versioned Dataset context bundle.

Written to ``MEDIA_ROOT/dataset/<dataset_key>/<data_version>/`` and indexed by a
manifest that embeds each artifact's content, so the standalone MCP server that
reads it needs no ORM.
"""
