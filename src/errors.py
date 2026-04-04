from __future__ import annotations

from enum import Enum
from typing import Optional

from fastapi.responses import JSONResponse
from pydantic import BaseModel


class ErrorCode(str, Enum):
    INVALID_FILING_TYPE = "INVALID_FILING_TYPE"
    INVALID_COMPANY = "INVALID_COMPANY"
    QUERY_TOO_LONG = "QUERY_TOO_LONG"
    QUERY_EMPTY = "QUERY_EMPTY"
    TOP_K_OUT_OF_RANGE = "TOP_K_OUT_OF_RANGE"
    UNKNOWN_FIELDS = "UNKNOWN_FIELDS"
    RATE_LIMIT_EXCEEDED = "RATE_LIMIT_EXCEEDED"
    PAYMENT_REQUIRED = "PAYMENT_REQUIRED"
    PAYMENT_VERIFICATION_FAILED = "PAYMENT_VERIFICATION_FAILED"
    FACILITATOR_UNAVAILABLE = "FACILITATOR_UNAVAILABLE"
    REQUEST_TOO_LARGE = "REQUEST_TOO_LARGE"
    INTERNAL_ERROR = "INTERNAL_ERROR"


class ErrorDetail(BaseModel):
    code: ErrorCode
    message: str
    retry: bool
    field: Optional[str] = None


class ErrorResponse(BaseModel):
    error: ErrorDetail


def make_error(
    code: ErrorCode,
    message: str,
    retry: bool,
    status_code: int,
    field: Optional[str] = None,
) -> JSONResponse:
    body = ErrorResponse(
        error=ErrorDetail(code=code, message=message, retry=retry, field=field)
    )
    return JSONResponse(status_code=status_code, content=body.model_dump())
