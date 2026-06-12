from __future__ import annotations

import html
import re
from datetime import datetime
from enum import StrEnum
from typing import Any

import pandas as pd
from pydantic import BaseModel, Field

from ..utils.logger import get_logger

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Spec
# ---------------------------------------------------------------------------
# Output schema for the automated visualization engine.
#
#   {
#     "type": "bar" | "line" | "pie" | "scatter" | "table",
#     "config": {
#       "xField": "category",
#       "yField": "revenue",
#       "seriesField": "region",
#       "color": {"field": "region", "values": ["#3b82f6", ...]},
#       "title": "Quarterly revenue"
#     },
#     "data": [{"category": "Q1", "revenue": 120}, ...],
#     "row_count": 1000,
#     "aggregated": true
#   }
# ---------------------------------------------------------------------------


class ChartType(StrEnum):
    BAR = "bar"
    LINE = "line"
    PIE = "pie"
    SCATTER = "scatter"
    TABLE = "table"


SUPPORTED_TYPES = {t.value for t in ChartType}

MAX_ROWS = 1000
MAX_TITLE_LEN = 200
MAX_FIELD_LEN = 100
MAX_CELL_LEN = 500

# Distinct, accessible palette. The viz engine only ever emits these
# hex values, so the rendered chart can never smuggle a `url(javascript:...)`
# style payload past the frontend.
DEFAULT_PALETTE: list[str] = [
    "#3b82f6",  # blue
    "#10b981",  # green
    "#f59e0b",  # amber
    "#ef4444",  # red
    "#a855f7",  # purple
    "#06b6d4",  # cyan
    "#ec4899",  # pink
    "#84cc16",  # lime
]

# Bilingual color lookup. Anything not in this map is rejected by
# sanitize_color, so user-supplied colors can never reach the chart.
COLOR_NAME_MAP: dict[str, str] = {
    "red": "#ef4444",
    "\u7ea2": "#ef4444",
    "\u7ea2\u8272": "#ef4444",
    "blue": "#3b82f6",
    "\u84dd": "#3b82f6",
    "\u84dd\u8272": "#3b82f6",
    "green": "#10b981",
    "\u7eff": "#10b981",
    "\u7eff\u8272": "#10b981",
    "yellow": "#eab308",
    "\u9ec4": "#eab308",
    "\u9ec4\u8272": "#eab308",
    "purple": "#a855f7",
    "\u7d2b": "#a855f7",
    "\u7d2b\u8272": "#a855f7",
    "orange": "#f97316",
    "\u6a59": "#f97316",
    "\u6a59\u8272": "#f97316",
    "black": "#1f2937",
    "\u9ed1": "#1f2937",
    "\u9ed1\u8272": "#1f2937",
    "white": "#f9fafb",
    "\u767d": "#f9fafb",
    "\u767d\u8272": "#f9fafb",
    "gray": "#6b7280",
    "\u7070": "#6b7280",
    "\u7070\u8272": "#6b7280",
    "cyan": "#06b6d4",
    "\u9752": "#06b6d4",
    "\u9752\u8272": "#06b6d4",
    "pink": "#ec4899",
    "\u7c89": "#ec4899",
    "\u7c89\u8272": "#ec4899",
}

# Column-name hints that suggest a time/date column.
_TIME_KEYWORDS = (
    "date",
    "time",
    "datetime",
    "timestamp",
    "created",
    "updated",
    "\u65e5\u671f",
    "\u65f6\u95f4",
    "\u5e74\u6708",
)


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------


class ColorSpec(BaseModel):
    field: str | None = None
    values: list[str] = Field(default_factory=list)


class ChartConfigSpec(BaseModel):
    xField: str | None = None
    yField: str | None = None
    seriesField: str | None = None
    color: ColorSpec = Field(default_factory=ColorSpec)
    title: str | None = None


class ChartSpec(BaseModel):
    type: ChartType
    config: ChartConfigSpec = Field(default_factory=ChartConfigSpec)
    data: list[dict[str, Any]] = Field(default_factory=list)
    row_count: int = 0
    aggregated: bool = False


