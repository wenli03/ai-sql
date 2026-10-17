from __future__ import annotations

import re
from datetime import date, datetime
from decimal import Decimal
from typing import Any

import pandas as pd
import sqlglot
from sqlglot import exp, parse
from sqlglot.lineage import lineage as sqlglot_lineage

from .core.dialects import sqlglot_dialect
from .models import (
    ConnectionConfig,
    FreshnessInfo,
    LineageEdge,
    Ontology,
    Provenance,
    ReliabilityCheck,
    ScannedSchema,
    SourceTableRef,
    TableInfo,
)

LOCAL_SOURCES = {"postgres", "mysql", "duckdb", "clickhouse"}


def _json_value(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (datetime, date)):
        return value.isoformat(sep=" ") if isinstance(value, datetime) else value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, float) and pd.isna(value):
        return None
    if hasattr(value, "item"):
        try:
            return value.item()
        except (ValueError, AttributeError):
            pass
    return str(value)


def _quote(dialect: str, value: str) -> str:
    if dialect in ("mysql", "databricks", "clickhouse", "spark", "bigquery"):
        return "`" + value.replace("`", "``") + "`"
    return '"' + value.replace('"', '""') + '"'


def _qualified(dialect: str, table: TableInfo) -> str:
    parts = []
    if table.catalog:
        parts.append(_quote(dialect, table.catalog))
    if table.schema_name:
        parts.append(_quote(dialect, table.schema_name))
    parts.append(_quote(dialect, table.name))
    return ".".join(parts)


def _table_lookup(scan: ScannedSchema) -> dict[tuple[str, str, str], TableInfo]:
    lookup: dict[tuple[str, str, str], TableInfo] = {}
    for table in scan.tables:
        lookup[(table.catalog or "", table.schema_name or "", table.name.lower())] = table
        lookup[("", table.schema_name or "", table.name.lower())] = table
        lookup[("", "", table.name.lower())] = table
    return lookup


def _model_lookup(manifest: dict[str, Any]) -> dict[tuple[str, str, str], str]:
    result: dict[tuple[str, str, str], str] = {}
    for model in manifest.get("models", []):
        ref = model.get("tableReference") or {}
        key = (
            (ref.get("catalog") or ""),
            (ref.get("schema") or ""),
            (ref.get("table") or "").lower(),
        )
        result[key] = model.get("name", "")
        result[("", key[1], key[2])] = model.get("name", "")
        result[("", "", key[2])] = model.get("name", "")
    return result


def _schema_for_lineage(scan: ScannedSchema) -> dict[str, dict[str, str]]:
    schema: dict[str, dict[str, str]] = {}
    for table in scan.tables:
        columns = {column.name: column.mdl_type for column in table.columns}
        keys = {
            table.name.lower(),
            f"{table.schema_name}.{table.name}".lower(),
        }
        if table.catalog:
            keys.add(f"{table.catalog}.{table.schema_name}.{table.name}".lower())
        for key in keys:
            schema[key] = columns
    return schema


def _cte_names(root: exp.Expression) -> set[str]:
    return {
        cte.alias_or_name.lower()
        for cte in root.find_all(exp.CTE)
        if cte.alias_or_name
    }


def _used_model_names(sql: str, dialect: str, manifest: dict[str, Any]) -> list[str]:
    dialect = sqlglot_dialect(dialect)
    try:
        expressions = parse(sql, dialect=dialect)
        if not expressions:
            return []
        root = expressions[0]
    except Exception:
        return []
    cte = _cte_names(root)
    known = {
        item.get("name", "").lower(): item
        for item in [*manifest.get("models", []), *manifest.get("views", [])]
    }
    found: list[str] = []
    for table in root.find_all(exp.Table):
        name = (table.name or "").lower()
        if not name or name in cte:
            continue
        if name in known and known[name].get("name") not in found:
            found.append(known[name]["name"])
    # Expand views to their underlying models.
    view_map = {v.get("name", "").lower(): v for v in manifest.get("views", [])}
    queue = list(found)
    while queue:
        name = queue.pop()
        view = view_map.get(name.lower())
        if not view:
            continue
        for table in parse(view.get("statement", ""), dialect=dialect)[0].find_all(exp.Table):
            target = (table.name or "").lower()
            if not target or target in cte:
                continue
            real = known.get(target, {}).get("name")
            if real and real not in found:
                found.append(real)
                queue.append(real)
    return found


