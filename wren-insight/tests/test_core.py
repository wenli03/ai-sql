from __future__ import annotations

from datetime import datetime
from decimal import Decimal

import pandas as pd

from app.models import Ontology, OntologyMetric
from app.nl2sql.chart import dataframe_records, recommend_chart
from app.nl2sql.llm import extract_json
from app.nl2sql.pipeline import check_metric_alignment, validate_sql

MANIFEST = {
    "models": [
        {"name": "orders", "tableReference": {"table": "orders"}},
        {"name": "customers", "tableReference": {"table": "customers"}},
    ],
    "views": [{"name": "sales_line"}],
}


def test_extract_json_handles_fences():
    assert extract_json('```json\n{"sql": "SELECT 1"}\n```')["sql"] == "SELECT 1"
    assert extract_json('prefix {"sql": "SELECT 2"} suffix')["sql"] == "SELECT 2"


def test_validate_sql_allows_models_and_cte():
    sql = "WITH x AS (SELECT COUNT(*) AS n FROM orders) SELECT n FROM x"
    clean, warnings = validate_sql(sql, "postgres", MANIFEST)
    assert "WITH" in clean
    assert warnings == []


def test_validate_sql_allows_views():
    clean, _ = validate_sql("SELECT category, SUM(revenue) FROM sales_line GROUP BY 1", "postgres", MANIFEST)
    assert "sales_line" in clean


def test_validate_sql_rejects_unknown_table():
    try:
        validate_sql("SELECT * FROM secret_table", "postgres", MANIFEST)
    except Exception as exc:
        assert "secret_table" in str(exc)
    else:
        raise AssertionError("unknown table should be rejected")


def test_validate_sql_rejects_writes_and_multi_statements():
    for sql in [
        "UPDATE orders SET total_amount = 0",
        "DELETE FROM orders",
        "SELECT 1; DROP TABLE orders",
    ]:
        try:
            validate_sql(sql, "postgres", MANIFEST)
        except Exception:
            continue
        raise AssertionError(f"should reject: {sql}")


def _metric_ontology() -> Ontology:
    return Ontology(
        metrics=[
            OntologyMetric(
                name="客单价",
                label="客单价",
                synonyms=["客单价", "AOV"],
                expression="SUM(orders.total_amount) / NULLIF(COUNT(DISTINCT orders.id), 0)",
                filters="orders.status NOT IN ('cancelled', 'refunded')",
            ),
            OntologyMetric(
                name="高价值客户数",
                label="高价值客户数",
                synonyms=["高价值客户"],
                expression="COUNT(DISTINCT customers.id)",
            ),
        ]
    )


def test_metric_alignment_catches_missing_division():
    ontology = _metric_ontology()
    issues = check_metric_alignment(
        "SELECT SUM(orders.total_amount) AS aov FROM orders WHERE orders.status NOT IN ('cancelled','refunded')",
        "各渠道的客单价",
        ontology,
    )
    assert any("除法" in issue for issue in issues)


def test_metric_alignment_catches_missing_filter():
    ontology = _metric_ontology()
    issues = check_metric_alignment(
        "SELECT SUM(orders.total_amount) / NULLIF(COUNT(DISTINCT orders.id),0) AS aov FROM orders",
        "各渠道的客单价",
        ontology,
    )
    assert any("过滤条件" in issue for issue in issues)


def test_metric_alignment_allows_detail_list():
    ontology = _metric_ontology()
    issues = check_metric_alignment(
        "SELECT customers.id, customers.name FROM customers LIMIT 100",
        "高价值客户有哪些",
        ontology,
    )
    assert issues == []


def test_chart_line():
    df = pd.DataFrame({"month": ["2026-01", "2026-02"], "revenue": [10.0, 20.0]})
    chart = recommend_chart(df, "每月的销售额趋势")
    assert chart.type == "line"
    assert chart.option and len(chart.option["series"]) == 1


def test_chart_bar_and_pie():
    df = pd.DataFrame({"category": ["A", "B", "C"], "revenue": [30.0, 20.0, 10.0]})
    assert recommend_chart(df, "各品类销售额排行").type == "bar"
    assert recommend_chart(df, "各品类销售额占比").type == "pie"


def test_chart_kpi():
    df = pd.DataFrame({"total_revenue": [123.45]})
    chart = recommend_chart(df, "总销售额是多少", kpi_label="销售额")
    assert chart.type == "kpi"
    assert chart.option["label"] == "销售额"
    assert chart.option["formatted"] == "123.45"


def test_chart_scatter_requires_multiple_points():
    df = pd.DataFrame({"revenue": [10.0], "orders": [2]})
    chart = recommend_chart(
        df, "销售额和订单数的关系", {"type": "scatter", "x": "revenue", "y": ["orders"]}
    )
    assert chart.type == "table"
    df2 = pd.DataFrame({"revenue": [10.0, 20.0, 30.0], "orders": [1, 2, 3]})
    assert recommend_chart(df2, "销售额和订单数的关系").type == "scatter"


def test_chart_multi_series_pivot():
    df = pd.DataFrame({
        "month": ["2026-01", "2026-01", "2026-02", "2026-02"],
        "channel": ["app", "web", "app", "web"],
        "revenue": [10.0, 5.0, 20.0, 8.0],
    })
    chart = recommend_chart(df, "各渠道销售额趋势", {"type": "line", "x": "month", "y": ["revenue"], "series": "channel"})
    assert chart.type == "line"
    assert len(chart.option["series"]) == 2


def test_dataframe_records_json_safe():
    df = pd.DataFrame({
        "amount": [Decimal("12.30"), None],
        "at": [datetime(2026, 1, 1, 12, 0), None],
        "note": ["ok", None],
    })
    records = dataframe_records(df)
    assert records[0]["amount"] == 12.3
    assert records[0]["at"].startswith("2026-01-01")
    assert records[1]["note"] is None
