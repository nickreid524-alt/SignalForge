"""Secret redaction shared by the audit trace, provider errors and cassettes.

Nothing that looks like a credential may be persisted, logged or shown: API keys,
bearer tokens, `key=value` secrets and credential-bearing HTTP headers.
"""

from __future__ import annotations

import re

_SECRET_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9_-]{10,}"),
    re.compile(r"(?i)bearer\s+[A-Za-z0-9._~+/=-]{10,}"),
    re.compile(r"(?i)\b(api[_-]?key|secret|password|token|x-api-key|authorization)\b(\s*[:=]\s*)([^\s,;'\"]{6,})"),
    re.compile(r"ghp_[A-Za-z0-9]{20,}"),
]


def redact(text: str | None) -> str | None:
    """Replace credential-shaped substrings with ``[REDACTED]``. Idempotent."""
    if text is None:
        return None
    out = text
    for pattern in _SECRET_PATTERNS:
        if pattern.groups >= 3:
            out = pattern.sub(lambda m: f"{m.group(1)}{m.group(2)}[REDACTED]", out)
        else:
            out = pattern.sub("[REDACTED]", out)
    return out
