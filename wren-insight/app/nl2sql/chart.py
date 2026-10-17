from __future__ import annotations

import math
import re
from datetime import date, datetime
from decimal import Decimal
from typing import Any

import pandas as pd

from ..models import ChartSpec

PALETTE = [
    "#4F7CFF", "#22C3A6", "#F5A524", "#F25F5C", "#8B5CF6",
    "#0EA5E9", "#84CC16", "#EC4899", "#14B8A6", "#F97316",
]

TIME_NAME = re.compile(r"(date|time|day|month|year|week|quarter|日期|时间|月|年|季度|周)", re.I)
TIME_VALUE = re.compile(r"^\d{4}[-/]\d{1,2}([-/]\d{1,2})?([ T]\d{1,2}:\d{2}(:\d{2})?)?$")


def _native(value: Any) -> Any:
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    if isinstance(value, (pd.Timestamp, datetime)):
        return value.isoformat(sep=" ")
    if isinstance(value, (date,)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if hasattr(value, "item"):
        try:
            return value.item()
        except (ValueError, AttributeError):
            pass
    return value


def dataframe_records(df: pd.DataFrame) -> list[dict[str, Any]]:
    records = []
    for row in df.to_dict(orient="records"):
        records.append({str(key): _native(value) for key, value in row.items()})
    return records


def _format_compact(value: Any) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    if math.isnan(number):
        return "-"
    sign = "-" if number < 0 else ""
    number = abs(number)
    if number >= 100_000_000:
        return f"{sign}{number / 100_000_000:.2f}亿"
    if number >= 10_000:
        return f"{sign}{number / 10_000:.2f}万"
    if number.is_integer():
        return f"{sign}{int(number):,}"
    return f"{sign}{number:,.2f}"


def _is_time_column(series: pd.Series, name: str) -> bool:
    if pd.api.types.is_datetime64_any_dtype(series):
        return True
    if TIME_NAME.search(name):
        return True
    sample = [v for v in series.dropna().head(5).tolist()]
    return bool(sample) and all(isinstance(v, str) and TIME_VALUE.match(v.strip()) for v in sample)


def _column_profile(df: pd.DataFrame) -> tuple[list[str], list[str], list[str]]:
    numeric: list[str] = []
    time_cols: list[str] = []
    categorical: list[str] = []
    for column in df.columns:
        series = df[column]
        name = str(column)
        if pd.api.types.is_numeric_dtype(series) and not pd.api.types.is_bool_dtype(series):
            numeric.append(name)
        elif _is_time_column(series, name):
            time_cols.append(name)
        elif not pd.api.types.is_numeric_dtype(series):
            distinct = series.dropna().astype(str).nunique()
            if distinct <= 60:
                categorical.append(name)
    return numeric, time_cols, categorical


def _resolve(name: str | None, columns: list[str]) -> str | None:
    if not name:
        return None
    for column in columns:
        if column == name:
            return column
    lower = name.lower()
    for column in columns:
        if column.lower() == lower:
            return column
    return None


def _label_series(series: pd.Series) -> list[str]:
    labels = []
    for value in series.tolist():
        native = _native(value)
        if isinstance(native, str):
            labels.append(native)
        elif native is None:
            labels.append("NULL")
        else:
            labels.append(str(native))
    return labels


def _base_option() -> dict[str, Any]:
    return {
        "backgroundColor": "transparent",
        "color": PALETTE,
        "textStyle": {"fontFamily": "Inter, 'PingFang SC', 'Microsoft YaHei', sans-serif"},
        "tooltip": {"trigger": "axis", "valueFormatter": "compactNumber", "axisPointer": {"type": "shadow"}},
        "legend": {"top": 0, "type": "scroll", "icon": "roundRect"},
        "grid": {"left": 18, "right": 28, "top": 48, "bottom": 18, "containLabel": True},
    }


def _category_axis(labels: list[str]) -> dict[str, Any]:
    return {
        "type": "category",
        "data": labels,
        "axisLabel": {"hideOverlap": True, "formatter": "categoryLabel", "color": "#6B7280"},
        "axisLine": {"lineStyle": {"color": "#E5E7EB"}},
        "axisTick": {"show": False},
    }


def _value_axis() -> dict[str, Any]:
    return {
        "type": "value",
        "axisLabel": {"formatter": "compactNumber", "color": "#6B7280"},
        "splitLine": {"lineStyle": {"color": "#EEF1F6", "type": "dashed"}},
    }


def _series_data(df: pd.DataFrame, y: str) -> list[Any]:
    return [_native(v) for v in df[y].tolist()]


def _line_or_bar(
    df: pd.DataFrame, chart_type: str, x: str, y_fields: list[str],
    series_field: str | None, horizontal: bool = False,
) -> dict[str, Any]:
    option = _base_option()
    x_values = _label_series(df[x])
    series = []

    pivot = None
    if series_field and len(y_fields) == 1:
        try:
            pivot = df.pivot_table(
                index=x, columns=series_field, values=y_fields[0],
                aggfunc="sum", sort=False,
            )
        except (ValueError, KeyError, TypeError):
            pivot = None

    if pivot is not None:
        x_values = [str(v) for v in pivot.index.tolist()]
        for column in pivot.columns:
            series.append({
                "name": str(column),
                "type": chart_type,
                "data": [_native(v) for v in pivot[column].tolist()],
                "smooth": chart_type == "line",
                "showSymbol": chart_type == "line" and len(x_values) <= 24,
                "barMaxWidth": 28,
                "emphasis": {"focus": "series"},
            })
    else:
        for y in y_fields[:4]:
            series.append({
                "name": y,
                "type": chart_type,
                "data": _series_data(df, y),
                "smooth": chart_type == "line",
                "showSymbol": chart_type == "line" and len(x_values) <= 24,
                "barMaxWidth": 28,
                "emphasis": {"focus": "series"},
            })

    option["legend"]["show"] = len(series) > 1
    if chart_type == "line":
        for item in series:
            item["lineStyle"] = {"width": 2.4}
            item["areaStyle"] = {"opacity": 0.06}
        option["tooltip"]["axisPointer"] = {"type": "line"}

    if horizontal:
        option["xAxis"] = _value_axis()
        option["yAxis"] = _category_axis(x_values)
    else:
        option["xAxis"] = _category_axis(x_values)
        option["yAxis"] = _value_axis()
    option["series"] = series
    return option


def _pie_option(df: pd.DataFrame, label: str, value: str) -> dict[str, Any]:
    working = df[[label, value]].dropna().copy()
    working[value] = pd.to_numeric(working[value], errors="coerce")
    working = working.dropna(subset=[value])
    working = working.groupby(label, as_index=False)[value].sum()
    working = working.sort_values(value, ascending=False)
    if len(working) > 9:
        head = working.head(8)
        other = working.iloc[8:][value].sum()
        working = pd.concat([
            head,
            pd.DataFrame([{label: "其他", value: other}]),
        ], ignore_index=True)
    data = [
        {"name": str(_native(row[label])), "value": _native(row[value])}
        for _, row in working.iterrows()
    ]
    return {
        "backgroundColor": "transparent",
        "color": PALETTE,
        "textStyle": {"fontFamily": "Inter, 'PingFang SC', 'Microsoft YaHei', sans-serif"},
        "tooltip": {"trigger": "item", "valueFormatter": "compactNumber"},
        "legend": {"top": 0, "type": "scroll", "icon": "roundRect"},
        "series": [
            {
                "name": value,
                "type": "pie",
                "radius": ["42%", "68%"],
                "center": ["50%", "56%"],
                "avoidLabelOverlap": True,
                "itemStyle": {"borderRadius": 6, "borderColor": "#fff", "borderWidth": 2},
                "label": {"formatter": "{b}\n{d}%", "color": "#4B5563"},
                "labelLine": {"length": 10, "length2": 8},
                "data": data,
            }
        ],
    }


def _scatter_option(df: pd.DataFrame, x: str, y: str) -> dict[str, Any]:
    points = []
    for _, row in df[[x, y]].dropna().iterrows():
        points.append([_native(row[x]), _native(row[y])])
    return {
        "backgroundColor": "transparent",
        "color": PALETTE,
        "textStyle": {"fontFamily": "Inter, 'PingFang SC', 'Microsoft YaHei', sans-serif"},
        "tooltip": {"trigger": "item", "valueFormatter": "compactNumber"},
        "grid": {"left": 24, "right": 28, "top": 32, "bottom": 24, "containLabel": True},
        "xAxis": {**_value_axis(), "name": x, "nameLocation": "middle", "nameGap": 28},
        "yAxis": {**_value_axis(), "name": y, "nameLocation": "middle", "nameGap": 40},
        "series": [{
            "type": "scatter",
            "symbolSize": 9,
            "itemStyle": {"opacity": 0.75},
            "data": points,
        }],
    }


def recommend_chart(
    df: pd.DataFrame,
    question: str,
    llm_chart: dict[str, Any] | None = None,
    override: str | None = None,
    title: str = "查询结果",
    kpi_label: str | None = None,
) -> ChartSpec:
    columns = [str(c) for c in df.columns]
    numeric, time_cols, categorical = _column_profile(df)
    llm_chart = llm_chart or {}
    raw_type = override or llm_chart.get("type")
    requested = str(raw_type).lower() if raw_type else None

    raw_x = llm_chart.get("x")
    x_name = raw_x if isinstance(raw_x, str) else None
    raw_y = llm_chart.get("y")
    if isinstance(raw_y, str):
        raw_y = [raw_y]
    if not isinstance(raw_y, list):
        raw_y = []
    raw_series = llm_chart.get("series")
    series_name = raw_series if isinstance(raw_series, str) else None

    x = _resolve(x_name, columns)
    y_fields = [r for r in (_resolve(str(v), columns) for v in raw_y) if r]
    y_fields = [v for v in y_fields if v in numeric]
    series_field = _resolve(series_name, columns)

    if not y_fields:
        y_fields = numeric[:3]
    if not x:
        if time_cols:
            x = time_cols[0]
        elif categorical:
            x = categorical[0]

    chart_type = requested if requested in {"line", "bar", "pie", "scatter", "kpi", "table"} else None

    if chart_type is None:
        if len(df) == 1 and len(numeric) >= 1:
            chart_type = "kpi"
        elif time_cols and numeric:
            chart_type = "line"
        elif categorical and numeric:
            if len(categorical) == 1 and df[categorical[0]].astype(str).nunique() <= 8 and re.search(r"占比|比例|构成|分布|份额|share|percent", question):
                chart_type = "pie"
            else:
                chart_type = "bar"
        elif len(numeric) >= 2 and not categorical:
            chart_type = "scatter"
        else:
            chart_type = "table"

    # Fallbacks that keep the chart renderable.
    if chart_type == "kpi" and (len(df) != 1 or not numeric):
        chart_type = "bar" if categorical and numeric else "table"
    if chart_type in {"line", "bar"} and (not x or not y_fields):
        chart_type = "table"
    if chart_type == "pie" and (not x or not y_fields):
        chart_type = "table"
    if chart_type == "scatter" and (len(numeric) < 2 or len(df) < 2):
        chart_type = "table"

    option: dict[str, Any] | None = None
    actual_x = x
    actual_y = y_fields
    reason = llm_chart.get("reason") or ""

    if chart_type == "kpi":
        value_field = y_fields[0]
        value = _native(df.iloc[0][value_field])
        option = {
            "kind": "kpi",
            "value": value,
            "formatted": _format_compact(value),
            "label": kpi_label or value_field,
        }
    elif chart_type == "line":
        if not time_cols and x in categorical:
            # A month-like category is fine; otherwise line still works.
            pass
        option = _line_or_bar(df, "line", x, y_fields, series_field)
    elif chart_type == "bar":
        distinct = df[x].astype(str).nunique()
        max_label = max((len(str(v)) for v in df[x].dropna().unique().tolist()), default=0)
        horizontal = distinct > 7 and max_label > 8
        option = _line_or_bar(df, "bar", x, y_fields, series_field, horizontal=horizontal)
    elif chart_type == "pie":
        option = _pie_option(df, x, y_fields[0])
    elif chart_type == "scatter":
        actual_x, actual_y = numeric[0], [numeric[1]]
        option = _scatter_option(df, actual_x, actual_y[0])

    return ChartSpec(
        type=chart_type,  # type: ignore[arg-type]
        title=title,
        x=actual_x if chart_type not in {"kpi", "table"} else (actual_x if chart_type == "kpi" else None),
        y=actual_y if chart_type not in {"table"} else [],
        series=series_field,
        option=option,
        reason=reason,
    )