# ---------------------------------------------------------------------------
# Column type inference
# ---------------------------------------------------------------------------


def _looks_like_time(values: pd.Series, name: str) -> bool:
    """Heuristic: dtype- or name-based, with a parsing confirmation."""
    if pd.api.types.is_datetime64_any_dtype(values):
        return True
    lname = (name or "").lower()
    if not any(kw in lname for kw in _TIME_KEYWORDS):
        return False
    sample = values.dropna().head(20)
    if sample.empty:
        return False
    try:
        parsed = pd.to_datetime(sample, errors="coerce")
    except Exception:
        return False
    return bool(parsed.notna().mean() > 0.7)


def infer_column_types(df: pd.DataFrame) -> dict[str, str]:
    """Return {col: 'time' | 'numeric' | 'category'}."""
    result: dict[str, str] = {}
    for col in df.columns:
        series = df[col]
        if _looks_like_time(series, str(col)):
            result[col] = "time"
        elif pd.api.types.is_numeric_dtype(series):
            result[col] = "numeric"
        else:
            result[col] = "category"
    return result


# ---------------------------------------------------------------------------
# Recommendation
# ---------------------------------------------------------------------------


def recommend_chart_type(df: pd.DataFrame) -> ChartType:
    """Apply the spec's auto-recommendation rules.

    Precedence: time+numeric > category+numeric > 2+numeric > table.
    Category+numeric becomes pie when the number of distinct values is <= 5.
    """
    if df is None or df.empty or len(df.columns) < 1:
        return ChartType.TABLE

    types = infer_column_types(df)
    has_time = any(t == "time" for t in types.values())
    numeric_cols = [c for c, t in types.items() if t == "numeric"]
    category_cols = [c for c, t in types.items() if t == "category"]

    if has_time and numeric_cols:
        return ChartType.LINE
    if category_cols and numeric_cols:
        n_categories = df[category_cols[0]].nunique(dropna=True)
        if n_categories <= 5:
            return ChartType.PIE
        return ChartType.BAR
    if len(numeric_cols) >= 2:
        return ChartType.SCATTER
    return ChartType.TABLE


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------


def aggregate_data(df: pd.DataFrame) -> tuple[pd.DataFrame, bool]:
    """Reduce to <= MAX_ROWS. Returns (dataframe, aggregated_flag)."""
    if df is None or df.empty:
        return df, False
    if len(df) <= MAX_ROWS:
        return df, False

    types = infer_column_types(df)
    time_cols = [c for c, t in types.items() if t == "time"]
    numeric_cols = [c for c, t in types.items() if t == "numeric"]
    category_cols = [c for c, t in types.items() if t == "category"]

    # Time series: bucket into ~50 time windows and average the numerics.
    if time_cols and numeric_cols:
        try:
            ts = pd.to_datetime(df[time_cols[0]], errors="coerce")
            valid_mask = ts.notna()
            if valid_mask.sum() > 0:
                sub = df.loc[valid_mask].copy()
                sub["_ts"] = ts[valid_mask]
                n_buckets = min(MAX_ROWS, 50)
                sub["_bucket"] = pd.cut(sub["_ts"], bins=n_buckets)
                agg = (
                    sub.groupby("_bucket", observed=True)[numeric_cols]
                    .mean(numeric_only=True)
                    .reset_index()
                )
                agg[time_cols[0]] = agg["_bucket"].astype(str)
                agg = agg.drop(columns=["_bucket"])
                return agg.head(MAX_ROWS), True
        except Exception as e:
            logger.warning("time bucketing failed: %s", e)

    # Categorical: group by the first category and sum the numerics.
    if category_cols and numeric_cols:
        try:
            agg = (
                df.groupby(category_cols[0], observed=True)[numeric_cols]
                .sum(numeric_only=True)
                .reset_index()
            )
            return agg.head(MAX_ROWS), True
        except Exception as e:
            logger.warning("category aggregation failed: %s", e)

    # Last resort: a stable random sample.
    return df.sample(n=MAX_ROWS, random_state=42).reset_index(drop=True), True


