"""
Standardized response helpers for consistent API responses.
"""

from typing import Any
from fastapi import HTTPException, status
from pydantic import BaseModel


class APIResponse(BaseModel):
    """Standard API response model"""

    success: bool
    message: str
    data: Any | None = None
    errors: list[str] | None = None


def success_response(
    message: str = "Success",
    data: Any = None,
) -> APIResponse:
    """Create a successful response"""
    return APIResponse(success=True, message=message, data=data)


def error_response(
    message: str = "An error occurred",
    errors: list[str] | None = None,
    status_code: int = status.HTTP_400_BAD_REQUEST,
) -> HTTPException:
    """Create an error response"""
    response_data = APIResponse(success=False, message=message, errors=errors or [])

    raise HTTPException(status_code=status_code, detail=response_data.model_dump())


def validation_error_response(
    errors: list[str], message: str = "Validation failed"
) -> HTTPException:
    """Create a validation error response"""
    return error_response(
        message=message, errors=errors, status_code=status.HTTP_422_UNPROCESSABLE_ENTITY
    )


def not_found_response(message: str = "Resource not found") -> HTTPException:
    """Create a not found error response"""
    return error_response(message=message, status_code=status.HTTP_404_NOT_FOUND)


def forbidden_response(message: str = "Access forbidden") -> HTTPException:
    """Create a forbidden error response"""
    return error_response(message=message, status_code=status.HTTP_403_FORBIDDEN)


def unauthorized_response(message: str = "Authentication required") -> HTTPException:
    """Create an unauthorized error response"""
    return error_response(message=message, status_code=status.HTTP_401_UNAUTHORIZED)


def conflict_response(message: str = "Resource conflict") -> HTTPException:
    """Create a conflict error response"""
    return error_response(message=message, status_code=status.HTTP_409_CONFLICT)
