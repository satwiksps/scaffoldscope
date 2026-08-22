"""Best-effort trace redaction. This is not a data-loss-prevention boundary."""

from __future__ import annotations

import re
from typing import Any

_PATTERNS = [
    # Escaped newlines are semantic token boundaries inside serialized observations.
    re.compile(r"(?:(?<![A-Za-z0-9_-])|(?<=\\[nr]))sk-[A-Za-z0-9_-]{16,}"),
    re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}"),
    re.compile(r"AKIA[A-Z0-9]{16}"),
    re.compile(r"(?i)(api[_-]?key\s*[=:]\s*)[^\s,;\"']+"),
    re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._~+/=-]{12,}"),
]

_SENSITIVE_KEYS = {
    "access_token",
    "api_key",
    "apikey",
    "authorization",
    "password",
    "refresh_token",
    "secret",
}

# These objects are keyed by operator-declared constraint or bundle identifiers.
# An identifier such as ``api-key`` is not itself a credential field, so preserve
# its typed metric value while still redacting strings nested below it.
_IDENTIFIER_KEYED_MAPS = {
    "constraint_checks",
    "constraint_details",
    "lexical_constraint_availability",
    "selected_scores",
}


def redact_text(value: str) -> str:
    redacted = value
    for pattern in _PATTERNS:
        if pattern.groups:
            redacted = pattern.sub(r"\1[REDACTED]", redacted)
        else:
            redacted = pattern.sub("[REDACTED]", redacted)
    return redacted


def _redact(value: Any, *, identifier_keys: bool = False) -> Any:
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, list):
        return [_redact(item) for item in value]
    if isinstance(value, dict):
        result: dict[Any, Any] = {}
        for key, item in value.items():
            normalized_key = str(key).lower().replace("-", "_")
            if not identifier_keys and normalized_key in _SENSITIVE_KEYS:
                result[key] = "[REDACTED]"
            else:
                result[key] = _redact(
                    item,
                    identifier_keys=normalized_key in _IDENTIFIER_KEYED_MAPS,
                )
        return result
    return value


def redact(value: Any) -> Any:
    return _redact(value)
