from __future__ import annotations

import pytest

from app.db.introspect import _lake_md_type
from app.models import ColumnInfo, ConnectionConfig, ScannedSchema, TableInfo
from app.provenance import (
    _check_status_filter,
    _lineage_edges,
    _output_expressions,
    _source_refs,
    _used_model_names,
)
from app.sources import SOURCE_TYPES


def test_source_types_include_data_lake_products():
    ids = {item["id"] for item in SOURCE_TYPES}
    assert {"postgres", "mysql", "trino", "databricks", "athena", "duckdb", "clickhouse", "bigquery", "spark"} <= ids
    trino = next(item for item in SOURCE_TYPES if item["id"] == "trino")
    assert any(field["key"] == "options.catalog" for field in trino["fields"])


@pytest.mark.parametrize(
    "dialect,options,expected",
    [
        ("trino", {"catalog": "iceberg"}, "iceberg"),
        ("databricks", {"http_path": "/sql/1.0/warehouses/x", "catalog": "main"}, "main"),
        ("athena", {"s3_staging_dir": "s3://bucket/staging/"}, "default"),
        ("duckdb", {"format": "duckdb", "catalog": "lakehouse"}, "lakehouse"),
        ("clickhouse", {}, "analytics"),
        ("bigquery", {"project_id": "my-project"}, "my-project"),
        ("spark", {}, "default"),
    ],
)
def test_connection_info_round_trip(dialect, options, expected):
    from wren.model.data_source import DataSource

    conn = ConnectionConfig(
        id="test",
        name="test",
        dialect=dialect,
        host="127.0.0.1",
        port=0,
        database="analytics",
        user="demo",
        password="secret" if dialect == "databricks" else "",
        schema_name="default" if dialect != "duckdb" else "main",
        options=options,
    )
    info = conn.wren_connection_info()
    typed = DataSource(dialect).get_connection_info(info)
    if dialect == "trino":
        assert typed.catalog == expected
    elif dialect == "databricks":
        assert typed.catalog == expected
    elif dialect == "duckdb":
        assert typed.format == "duckdb"
        assert typed.url == "analytics"
    elif dialect == "athena":
        assert typed.schema_name == "default"
    elif dialect == "clickhouse":
        assert typed.database == "analytics"
    elif dialect == "bigquery":
        assert typed.project_id == expected


@pytest.mark.parametrize(
    "source,raw,expected",
    [
        ("trino", "INTEGER", "INTEGER"),
        ("trino", "DECIMAL(18,2)", "DECIMAL"),
        ("trino", "timestamp(3) with time zone", "TIMESTAMP"),
        ("databricks", "STRING", "VARCHAR"),
        ("databricks", "TIMESTAMP_NTZ", "TIMESTAMP"),
        ("athena", "array(varchar)", "VARCHAR"),
        ("duckdb", "BIGINT", "BIGINT"),
        ("clickhouse", "Nullable(Decimal(18,4))", "DECIMAL"),
        ("clickhouse", "DateTime64(3)", "TIMESTAMP"),
        ("bigquery", "INT64", "BIGINT"),
        ("bigquery", "FLOAT64", "DOUBLE"),
        ("spark", "STRUCT<a:int>", "VARCHAR"),
    ],
)
def test_lake_type_mapping(source, raw, expected):
    assert _lake_md_type(source, raw) == expected


def _scan() -> ScannedSchema:
    return ScannedSchema(
        dialect="duckdb",
        database="lakehouse",
        catalog="lakehouse",
        schema_name="main",
        tables=[
            TableInfo(
                name="orders",
                schema_name="main",
                catalog="lakehouse",
                table_type="TABLE",
                columns=[
                    ColumnInfo(name="id", data_type="INTEGER", mdl_type="INTEGER", is_primary_key=True),
                    ColumnInfo(name="order_date", data_type="TIMESTAMP", mdl_type="TIMESTAMP", role="time"),
                    ColumnInfo(name="total_amount", data_type="DECIMAL", mdl_type="DECIMAL", role="measure"),
                    ColumnInfo(name="status", data_type="VARCHAR", mdl_type="VARCHAR", role="dimension"),
                ],
            )
        ],
    )


def _manifest() -> dict:
    return {
        "models": [
            {
                "name": "orders",
                "tableReference": {"catalog": "lakehouse", "schema": "main", "table": "orders"},
            }
        ],
        "views": [],
    }


def test_used_models_and_source_refs():
    sql = "SELECT order_date, total_amount FROM orders"
    names = _used_model_names(sql, "duckdb", _manifest())
    assert names == ["orders"]
    refs, views = _source_refs(names, _manifest(), _scan())
    assert views == []
    assert refs[0].catalog == "lakehouse"
    assert refs[0].table == "orders"


def test_output_expressions():
    expressions = _output_expressions(
        "SELECT order_date AS month, SUM(total_amount) AS revenue FROM orders GROUP BY 1",
        "duckdb",
    )
    assert expressions["month"] == "order_date"
    assert "SUM(total_amount)" in expressions["revenue"]


def test_column_lineage_on_planned_sql():
    planned = (
        'WITH orders AS (SELECT __source.order_date AS order_date, '
        '__source.total_amount AS total_amount FROM lakehouse."main".orders AS __source) '
        "SELECT order_date AS month, SUM(total_amount) AS revenue FROM orders GROUP BY 1"
    )
    edges = _lineage_edges(
        ["month", "revenue"],
        planned,
        _scan(),
        "duckdb",
        {"month": "order_date", "revenue": "SUM(total_amount)"},
    )
    pairs = {(edge.output_column, edge.source_table, edge.source_column) for edge in edges}
    assert ("month", "orders", "order_date") in pairs
    assert ("revenue", "orders", "total_amount") in pairs


def test_status_filter_check():
    metrics = [
        {
            "label": "销售额",
            "filters": "orders.status NOT IN ('cancelled', 'refunded')",
        }
    ]
    passing = _check_status_filter(
        "SELECT SUM(total_amount) FROM orders WHERE orders.status NOT IN ('cancelled','refunded')",
        metrics,
    )
    assert passing.status == "pass"
    warning = _check_status_filter("SELECT SUM(total_amount) FROM orders", metrics)
    assert warning.status == "warn"
