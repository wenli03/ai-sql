from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from ..models import ColumnInfo, ConnectionConfig, ScannedSchema, TableInfo
from .base import VectorError
from .factory import get_adapter

ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}([T ]\d{2}:\d{2}(:\d{2})?)?")


def _stringify(value: Any, max_len: int = 120) -> str:
    if value is None:
        return ""
    text = str(value).strip().replace("\n", " ")
    return text[:max_len]


def _is_iso_date(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    if not ISO_DATE.match(value.strip()):
        return False
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
        return True
    except ValueError:
        return False


def _infer_columns(collection_name: str, records: list[dict[str, Any]], vector_field: str, dimension: int | None, metric: str) -> list[ColumnInfo]:
    values: dict[str, list[Any]] = {}
    for record in records:
        for key, value in record.items():
            if key in ("id", "document", "score", "distance"):
                continue
            values.setdefault(str(key), []).append(value)
    columns: list[ColumnInfo] = [
        ColumnInfo(name="id", data_type="VARCHAR", mdl_type="VARCHAR", nullable=False, is_primary_key=True, role="key", comment="记录 ID")
    ]
    for name, items in values.items():
        non_null = [item for item in items if item is not None]
        if non_null and all(isinstance(item, bool) for item in non_null):
            mdl_type = "BOOLEAN"
        elif non_null and all(isinstance(item, int) and not isinstance(item, bool) for item in non_null):
            mdl_type = "INTEGER"
        elif non_null and all(isinstance(item, (int, float)) and not isinstance(item, bool) for item in non_null):
            mdl_type = "DOUBLE"
        elif non_null and all(_is_iso_date(item) for item in non_null):
            mdl_type = "TIMESTAMP"
        else:
            mdl_type = "VARCHAR"
        lower = name.lower()
        if mdl_type == "TIMESTAMP" or any(token in lower for token in ("date", "time", "created", "updated", "_at")):
            role = "time"
            if mdl_type == "VARCHAR":
                mdl_type = "TIMESTAMP"
        elif mdl_type in ("INTEGER", "DOUBLE"):
            role = "measure"
        else:
            role = "dimension"
        sample_values = []
        for item in non_null:
            text = _stringify(item)
            if text and text not in sample_values:
                sample_values.append(text)
            if len(sample_values) >= 12:
                break
        columns.append(
            ColumnInfo(
                name=name,
                data_type=mdl_type,
                mdl_type=mdl_type,
                nullable=len(non_null) < len(items),
                role=role,
                sample_values=sample_values,
                comment=None,
            )
        )
    if any("document" in record for record in records):
        snippets = [_stringify(record.get("document"), 200) for record in records[:3] if record.get("document")]
        columns.append(
            ColumnInfo(
                name="document",
                data_type="VARCHAR",
                mdl_type="VARCHAR",
                role="dimension",
                sample_values=snippets,
                comment=None,
            )
        )
    columns.append(
        ColumnInfo(
            name=vector_field or "vector",
            data_type=f"vector({dimension or '?'})",
            mdl_type="VECTOR",
            role="other",
            comment=f"向量字段 · {metric} · {dimension or '?'} 维",
        )
    )
    return columns


def scan_vector(conn: ConnectionConfig) -> ScannedSchema:
    adapter = get_adapter(conn)
    try:
        names = adapter.list_collections()
        if not names:
            raise VectorError("向量库中没有可用的 collection / class")
        tables: list[TableInfo] = []
        for name in names[:50]:
            collection = adapter.describe(name)
            records = collection.sample or adapter.scan(name, max_records=20)
            columns = _infer_columns(
                name, records, collection.vector_field or "vector",
                collection.dimension, collection.metric,
            )
            tables.append(
                TableInfo(
                    name=name,
                    schema_name="",
                    catalog=None,
                    comment=(
                        f"向量集合 {name}：{collection.count} 条记录，"
                        f"{collection.dimension or '?'} 维，{collection.metric} 距离。"
                    ),
                    row_count=collection.count,
                    row_count_estimated=False,
                    table_type="COLLECTION",
                    location=collection.location,
                    vector_field=collection.vector_field,
                    vector_dimension=collection.dimension,
                    vector_metric=collection.metric,
                    columns=columns,
                    primary_key=["id"],
                )
            )
        return ScannedSchema(
            dialect=conn.dialect,
            database=conn.host or "",
            catalog=None,
            schema_name="",
            tables=tables,
        )
    finally:
        adapter.close()