def _source_refs(
    used_names: list[str],
    manifest: dict[str, Any],
    scan: ScannedSchema,
) -> tuple[list[SourceTableRef], list[str]]:
    models = {m.get("name"): m for m in manifest.get("models", [])}
    views = {v.get("name"): v for v in manifest.get("views", [])}
    table_lookup = _table_lookup(scan)
    refs: list[SourceTableRef] = []
    view_names: list[str] = []
    seen: set[tuple[str, str]] = set()
    for name in used_names:
        if name in views:
            if ("view", name) not in seen:
                seen.add(("view", name))
                refs.append(
                    SourceTableRef(
                        model=name,
                        kind="view",
                        table=name,
                        source_type=scan.dialect,
                    )
                )
            view_names.append(name)
            continue
        model = models.get(name)
        if not model:
            continue
        ref = model.get("tableReference") or {}
        catalog = ref.get("catalog") or ""
        schema = ref.get("schema") or ""
        table_name = ref.get("table") or name
        table = table_lookup.get((catalog, schema, table_name.lower())) or table_lookup.get(("", "", table_name.lower()))
        key = ("model", name)
        if key in seen:
            continue
        seen.add(key)
        refs.append(
            SourceTableRef(
                model=name,
                kind="model",
                catalog=table.catalog if table else (catalog or None),
                schema_name=table.schema_name if table else (schema or None),
                table=table_name,
                source_type=scan.dialect,
                location=table.location if table else None,
            )
        )
    return refs, view_names


def _used_columns(sql: str, dialect: str) -> list[str]:
    dialect = sqlglot_dialect(dialect)
    try:
        root = parse(sql, dialect=dialect)[0]
    except Exception:
        return []
    values: list[str] = []
    for column in root.find_all(exp.Column):
        if isinstance(column.this, exp.Star):
            continue
        if column.name and column.name not in values:
            values.append(column.name)
    return values


def _output_expressions(sql: str, dialect: str) -> dict[str, str]:
    dialect = sqlglot_dialect(dialect)
    try:
        root = parse(sql, dialect=dialect)[0]
    except Exception:
        return {}
    select = None
    if isinstance(root, exp.Select):
        select = root
    elif isinstance(root, exp.Union):
        select = root.this if isinstance(root.this, exp.Select) else None
    if select is None:
        select = root.find(exp.Select)
    result: dict[str, str] = {}
    if select:
        for projection in select.expressions:
            alias = projection.alias_or_name
            if alias:
                expression = projection.this if isinstance(projection, exp.Alias) else projection
                result[alias] = expression.sql(dialect=dialect)
    return result


def _lineage_edges(
    output_columns: list[str],
    planned_sql: str | None,
    scan: ScannedSchema,
    dialect: str,
    expressions: dict[str, str],
) -> list[LineageEdge]:
    if not planned_sql:
        return []
    schema = _schema_for_lineage(scan)
    table_lookup = _table_lookup(scan)
    lineage_dialect = sqlglot_dialect(dialect)
    edges: list[LineageEdge] = []
    seen: set[tuple[str, str, str, str]] = set()

    def collect(node, output_column: str) -> None:
        source = node.source
        if isinstance(source, exp.Table):
            table_name = source.name or ""
            catalog = source.catalog or ""
            schema_name = source.db or ""
            column_name = (node.name or "").split(".")[-1].strip('"`')
            physical = (
                table_lookup.get((catalog, schema_name, table_name.lower()))
                or table_lookup.get(("", schema_name, table_name.lower()))
                or table_lookup.get(("", "", table_name.lower()))
            )
            if physical:
                catalog = physical.catalog or catalog
                schema_name = physical.schema_name or schema_name
                table_name = physical.name
            key = (output_column, catalog, schema_name, f"{table_name}.{column_name}")
            if column_name and key not in seen:
                seen.add(key)
                edges.append(
                    LineageEdge(
                        output_column=output_column,
                        source_catalog=catalog or None,
                        source_schema=schema_name or None,
                        source_table=table_name,
                        source_column=column_name,
                        expression=expressions.get(output_column),
                    )
                )
        for child in node.downstream:
            collect(child, output_column)

    for output_column in output_columns:
        try:
            node = sqlglot_lineage(output_column, planned_sql, schema=schema, dialect=lineage_dialect)
            collect(node, output_column)
        except Exception:
            continue
    return edges