# ---------------------------------------------------------------------------
# XSS prevention
# ---------------------------------------------------------------------------

_HTML_TAG = re.compile(r"<[^>]*>")
_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_HEX_COLOR = re.compile(r"^#([0-9a-fA-F]{3}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})$")


def sanitize_text(value: Any, max_len: int = MAX_TITLE_LEN) -> str:
    """Return a string safe to embed in HTML / JS contexts.

    Strips control characters, HTML tags, and HTML-escapes the result.
    Used for titles, category labels, and any other free-form text that the
    chart renderer might echo back into the DOM.
    """
    if value is None:
        return ""
    s = str(value)
    s = _CONTROL_CHARS.sub("", s)
    s = _HTML_TAG.sub("", s)
    s = html.escape(s, quote=True)
    if len(s) > max_len:
        s = s[: max_len - 1] + "\u2026"
    return s


def sanitize_field_name(name: str) -> str:
    """Field names go into ECharts option keys, so they must be safe."""
    if not isinstance(name, str):
        return ""
    s = _CONTROL_CHARS.sub("", name)
    s = _HTML_TAG.sub("", s)
    s = html.escape(s, quote=True)
    if len(s) > MAX_FIELD_LEN:
        s = s[:MAX_FIELD_LEN]
    return s


def sanitize_color(color: Any) -> str | None:
    """Accept only the curated palette or a strict hex literal."""
    if not isinstance(color, str):
        return None
    raw = color.strip().lower()
    if not raw:
        return None
    if _HEX_COLOR.match(raw):
        # Normalize 3-digit / 8-digit hex to the 6-digit form ECharts uses.
        hexpart = raw[1:]
        if len(hexpart) == 3:
            hexpart = "".join(ch * 2 for ch in hexpart)
        elif len(hexpart) == 8:
            hexpart = hexpart[:6]
        return "#" + hexpart
    if raw in COLOR_NAME_MAP:
        return COLOR_NAME_MAP[raw]
    return None


def normalize_row(row: dict[str, Any]) -> dict[str, Any]:
    """Coerce a DataFrame row to JSON-safe primitives, sanitizing strings."""
    out: dict[str, Any] = {}
    for k, v in row.items():
        safe_k = sanitize_field_name(str(k))
        if not safe_k:
            continue
        if v is None or isinstance(v, bool | int):
            out[safe_k] = v
        elif isinstance(v, float):
            # NaN / inf would break JSON; map them to None.
            out[safe_k] = v if v == v and v not in (float("inf"), float("-inf")) else None
        elif isinstance(v, pd.Timestamp | datetime):
            out[safe_k] = v.isoformat()
        else:
            out[safe_k] = sanitize_text(v, max_len=MAX_CELL_LEN)
    return out


# ---------------------------------------------------------------------------
# User override parsing
# ---------------------------------------------------------------------------

_TYPE_PATTERNS_EN = (
    re.compile(
        r"(?:switch|change|convert)\s+(?:it\s+)?to\s+(bar|line|pie|scatter|table)", re.IGNORECASE
    ),
    re.compile(r"\b(?:as|to)\s+(bar|line|pie|scatter|table)\b", re.IGNORECASE),
)
_TYPE_PATTERN_CN = re.compile(
    r"(?:改成|换成|变为|切到|用)\s*([\u6298\u67f1\u997c\u6563\u8868]?\s*图|\u8868\u683c|bar|line|pie|scatter|table)",
    re.IGNORECASE,
)

_CN_TYPE_KEYWORDS = {
    "\u6298\u7ebf": "line",
    "\u6298\u7ebf\u56fe": "line",
    "\u67f1\u72b6": "bar",
    "\u67f1\u72b6\u56fe": "bar",
    "\u997c": "pie",
    "\u997c\u56fe": "pie",
    "\u6563\u70b9": "scatter",
    "\u6563\u70b9\u56fe": "scatter",
    "\u8868\u683c": "table",
}


