"""
Template Extractor - Extract prompt templates from LLM traces.

This package analyzes a list of strings (LLM inputs) and groups them by
common templates, extracting both the template structure and variable values.

Usage:
    from overmind_core.overmind.template_extractor import extract_templates, ExtractionConfig

    traces = [
        "Hello Alice, welcome to the system!",
        "Hello Bob, welcome to the system!",
        "Hello Charlie, welcome to the system!",
    ]

    result = extract_templates(traces)
    print(result.summary())
"""

from .extractor import (
    ExtractionConfig,
    ExtractionResult,
    ExtractedVariable,
    Template,
    TemplateElement,
    TemplateMatch,
    extract_templates,
    match_string_to_template,
)
from .helpers import Token, tokenize, token_values, tokens_to_string

__all__ = [
    # Main API
    "extract_templates",
    "match_string_to_template",
    # Config & Result types
    "ExtractionConfig",
    "ExtractionResult",
    # Data models
    "Template",
    "TemplateElement",
    "TemplateMatch",
    "ExtractedVariable",
    "Token",
    # Helper functions (for advanced usage)
    "tokenize",
    "token_values",
    "tokens_to_string",
]
