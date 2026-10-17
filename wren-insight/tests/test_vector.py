from __future__ import annotations

import pytest

from app.core.dialects import sqlglot_dialect
from app.db.introspect import _mssql_md_type, _oracle_md_type
from app.models import ColumnInfo, ConnectionConfig, ScannedSchema, TableInfo
from app.semantic.ontology import build_ontology
from app.sources import SOURCE_TYPES
from app.vector.chroma import ChromaAdapter
from app.vector.planner import _hybrid_rerank, aggregate_records, validate_vector_plan


def _vector_scan() -> ScannedSchema:
    table = TableInfo(
        name="knowledge_docs",
        schema_name="",
        table_type="COLLECTION",
        row_count=3,
        vector_field="embedding",
        vector_dimension=3,
        vector_metric="cosine",
        columns=[
            ColumnInfo(name="id", data_type="VARCHAR", mdl_type="VARCHAR", role="key", is_primary_key=True),
            ColumnInfo(name="category", data_type="VARCHAR", mdl_type="VARCHAR", role="dimension", sample_values=["售后", "技术"]),
            ColumnInfo(name="priority", data_type="INTEGER", mdl_type="INTEGER", role="measure", sample_values=["5", "3"]),
            ColumnInfo(name="created_at", data_type="TIMESTAMP", mdl_type="TIMESTAMP", role="time"),
            ColumnInfo(name="document", data_type="VARCHAR", mdl_type="VARCHAR", role="dimension"),
            ColumnInfo(name="embedding", data_type="vector(3)", mdl_type="VECTOR", role="other"),
        ],
    )
    return ScannedSchema(dialect="chroma", database="http://localhost:8001", schema_name="", tables=[table])


def test_sources_include_enterprise_and_vectors():
    ids = {item["id"] for item in SOURCE_TYPES}
    assert {"oracle", "mssql", "qdrant", "chroma", "weaviate"} <= ids


def test_vector_ontology_has_vector_domain():
    ontology = build_ontology(_vector_scan())
    assert ontology.domain == "知识库 / 向量检索"
    assert any(m.name == "记录数" for m in ontology.metrics)
    assert any(d.name == "category" for d in ontology.dimensions)
    assert any("top_k" in rule for rule in ontology.rules)


def test_validate_vector_plan_accepts_aggregate():
    plan = {
        "mode": "aggregate",
        "collection": "knowledge_docs",
        "group_by": ["category"],
        "metrics": [{"op": "count", "field": "*", "as": "doc_count"}],
        "chart": {"type": "bar", "x": "category", "y": ["doc_count"]},
    }
    cleaned = validate_vector_plan(plan, _vector_scan())
    assert cleaned["group_by"] == ["category"]
    assert cleaned["metrics"][0]["as"] == "doc_count"


def test_validate_vector_plan_rejects_bad_collection_and_top_k():
    with pytest.raises(Exception):
        validate_vector_plan({"mode": "aggregate", "collection": "missing"}, _vector_scan())
    with pytest.raises(Exception):
        validate_vector_plan(
            {
                "mode": "search",
                "collection": "knowledge_docs",
                "search": {"text": "退款", "top_k": 999},
            },
            _vector_scan(),
        )


def test_aggregate_records_group_count_and_average():
    records = [
        {"id": "1", "category": "售后", "priority": 5},
        {"id": "2", "category": "售后", "priority": 3},
        {"id": "3", "category": "技术", "priority": 1},
    ]
    plan = {
        "group_by": ["category"],
        "metrics": [
            {"op": "count", "field": "*", "as": "doc_count"},
            {"op": "avg", "field": "priority", "as": "avg_priority"},
        ],
        "order_by": [{"field": "doc_count", "direction": "desc"}],
        "limit": 10,
    }
    df = aggregate_records(records, plan)
    assert list(df["category"]) == ["售后", "技术"]
    assert df.iloc[0]["doc_count"] == 2
    assert df.iloc[0]["avg_priority"] == 4.0


def test_hybrid_rerank_prefers_keyword_match():
    records = [
        {"id": "holiday", "title": "假期政策", "document": "holiday policy", "distance": 0.24, "score": 0.76},
        {"id": "refund", "title": "退款政策与售后处理规范", "document": "退款到账时间 1-3 天", "distance": 0.26, "score": 0.74},
    ]
    ranked = _hybrid_rerank(records, "退款政策", 2)
    assert ranked[0]["id"] == "refund"


def test_chroma_filter_translation():
    adapter = ChromaAdapter(
        ConnectionConfig(
            id="c", name="c", dialect="chroma", host="http://localhost:8001",
            port=8001, database="", user="", password="", schema_name="",
            options={"collection": "knowledge_docs"},
        )
    )
    where, document_where = adapter._where([
        {"field": "category", "op": "eq", "value": "售后"},
        {"field": "priority", "op": "gte", "value": 3},
    ])
    assert where and "$and" in where
    assert document_where is None


def test_mssql_dialect_mapping():
    assert sqlglot_dialect("mssql") == "tsql"
    assert sqlglot_dialect("oracle") == "oracle"


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("NUMBER", "DECIMAL"),
        ("VARCHAR2", "VARCHAR"),
        ("DATE", "TIMESTAMP"),
        ("TIMESTAMP(6)", "TIMESTAMP"),
        ("CLOB", "VARCHAR"),
    ],
)
def test_oracle_type_mapping(raw, expected):
    assert _oracle_md_type(raw) == expected


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("int", "INTEGER"),
        ("bigint", "BIGINT"),
        ("decimal", "DECIMAL"),
        ("nvarchar", "VARCHAR"),
        ("datetime2", "TIMESTAMP"),
        ("bit", "BOOLEAN"),
    ],
)
def test_mssql_type_mapping(raw, expected):
    assert _mssql_md_type(raw) == expected


def test_oracle_and_mssql_connection_info():
    from wren.model.data_source import DataSource

    oracle = ConnectionConfig(
        id="o", name="o", dialect="oracle", host="127.0.0.1", port=1521,
        database="FREEPDB1", user="SYSTEM", password="secret", schema_name="SYSTEM",
    )
    typed = DataSource("oracle").get_connection_info(oracle.wren_connection_info())
    assert typed.database == "FREEPDB1"

    mssql = ConnectionConfig(
        id="m", name="m", dialect="mssql", host="127.0.0.1", port=1433,
        database="demo", user="sa", password="secret", schema_name="dbo",
        options={"driver": "SQL Server", "Encrypt": "no", "TrustServerCertificate": "yes"},
    )
    info = mssql.wren_connection_info()
    assert info["driver"] == "SQL Server"
    typed = DataSource("mssql").get_connection_info(info)
    assert typed.database == "demo"