def _parse_type_override(query: str) -> ChartType | None:
    for pat in _TYPE_PATTERNS_EN:
        m = pat.search(query)
        if m:
            v = m.group(1).lower()
            if v in SUPPORTED_TYPES:
                return ChartType(v)
    m = _TYPE_PATTERN_CN.search(query)
    if m:
        token = m.group(1).lower()
        for k, v in _CN_TYPE_KEYWORDS.items():
            if k in token:
                return ChartType(v)
        for v in SUPPORTED_TYPES:
            if v in token:
                return ChartType(v)
    return None


_COLOR_PATTERN = re.compile(
    r"(?P<field>[\w\u4e00-\u9fff]{1,20})\s*用\s*"
    r"(?P<color>#[0-9a-fA-F]{3,8}|[\u4e00-\u9fff]{1,6})"
)


def _parse_color_override(query: str) -> tuple[str, str] | None:
    m = _COLOR_PATTERN.search(query)
    if not m:
        return None
    field = sanitize_field_name(m.group("field"))
    color = sanitize_color(m.group("color"))
    if not color:
        return None
    return field, color


_TITLE_PATTERN = re.compile(
    r"(?:\u6dfb\u52a0\u6807\u9898|set\s+title)\s*[:\uff1a]?\s*"
    r"(?:[\"\"\u201c\u300c\u300e'])?(?P<title>.+?)(?:[\"\"\u201d\u300d\u300f']|$)",
    re.IGNORECASE,
)


def _parse_title_override(query: str) -> str | None:
    m = _TITLE_PATTERN.search(query)
    if not m:
        return None
    return sanitize_text(m.group("title"), max_len=MAX_TITLE_LEN)


def apply_user_overrides(spec: ChartSpec, user_query: str) -> ChartSpec:
    """Mutate-and-return `spec` after applying any matching overrides."""
    user_query = (user_query or "").strip()
    if not user_query:
        return spec

    type_override = _parse_type_override(user_query)
    if type_override and type_override != spec.type:
        # Type changed -- the data needs to be re-laid-out for the new shape.
        df = pd.DataFrame(spec.data) if spec.data else pd.DataFrame()
        new_type = type_override
        if df.empty:
            spec.type = new_type
        else:
            df_agg, _ = aggregate_data(df)
            new_config, new_rows = _build_config_from_data(
                df_agg, new_type, title=spec.config.title
            )
            spec.type = new_type
            spec.config = new_config
            spec.data = new_rows
            spec.row_count = len(new_rows)

    color_override = _parse_color_override(user_query)
    if color_override:
        field, color = color_override
        if not spec.config.color.values:
            spec.config.color = ColorSpec(field=field or None, values=[color])
        else:
            # If the user named a field, attach it; otherwise just paint the
            # first series.
            if field:
                spec.config.color.field = field
            spec.config.color.values = [color, *spec.config.color.values[1:]]

    title_override = _parse_title_override(user_query)
    if title_override:
        spec.config.title = title_override

    return spec


# ---------------------------------------------------------------------------
# Chart spec building
# ---------------------------------------------------------------------------


