"""Redact secrets before they reach logs or process output."""

from __future__ import annotations

import re

_POSTGRES_URI = re.compile(r"postgres(?:ql)?://[^\s@'\"]+@", re.IGNORECASE)
_PASSWORD_ASSIGN = re.compile(
    r"(?i)(password|secret|api_key|apikey)(\s*[=:]\s*)(\S+)"
)


def redact(text: str) -> str:
    """Return text with database URIs and obvious secret assignments removed."""
    cleaned = _POSTGRES_URI.sub("postgres://REDACTED@", text)
    cleaned = _PASSWORD_ASSIGN.sub(r"\1\2REDACTED", cleaned)
    return cleaned
