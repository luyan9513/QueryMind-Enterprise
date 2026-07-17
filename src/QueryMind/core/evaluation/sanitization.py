"""Redaction helpers for evaluation traces and exported reports."""

from __future__ import annotations

import re
from typing import Any, Dict


_DROP_KEYS = {
    "results",
    "result_rows",
    "raw_response",
    "connection_string",
}
_EXPORT_DROP_KEYS = (_DROP_KEYS - {"results"}) | {
    "preview_rows",
    "ground_truth_result_preview",
    "agent_result_preview",
    "raw_output",
}
_SENSITIVE_KEY_PARTS = (
    "api_key",
    "apikey",
    "password",
    "passwd",
    "secret",
    "token",
    "authorization",
)
_SECRET_PATTERNS = (
    re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._~+\-/=]+"),
    re.compile(r"(?i)(api[_-]?key\s*[=:]\s*)[^\s,;]+"),
    re.compile(r"(?i)(password\s*[=:]\s*)[^\s,;]+"),
    re.compile(r"(?i)(postgres(?:ql)?://[^:/\s]+:)[^@\s]+(@)"),
)


def redact_sensitive_text(value: str, *, max_length: int = 4000) -> str:
    """Redact common credential shapes without hiding useful SQL diagnostics."""
    redacted = value
    for pattern in _SECRET_PATTERNS:
        if pattern.groups >= 2:
            redacted = pattern.sub(r"\1***REDACTED***\2", redacted)
        else:
            redacted = pattern.sub(r"\1***REDACTED***", redacted)
    if len(redacted) > max_length:
        return f"{redacted[:max_length]}...<truncated>"
    return redacted


def sanitize_trace_value(value: Any, *, key: str = "") -> Any:
    """Remove bulky result rows and redact secret-like values recursively."""
    normalized_key = key.strip().lower()
    if normalized_key in _DROP_KEYS:
        return "<omitted>"
    if any(part in normalized_key for part in _SENSITIVE_KEY_PARTS):
        return "***REDACTED***"
    if isinstance(value, str):
        return redact_sensitive_text(value)
    if isinstance(value, dict):
        return {
            str(child_key): sanitize_trace_value(child_value, key=str(child_key))
            for child_key, child_value in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [sanitize_trace_value(item) for item in value]
    return value


def sanitize_trace_metadata(metadata: Dict[str, Any] | None) -> Dict[str, Any]:
    if not metadata:
        return {}
    sanitized = sanitize_trace_value(metadata)
    return sanitized if isinstance(sanitized, dict) else {}


def sanitize_export_payload(value: Any, *, key: str = "", depth: int = 0) -> Any:
    """Sanitize persisted reports while preserving aggregate token counters."""
    normalized_key = key.strip().lower()
    if normalized_key == "results" and depth != 1:
        return "<omitted>"
    if normalized_key in _EXPORT_DROP_KEYS:
        return "<omitted>"
    if normalized_key in {
        "api_key",
        "apikey",
        "password",
        "passwd",
        "secret",
        "authorization",
        "connection_string",
    }:
        return "***REDACTED***"
    if isinstance(value, str):
        return redact_sensitive_text(value)
    if isinstance(value, dict):
        return {
            str(child_key): sanitize_export_payload(
                child_value,
                key=str(child_key),
                depth=depth + 1,
            )
            for child_key, child_value in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [sanitize_export_payload(item, depth=depth + 1) for item in value]
    return value