def _build_config_from_data(
    df: pd.DataFrame,
    chart_type: ChartType,
    title: str | None = None,
) -> tuple[ChartConfigSpec, list[dict[str, Any]]]:
    """Build a config + normalized data from a DataFrame (no LLM suggestion)."""
    types = infer_column_types(df)
    numeric_cols = [sanitize_field_name(c) for c, t in types.items() if t == "numeric"]
    category_cols = [sanitize_field_name(c) for c, t in types.items() if t == "category"]
    time_cols = [sanitize_field_name(c) for c, t in types.items() if t == "time"]

    config = ChartConfigSpec(
        title=sanitize_text(title) if title else None,
    )
    rows = [normalize_row(r) for r in df.to_dict(orient="records")]

    if chart_type == ChartType.TABLE or len(numeric_cols) == 0 and len(category_cols) == 0:
        return config, rows

    if chart_type == ChartType.SCATTER:
        if len(numeric_cols) >= 2:
            config.xField = numeric_cols[0]
            config.yField = numeric_cols[1]
            config.color = ColorSpec(field=None, values=[DEFAULT_PALETTE[0]])
        return config, rows

    if chart_type == ChartType.PIE:
        if category_cols and numeric_cols:
            x_col = category_cols[0]
            y_col = numeric_cols[0]
            config.xField = x_col
            config.yField = y_col
            n = max(1, min(len(DEFAULT_PALETTE), df[category_cols[0]].nunique(dropna=True)))
            config.color = ColorSpec(
                field=x_col,
                values=DEFAULT_PALETTE[:n],
            )
            # PIE's wire format is [{name, value}, ...], not the column-name
            # shape BAR/LINE use. echarts_from_spec reads r.get("name"/"value"),
            # so we project to that shape here.
            rows = [
                {"name": str(r.get(x_col, "")), "value": r.get(y_col)}
                for r in rows
                if r.get(x_col) is not None
            ]
        return config, rows

    # BAR / LINE
    x_pick = time_cols[:1] or category_cols[:1] or numeric_cols[:1]
    y_pick = numeric_cols[:1] or category_cols[1:2]
    if x_pick:
        config.xField = x_pick[0]
    if y_pick:
        config.yField = y_pick[0]

    if category_cols:
        n = max(1, min(len(DEFAULT_PALETTE), df[category_cols[0]].nunique(dropna=True)))
        config.color = ColorSpec(
            field=category_cols[0],
            values=DEFAULT_PALETTE[:n],
        )
    else:
        config.color = ColorSpec(field=None, values=[DEFAULT_PALETTE[0]])

    return config, rows


def _build_config_from_llm(
    args: dict[str, Any],
) -> tuple[ChartType, ChartConfigSpec, list[dict[str, Any]]]:
    """Translate the raw `create_chart` tool args into a ChartSpec."""
    raw_type = (args.get("chart_type") or "").lower()
    chart_type = ChartType(raw_type) if raw_type in SUPPORTED_TYPES else ChartType.BAR
    title = args.get("title")
    x_data = args.get("x_data") or []
    series = args.get("series") or []

    config = ChartConfigSpec(title=sanitize_text(title) if title else None)
    series_names = [
        sanitize_field_name(s.get("name") or f"series_{i + 1}") for i, s in enumerate(series)
    ]
    config.color = ColorSpec(
        field=None,
        values=DEFAULT_PALETTE[: max(1, len(series_names))],
    )

    if chart_type == ChartType.PIE:
        first = series[0] if series else {}
        first_data = _coerce_to_list(first.get("data"))
        rows = []
        for i, x in enumerate(x_data):
            value = first_data[i] if i < len(first_data) else None
            rows.append({"name": sanitize_text(x), "value": value})
        return chart_type, config, rows

    rows = []
    for i, x in enumerate(x_data):
        row: dict[str, Any] = {"_x": sanitize_text(x)}
        for sname, s in zip(series_names, series, strict=False):
            data = _coerce_to_list(s.get("data"))
            if i < len(data):
                row[sname] = data[i]
        rows.append(row)
    return chart_type, config, rows


def _coerce_to_list(value: Any) -> list[Any]:
    """LLM tool args don't always honor the documented list shape.

    `series[].data` is supposed to be a flat list of numbers, but the model
    sometimes returns a dict, a string, or a single value. Coerce defensively
    so post_process never crashes on a contract slip.
    """
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    if isinstance(value, dict):
        # Common LLM slip: {"values": [...]} or {"0": ..., "1": ...}.
        if "values" in value and isinstance(value["values"], list):
            return value["values"]
        try:
            return [value[str(i)] for i in range(len(value))]
        except (KeyError, TypeError):
            return [value]
    if value is None or value == "":
        return []
    return [value]


