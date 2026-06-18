"""Phase 4B: log scrubbing filter.

A logging.Filter that runs regex replacements on every record's message
before it reaches the handler. Patterns covered:

- Bearer tokens (Authorization: Bearer ...)
- OpenAI-style API keys (sk-...)
- Email addresses
- Chinese mobile numbers (11 digits, 1[3-9]...)
- Chinese ID card numbers (15 or 18 digits, last char may be X)
- Long digit runs that look like bank cards (13-19 digits)

The patterns are conservative — better to under-redact than to mangle
legitimate log lines (e.g. a 10-digit order id shouldn't be touched).
"""

from __future__ import annotations

import logging
import re

_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"Bearer\s+[A-Za-z0-9_\-\.\+]+", re.IGNORECASE), "Bearer ***"),
    (re.compile(r"sk-[A-Za-z0-9]{16,}"), "sk-***"),
    (re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}"), "***@***.***"),
    # 11-digit Chinese mobile — anchored with non-digit boundaries so we
    # don't catch substrings of larger numbers.
    (re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)"), "1**********"),
    # 18-digit ID card (last char 0-9 or X/x).
    (re.compile(r"(?<!\d)\d{17}[\dXx](?!\d)"), "******************"),
    # 15-digit legacy ID card.
    (re.compile(r"(?<!\d)\d{15}(?!\d)"), "***************"),
    # Bank-card-shaped runs (13-19 digits). Run last so ID-shaped numbers
    # above are matched first by the more specific patterns.
    (re.compile(r"(?<!\d)\d{13,19}(?!\d)"), "****"),
]


def scrub(text: str) -> str:
    """Apply every pattern to ``text`` and return the redacted version."""
    if not text:
        return text
    out = text
    for pat, repl in _PATTERNS:
        out = pat.sub(repl, out)
    return out


class ScrubFilter(logging.Filter):
    """logging.Filter that redacts PII / secrets from each record's message.

    We rewrite ``record.msg`` with the scrubbed text and clear ``record.args``
    so the formatter doesn't re-interpolate the original (un-scrubbed)
    arguments.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            formatted = record.getMessage()
        except Exception:  # noqa: BLE001
            # Interpolation can fail if args don't match the format string.
            # Don't make logging itself crash — pass the record through.
            return True
        scrubbed = scrub(formatted)
        record.msg = scrubbed
        record.args = ()
        return True
