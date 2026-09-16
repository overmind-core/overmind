# Security policy

## Reporting a vulnerability

Do not open a public issue. Use one of:

- GitHub private vulnerability reporting: **Security → Report a vulnerability** on this repository.
- Email: support@overmindlab.ai

Include the affected component (API, Console, SDK, MCP server), steps to reproduce, and the impact you observed. You will get an acknowledgement within 3 business days.

## Scope

- This repository: the API (`overbae/`), the Console (`frontend/`), the SDK and CLI (`overmind/`, published to PyPI as `overmind`), and the MCP server at `/api/mcp/`.
- The hosted service at `console.overmindlab.ai` and `api.overmindlab.ai`.

Third-party services the platform integrates with (model providers, Modal, Baseten, Stripe, Clerk) are out of scope; report those upstream.

## Supported versions

Only `main` and the latest published `overmind` SDK release receive fixes.