def build_chart_spec(
    df: pd.DataFrame | None,
    llm_suggestion: dict[str, Any] | None = None,
    user_query: str = "",
    title: str | None = None,
) -> ChartSpec:
    """Main entry point.

    1. Aggregate the input DataFrame if it exceeds MAX_ROWS.
    2. Take the LLM's `create_chart` args as a strong hint if provided;
       otherwise run the recommendation rules.
    3. Apply any user overrides (改类型 / 字段用颜色 / 添加标题).
    4. Sanitize every string and color value on the way out.
    """
    if df is None or df.empty:
        empty = ChartSpec(
            type=ChartType.TABLE,
            data=[],
            row_count=0,
        )
        return apply_user_overrides(empty, user_query)

    df_agg, aggregated = aggregate_data(df)

    if llm_suggestion and isinstance(llm_suggestion, dict):
        chart_type, config, rows = _build_config_from_llm(llm_suggestion)
    else:
        chart_type = recommend_chart_type(df_agg)
        config, rows = _build_config_from_data(df_agg, chart_type, title=title)
        if title and not config.title:
            config.title = sanitize_text(title)

    spec = ChartSpec(
        type=chart_type,
        config=config,
        data=rows,
        row_count=len(rows),
        aggregated=aggregated,
    )
    return apply_user_overrides(spec, user_query)


# ---------------------------------------------------------------------------
# ECharts translation
# ---------------------------------------------------------------------------


def echarts_from_spec(spec: ChartSpec) -> dict[str, Any] | None:
    """Translate a ChartSpec to an ECharts option dict.

    Returns None for TABLE (the frontend renders its own table view). The
    shape mirrors what `_build_echarts_option` used to emit in graph.py, so
    existing ECharts frontends can keep consuming the stream unchanged.
    """
    if spec.type == ChartType.TABLE or not spec.data:
        return None

    title = spec.config.title or ""
    palette = spec.config.color.values or DEFAULT_PALETTE[:1]

    if spec.type == ChartType.SCATTER:
        x_field = spec.config.xField or ""
        y_field = spec.config.yField or ""
        data = [
            [r.get(x_field), r.get(y_field)]
            for r in spec.data
            if r.get(x_field) is not None and r.get(y_field) is not None
        ]
        return {
            "title": {"text": title, "left": "center"},
            "tooltip": {"trigger": "item"},
            "xAxis": {"type": "value", "name": x_field},
            "yAxis": {"type": "value", "name": y_field},
            "color": palette,
            "series": [{"type": "scatter", "data": data}],
        }

    if spec.type == ChartType.PIE:
        pie_data = [{"name": str(r.get("name", "")), "value": r.get("value")} for r in spec.data]
        series_name = spec.config.seriesField or (pie_data[0]["name"] if pie_data else title)
        return {
            "title": {"text": title, "left": "center"},
            "tooltip": {"trigger": "item"},
            "legend": {"bottom": 0},
            "color": palette,
            "series": [
                {
                    "name": series_name,
                    "type": "pie",
                    "data": pie_data,
                    "radius": ["0%", "70%"],
                }
            ],
        }

    # BAR / LINE
    x_field = spec.config.xField
    if x_field and spec.data and x_field in spec.data[0]:
        x_data = [str(r.get(x_field, "")) for r in spec.data]
        if spec.config.yField and spec.config.yField not in (x_field,):
            series_names = [spec.config.yField]
        else:
            series_names = sorted(k for r in spec.data for k in r if k not in (x_field,))
    else:
        x_data = [str(r.get("_x", "")) for r in spec.data]
        series_names = sorted(k for r in spec.data for k in r if k not in ("_x",))

    series = []
    for sname in series_names:
        sdata = [r.get(sname) for r in spec.data]
        series.append(
            {
                "name": sname,
                "type": spec.type.value,
                "data": sdata,
            }
        )

    return {
        "title": {"text": title, "left": "center"},
        "tooltip": {"trigger": "axis"},
        "legend": {"bottom": 0},
        "color": palette,
        "grid": {"left": 40, "right": 20, "top": 50, "bottom": 50, "containLabel": True},
        "xAxis": {"type": "category", "data": x_data, "name": ""},
        "yAxis": {"type": "value"},
        "series": series,
    }