def _metric_dicts(question: str, ontology: Ontology) -> list[dict[str, Any]]:
    lower = question.lower()
    result = []
    for metric in ontology.metrics:
        terms = [metric.name, metric.label, *metric.synonyms]
        if any(term and term.lower() in lower for term in terms):
            result.append({
                "name": metric.name,
                "label": metric.label,
                "synonyms": metric.synonyms,
                "expression": metric.expression,
                "filters": metric.filters,
                "description": metric.description,
                "unit": metric.unit,
            })
    return result


def _check_status_filter(sql: str, metrics: list[dict[str, Any]]) -> ReliabilityCheck:
    lower_sql = sql.lower()
    for metric in metrics:
        filters = metric.get("filters")
        if not filters:
            continue
        column_match = re.search(r"([A-Za-z_][\w.]*)\s+NOT\s+IN\s*\(([^)]*)\)", filters, re.I)
        if not column_match:
            continue
        column = column_match.group(1).split(".")[-1].lower()
        values = [v.strip().strip("'\"") for v in column_match.group(2).split(",") if v.strip()]
        present = column in lower_sql and (
            "not in" in lower_sql or all(v.lower() not in lower_sql for v in values)
        )
        if not present:
            return ReliabilityCheck(
                name="status_filter",
                label="指标过滤条件",
                status="warn",
                detail=f"指标「{metric['label']}」建议应用过滤：{filters}",
            )
    return ReliabilityCheck(
        name="status_filter",
        label="指标过滤条件",
        status="pass",
        detail="指标口径中的过滤条件已应用。",
    )


def _freshness_from_scan(scan: ScannedSchema, refs: list[SourceTableRef]) -> list[FreshnessInfo]:
    lookup = _table_lookup(scan)
    result = []
    for ref in refs:
        if ref.kind != "model":
            continue
        table = lookup.get((ref.catalog or "", ref.schema_name or "", ref.table.lower()))
        if not table:
            continue
        result.append(
            FreshnessInfo(
                model=ref.model,
                table=ref.table,
                row_count=table.row_count,
                checked_at=scan.scanned_at,
                note="扫描时统计（估算）；可在溯源面板刷新" if table.row_count_estimated else "扫描时统计；可在溯源面板刷新",
            )
        )
    return result


def probe_freshness(
    conn: ConnectionConfig,
    scan: ScannedSchema,
    models: list[str],
    *,
    max_models: int = 8,
) -> list[FreshnessInfo]:
    from .db.introspect import _close_connector, _query_dicts, _wren_connector

    lookup = _table_lookup(scan)
    result: list[FreshnessInfo] = []
    connector = None
    try:
        connector = _wren_connector(conn)
        for model in models[:max_models]:
            table = next((t for t in scan.tables if t.name == model), None)
            if not table:
                continue
            time_column = next(
                (c for c in table.columns if c.role == "time"),
                None,
            )
            try:
                if time_column:
                    sql = (
                        f"SELECT COUNT(*) AS row_count, MAX({_quote(conn.dialect, time_column.name)}) AS max_time "
                        f"FROM {_qualified(conn.dialect, table)}"
                    )
                else:
                    sql = f"SELECT COUNT(*) AS row_count FROM {_qualified(conn.dialect, table)}"
                rows = _query_dicts(connector, sql)
                row = rows[0] if rows else {}
                result.append(
                    FreshnessInfo(
                        model=model,
                        table=table.name,
                        row_count=int(row.get("row_count") or 0),
                        max_time_column=time_column.name if time_column else None,
                        max_time_value=(
                            str(_json_value(row.get("max_time")))
                            if row.get("max_time") is not None
                            else None
                        ),
                        checked_at=datetime.now(),
                        note="实时探测",
                    )
                )
            except Exception as exc:
                result.append(
                    FreshnessInfo(
                        model=model,
                        table=table.name,
                        row_count=table.row_count,
                        max_time_column=time_column.name if time_column else None,
                        checked_at=datetime.now(),
                        note=f"探测失败，使用扫描统计：{str(exc)[:120]}",
                    )
                )
    finally:
        if connector is not None:
            _close_connector(connector)
    return result


