"""
Custom exceptions for the Overmind client.
"""


class OvermindError(Exception):
    """Base exception for all Overmind client errors."""


class OvermindAuthenticationError(OvermindError):
    """Raised when authentication fails."""


class OvermindAPIError(OvermindError):
    """Raised when the API returns an error."""

    def __init__(self, message: str, status_code: int | None = None, response_data: dict | None = None):
        super().__init__(message)
        self.status_code = status_code
        self.response_data = response_data or {}


class OvermindValidationError(OvermindError):
    """Raised when input validation fails."""
