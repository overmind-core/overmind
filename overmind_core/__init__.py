"""
overmind_core — standalone observability backend for distributed tracing.

This package provides all core product features: tracing, spans, prompts,
agents, jobs, suggestions, backtesting, the guardrail/policy engine,
OTLP ingest, and the LLM proxy.

Data model: User, Project, Token (simple). Everything is scoped by
project_id + user_id. No organisations, no RBAC roles.
"""
