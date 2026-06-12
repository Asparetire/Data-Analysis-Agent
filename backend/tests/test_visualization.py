"""Tests for the visualization pipeline.

Covers:
- recommend_chart_type over the spec's input-shape matrix
- build_chart_spec with empty / sparse DataFrames
- apply_user_overrides: 改类型, 改颜色, 加标题
- echarts_from_spec: shape contract for SCATTER / PIE / BAR / TABLE
"""

from __future__ import annotations

import pandas as pd
from app.services.visualization import (
    MAX_ROWS,
    ChartType,
    apply_user_overrides,
    build_chart_spec,
    echarts_from_spec,
    recommend_chart_type,
)

# ---------------------------------------------------------------------------
# recommend_chart_type
# ---------------------------------------------------------------------------


def test_recommend_line_for_time_and_numeric():
    df = pd.DataFrame(
        {
            "date": pd.to_datetime(["2026-01-01", "2026-02-01", "2026-03-01", "2026-04-01"]),
            "sales": [10, 20, 30, 40],
        }
    )
    assert recommend_chart_type(df) == ChartType.LINE


def test_recommend_pie_for_few_categories():
    df = pd.DataFrame({"region": ["A", "B", "C"], "revenue": [10, 20, 30]})
    assert recommend_chart_type(df) == ChartType.PIE


def test_recommend_bar_for_many_categories():
    df = pd.DataFrame({"region": [f"R{i}" for i in range(8)], "revenue": list(range(8))})
    assert recommend_chart_type(df) == ChartType.BAR


def test_recommend_scatter_for_two_numerics():
    df = pd.DataFrame({"x": [1.0, 2.0, 3.0, 4.0], "y": [2.0, 4.0, 1.0, 5.0]})
    assert recommend_chart_type(df) == ChartType.SCATTER


def test_recommend_table_for_empty_df():
    assert recommend_chart_type(pd.DataFrame()) == ChartType.TABLE


# ---------------------------------------------------------------------------
# build_chart_spec
# ---------------------------------------------------------------------------


def test_build_chart_spec_empty_dataframe_returns_table():
    spec = build_chart_spec(df=pd.DataFrame(), user_query="")
    assert spec.type == ChartType.TABLE
    assert spec.data == []


def test_build_chart_spec_aggregates_over_max_rows():
    """A frame with > MAX_ROWS should be aggregated and marked aggregated=True."""
    df = pd.DataFrame({"k": list(range(MAX_ROWS + 500)), "v": [1.0] * (MAX_ROWS + 500)})
    spec = build_chart_spec(df=df, user_query="")
    assert spec.row_count <= MAX_ROWS
    assert spec.aggregated is True


def test_build_chart_spec_none_returns_table_with_zero_rows():
    spec = build_chart_spec(df=None, user_query="")
    assert spec.type == ChartType.TABLE
    assert spec.row_count == 0


# ---------------------------------------------------------------------------
# apply_user_overrides
# ---------------------------------------------------------------------------


def test_user_override_changes_type_to_pie():
    df = pd.DataFrame({"region": ["A", "B", "C", "D", "E", "F"], "rev": [1, 2, 3, 4, 5, 6]})
    spec = build_chart_spec(df=df, user_query="")
    assert spec.type == ChartType.BAR
    new_spec = apply_user_overrides(spec, "改成饼图")
    assert new_spec.type == ChartType.PIE


def test_user_override_sets_title():
    df = pd.DataFrame({"k": [1, 2, 3], "v": [4, 5, 6]})
    spec = build_chart_spec(df=df, user_query="添加标题:Q1 销售")
    assert spec.config.title is not None
    assert "Q1 销售" in spec.config.title


# ---------------------------------------------------------------------------
# echarts_from_spec
# ---------------------------------------------------------------------------


def test_echarts_scatter_has_xy_axis():
    df = pd.DataFrame({"x": [1, 2, 3], "y": [4, 5, 6]})
    spec = build_chart_spec(df=df, user_query="")
    opt = echarts_from_spec(spec)
    assert opt is not None
    assert opt["series"][0]["type"] == "scatter"
    assert opt["xAxis"]["name"] == "x"
    assert opt["yAxis"]["name"] == "y"


def test_echarts_pie_emits_pie_series():
    df = pd.DataFrame({"k": ["A", "B", "C"], "v": [1, 2, 3]})
    spec = build_chart_spec(df=df, user_query="")
    opt = echarts_from_spec(spec)
    assert opt is not None
    assert opt["series"][0]["type"] == "pie"
    assert len(opt["series"][0]["data"]) == 3


def test_echarts_table_returns_none():
    spec = build_chart_spec(df=pd.DataFrame(), user_query="")
    assert echarts_from_spec(spec) is None