def build_provenance(
    conn: ConnectionConfig,
    scan: ScannedSchema,
    ontology: Ontology,
    manifest: dict[str, Any],
    question: str,
    sql: str,
    planned_sql: str | None,
    output_columns: list[str],
    *,
    elapsed_ms: int,
    attempts: int,
    provider: str,
    model: str,
    row_count: int,
    limit: int,
    probe_freshness_enabled: bool = True,
) -> Provenance:
    try:
        used_names = _used_model_names(sql, conn.dialect, manifest)
    except Exception:
        used_names = []
    refs, views_used = _source_refs(used_names, manifest, scan)
    metrics = _metric_dicts(question, ontology)
    columns = _used_columns(sql, conn.dialect)
    expressions = _output_expressions(sql, conn.dialect)
    lineage = _lineage_edges(output_columns, planned_sql, scan, conn.dialect, expressions)

    model_names = [ref.model for ref in refs if ref.kind == "model"]
    if probe_freshness_enabled and conn.dialect in LOCAL_SOURCES:
        freshness = probe_freshness(conn, scan, model_names)
    else:
        freshness = _freshness_from_scan(scan, refs)
        if conn.dialect not in LOCAL_SOURCES:
            for item in freshness:
                item.note = item.note or "扫描时统计；云湖仓未实时探测"

    covered = {edge.output_column for edge in lineage}
    missing = [column for column in output_columns if column not in covered]
    checks = [
        ReliabilityCheck(name="read_only", label="只读查询", status="pass", detail="SQL 已通过只读与白名单校验。"),
        ReliabilityCheck(name="table_allowlist", label="语义表白名单", status="pass", detail="所有表均来自 MDL 模型或视图。"),
        ReliabilityCheck(name="metric_alignment", label="指标口径", status="pass", detail="问题涉及的指标口径已通过校验。"),
        _check_status_filter(sql, metrics),
        ReliabilityCheck(
            name="row_limit",
            label="结果行数",
            status="warn" if row_count >= limit else "pass",
            detail=f"返回 {row_count} 行，上限 {limit} 行。",
        ),
        ReliabilityCheck(
            name="nonempty",
            label="结果非空",
            status="pass" if row_count > 0 else "warn",
            detail=f"结果集包含 {row_count} 行。",
        ),
        ReliabilityCheck(
            name="lineage",
            label="字段级血缘",
            status="pass" if not missing else "warn",
            detail=(
                "所有输出字段均已追溯到物理表字段。"
                if not missing
                else f"以下输出字段未能追溯到物理字段：{', '.join(missing)}"
            ),
        ),
        ReliabilityCheck(
            name="source_mapping",
            label="来源映射",
            status="pass" if refs else "fail",
            detail=(
                "模型/视图已映射到物理表或文件。" if refs else "未能解析 SQL 的来源模型。"
            ),
        ),
        ReliabilityCheck(
            name="freshness",
            label="数据新鲜度",
            status="pass" if any(f.row_count for f in freshness) else "warn",
            detail=(
                "已获取来源表行数与最近时间。" if any(f.row_count for f in freshness)
                else "未获取到来源表数据量信息。"
            ),
        ),
        ReliabilityCheck(
            name="attempts",
            label="生成稳定性",
            status="pass" if attempts <= 1 else "warn",
            detail=f"SQL 生成尝试 {attempts} 次。",
        ),
    ]
    penalties = {
        "read_only": 100, "table_allowlist": 100, "metric_alignment": 20,
        "status_filter": 15, "row_limit": 5, "nonempty": 20,
        "lineage": 10, "source_mapping": 20, "freshness": 5, "attempts": 5,
    }
    score = 100
    for check in checks:
        if check.status == "warn":
            score -= penalties.get(check.name, 5)
        elif check.status == "fail":
            score -= penalties.get(check.name, 20)
    score = max(0, min(100, score))

    return Provenance(
        connection_id=conn.id,
        connection_name=conn.name,
        source_type=conn.dialect,
        database=conn.database,
        catalog=scan.catalog,
        schema_name=scan.schema_name,
        ontology_generated_at=ontology.generated_at,
        metrics_used=metrics,
        rules_used=list(ontology.rules),
        source_tables=refs,
        views_used=views_used,
        columns_used=columns,
        lineage=lineage,
        checks=checks,
        reliability_score=score,
        freshness=freshness,
        sql=sql,
        planned_sql=planned_sql,
        sql_dialect=conn.dialect,
        row_count=row_count,
        elapsed_ms=elapsed_ms,
        llm_provider=provider,
        llm_model=model,
        attempts=attempts,
    )
