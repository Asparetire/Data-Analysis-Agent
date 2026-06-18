"""Phase 4C: PII masking utilities, applied at four layers.

Layers (per Phase 4 design):
  1. Upload-time scrub — DataFrame columns are masked before ``to_sql``,
     so the raw PII never lands in the SQLite file.
  2. SQL result exit — ``query_database`` masks rows before returning JSON.
     Catches PII-shaped values that slipped past layer 1 (e.g. a column
     the heuristic missed) and serves as defense-in-depth.
  3. Audit log — ``lineage`` scrubs string literals in the recorded SQL so
     ``WHERE email = 'alice@x.com'`` doesn't end up in ``datasources.json``.
  4. LLM prompt — ``get_table_schema`` / ``get_sample_rows`` mask sample
     values before they ride into the LLM prompt.

The same regex set powers every layer; ``mask_value`` is the single
primitive. Patterns are anchored with non-digit boundaries so we don't
accidentally redact substrings of legitimate numbers (order ids, etc.).
"""

from __future__ import annotations

import re
from typing import Any

import pandas as pd
from pandas.api.types import is_string_dtype

# Order matters: more specific patterns first so ID-card-shaped numbers
# don't get caught by the broader bank-card pattern.
_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}"), "***@***.***"),
    (re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)"), "1**********"),
    (re.compile(r"(?<!\d)\d{17}[\dXx](?!\d)"), "******************"),
    (re.compile(r"(?<!\d)\d{15}(?!\d)"), "***************"),
    (re.compile(r"(?<!\d)\d{13,19}(?!\d)"), "****"),
]


def mask_value(value: Any) -> Any:
    """Return ``value`` with any PII substring redacted.

    Non-string values pass through untouched. ``None`` stays ``None`` so
    NULL semantics in the DB aren't disturbed.
    """
    if value is None:
        return None
    if not isinstance(value, str):
        return value
    out = value
    for pat, repl in _PATTERNS:
        out = pat.sub(repl, out)
    return out


def mask_rows(rows: list[dict]) -> list[dict]:
    """Mask every string value in a list of dict rows (layer 2)."""
    if not rows:
        return rows
    return [{k: mask_value(v) for k, v in row.items()} for row in rows]


def mask_dataframe(df: pd.DataFrame) -> pd.DataFrame:
    """Mask every string-column value in a DataFrame (layer 1).

    Returns a new DataFrame; the input is not mutated. We only scan
    string-dtype columns (which covers both the legacy ``object`` dtype
    and pandas 3's native ``str`` dtype) because numeric / datetime
    columns can't carry PII strings.
    """
    if df.empty:
        return df.copy()
    out = df.copy()
    for col in out.columns:
        if is_string_dtype(out[col]):
            out[col] = out[col].map(mask_value)
    return out


# ---------------------------------------------------------------------------
# Layer 3: SQL literal scrubbing for audit logs
# ---------------------------------------------------------------------------

# Match single-quoted string literals (SQL standard). Doubled quotes ('')
# are the SQL escape for a literal single quote; the regex tolerates them
# so 'alice''s email' is treated as one literal.
_SQL_STRING_LITERAL = re.compile(r"'(?:''|[^'])*'")


def mask_sql_literals(sql: str) -> str:
    """Redact PII inside SQL string literals.

    Used only for lineage records — the actual SQL still runs against the
    DB unchanged. We walk every ``'...'`` literal and apply ``mask_value``
    to its contents, so ``WHERE email = 'alice@x.com'`` becomes
    ``WHERE email = '***@***.***'`` in the audit log.
    """
    if not sql:
        return sql

    def _mask_literal(m: re.Match[str]) -> str:
        literal = m.group(0)
        # Strip the surrounding quotes, mask, re-wrap.
        inner = literal[1:-1].replace("''", "'")
        masked = mask_value(inner)
        # Re-escape any single quotes for SQL safety.
        escaped = masked.replace("'", "''")
        return f"'{escaped}'"

    return _SQL_STRING_LITERAL.sub(_mask_literal, sql)
