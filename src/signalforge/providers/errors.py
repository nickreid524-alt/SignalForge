"""Normalised provider failures.

Vendor SDK exceptions are mapped to a small set of SignalForge categories so
the orchestrator, trace and CLI never depend on vendor types. Messages are
redacted before they leave this module; no credential or auth header survives.
"""

from __future__ import annotations

from typing import Any, Literal

from signalforge.providers.base import ProviderError
from signalforge.redaction import redact

ErrorCategory = Literal[
    "missing_sdk",
    "missing_api_key",
    "missing_model",
    "invalid_model",
    "authentication",
    "permission",
    "rate_limited",
    "timeout",
    "network",
    "refusal",
    "malformed_output",
    "context_exhausted",
    "invalid_request",
    "server_error",
    "unknown",
]

RETRYABLE: frozenset[str] = frozenset({"rate_limited", "timeout", "network", "server_error"})

SETUP_CATEGORIES: frozenset[str] = frozenset({"missing_sdk", "missing_api_key", "missing_model"})


def sanitize(text: str) -> str:
    """Redact credentials and collapse whitespace; safe for logs, traces and CLI output."""
    return " ".join((redact(text) or "").split())[:800]


class ProviderFailure(ProviderError):
    def __init__(self, category: ErrorCategory, message: str, *, provider: str, status_code: int | None = None,
                 retryable: bool | None = None) -> None:
        self.category: ErrorCategory = category
        self.provider = provider
        self.status_code = status_code
        self.retryable = category in RETRYABLE if retryable is None else retryable
        self.detail = sanitize(message)
        super().__init__(f"[{provider}:{category}] {self.detail}")

    def to_record(self) -> dict[str, Any]:
        return {"provider": self.provider, "category": self.category, "status_code": self.status_code,
                "retryable": self.retryable, "detail": self.detail}


_CONTEXT_MARKERS = ("context", "too long", "too large", "max_tokens", "maximum", "token limit", "exceeds", "prompt is too")

_BY_CLASS_NAME: dict[str, ErrorCategory] = {
    "AuthenticationError": "authentication",
    "CredentialsError": "missing_api_key",
    "PermissionDeniedError": "permission",
    "RateLimitError": "rate_limited",
    "APITimeoutError": "timeout",
    "DeadlineExceededError": "timeout",
    "TimeoutError": "timeout",
    "APIConnectionError": "network",
    "ConnectError": "network",
    "InternalServerError": "server_error",
    "OverloadedError": "server_error",
    "ServiceUnavailableError": "server_error",
    "RequestTooLargeError": "context_exhausted",
    "LengthFinishReasonError": "context_exhausted",
    "ContentFilterFinishReasonError": "refusal",
    "APIResponseValidationError": "malformed_output",
    "UnprocessableEntityError": "invalid_request",
    "ConflictError": "server_error",
}


def _status_code(exc: BaseException) -> int | None:
    code = getattr(exc, "status_code", None)
    if isinstance(code, int):
        return code
    response = getattr(exc, "response", None)
    code = getattr(response, "status_code", None)
    return code if isinstance(code, int) else None


def classify_exception(exc: BaseException, provider: str) -> ProviderFailure:
    """Map any vendor exception to a ProviderFailure by class name, status code and message shape."""
    if isinstance(exc, ProviderFailure):
        return exc
    name = exc.__class__.__name__
    message = str(exc) or name
    lowered = message.lower()
    status = _status_code(exc)

    if name in ("NotFoundError",) or status == 404:
        category: ErrorCategory = "invalid_model" if "model" in lowered else "invalid_request"
    elif name in ("BadRequestError",) or status in (400, 413, 422):
        category = "context_exhausted" if any(m in lowered for m in _CONTEXT_MARKERS) else "invalid_request"
    elif name in _BY_CLASS_NAME:
        category = _BY_CLASS_NAME[name]
    elif status is not None:
        if status == 401:
            category = "authentication"
        elif status == 403:
            category = "permission"
        elif status in (408, 429):
            category = "rate_limited" if status == 429 else "timeout"
        elif status >= 500:
            category = "server_error"
        else:
            category = "invalid_request"
    elif isinstance(exc, TimeoutError):
        category = "timeout"
    elif isinstance(exc, (ConnectionError, OSError)):
        category = "network"
    else:
        category = "unknown"
    return ProviderFailure(category, f"{name}: {message}", provider=provider, status_code=status)
