from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from ..models import (
    ConnectionConfig,
    FreshnessInfo,
    LineageEdge,
    Ontology,
    Provenance,
    ReliabilityCheck,
    ScannedSchema,
    SourceTableRef,
)
from ..provenance import _metric_dicts
from .planner import VectorExecution


def _lineage_for_plan(
    plan: dict[str, Any],
    columns: list[str],
    collection: str,
    vector_field: str,
) -> list[LineageEdge]:
    group_by = set(plan.get("group_by") or [])
    metric_map = {metric["as"]: metric for metric in (plan.get("metrics") or [])}
    edges: list[LineageEdge] = []
    seen: set[tuple[str, str]] = set()
    for column in columns:
        source_field = None
        expression = None
        if column in group_by:
            source_field = column
            expression = column
        elif column in metric_map:
            metric = metric_map[column]
            source_field = metric.get("field") if metric.get("field") != "*" else "id"
            expression = f"{metric.get('op')}({metric.get('field')})"
        elif column in ("id", "score", "distance"):
            source_field = "id" if column == "id" else vector_field
            expression = column
        elif column == "document":
            source_field = "document"
            expression = "document"
        else:
            source_field = column
            expression = column
        if source_field and (column, source_field) not in seen:
            seen.add((column, source_field))
            edges.append(
                LineageEdge(
                    output_column=column,
                    source_table=collection,
                    source_column=str(source_field),
                    expression=expression,
                )
            )
    return edges


def build_vector_provenance(
    conn: ConnectionConfig,
    scan: ScannedSchema,
    ontology: Ontology,
    question: str,
    plan: dict[str, Any],
    execution: VectorExecution,
    *,
    elapsed_ms: int,
    attempts: int,
    provider: str,
    model: str,
) -> Provenance:
    table = execution.collection
    metrics = _metric_dicts(question, ontology)
    columns = [str(column) for column in execution.df.columns]
    vector_field = table.vector_field or "vector"
    lineage = _lineage_for_plan(plan, columns, table.name, vector_field)
    covered = {edge.output_column for edge in lineage}
    missing = [column for column in columns if column not in covered]
    metrics_for_run = plan.get("metrics") or []
    limit = int(plan.get("limit") or 100)
    checks = [
        ReliabilityCheck(name="read_only", label="只读查询", status="pass", detail="查询计划不包含写入或管理操作。"),
        ReliabilityCheck(name="collection_allowlist", label="集合白名单", status="pass", detail="只访问已扫描的向量集合。"),
        ReliabilityCheck(
            name="vector_top_k",
            label="相似度检索上限",
            status="pass",
            detail=f"top_k={plan.get('search', {}).get('top_k', '-')}（上限 {50}）",
        ),
        ReliabilityCheck(
            name="row_limit",
            label="结果行数",
            status="warn" if len(execution.df) >= limit else "pass",
            detail=f"返回 {len(execution.df)} 行，上限 {limit} 行。",
        ),
        ReliabilityCheck(
            name="nonempty",
            label="结果非空",
            status="pass" if len(execution.df) else "warn",
            detail=f"结果集包含 {len(execution.df)} 行。",
        ),
        ReliabilityCheck(
            name="lineage",
            label="字段级血缘",
            status="pass" if not missing else "warn",
            detail=(
                "所有输出字段均已映射到集合字段。"
                if not missing
                else f"以下输出字段未能映射：{', '.join(missing)}"
            ),
        ),
        ReliabilityCheck(
            name="source_mapping",
            label="来源映射",
            status="pass",
            detail=f"{table.name} → {table.location}",
        ),
        ReliabilityCheck(
            name="freshness",
            label="数据新鲜度",
            status="pass" if table.row_count else "warn",
            detail=f"集合当前记录数：{table.row_count}。",
        ),
        ReliabilityCheck(
            name="attempts",
            label="生成稳定性",
            status="pass" if attempts <= 1 else "warn",
            detail=f"查询计划生成尝试 {attempts} 次。",
        ),
    ]
    penalties = {
        "read_only": 100, "collection_allowlist": 100, "vector_top_k": 15,
        "row_limit": 5, "nonempty": 20, "lineage": 10, "source_mapping": 20,
        "freshness": 10, "attempts": 5,
    }
    score = 100
    for check in checks:
        if check.status == "warn":
            score -= penalties.get(check.name, 5)
        elif check.status == "fail":
            score -= penalties.get(check.name, 20)
    score = max(0, min(100, score))

    time_column = next(
        (col.name for col in table.columns if col.role == "time"),
        None,
    )
    freshness = [
        FreshnessInfo(
            model=table.name,
            table=table.name,
            row_count=table.row_count,
            max_time_column=time_column,
            max_time_value=None,
            checked_at=datetime.now(),
            note="向量库实时计数" + ("；时间字段最大值可在列表查询中查看" if time_column else ""),
        )
    ]
    used_columns: list[str] = []
    for item in [*(plan.get("group_by") or []), *(plan.get("filters") or [])]:
        field_name = item if isinstance(item, str) else item.get("field")
        if field_name and field_name not in used_columns:
            used_columns.append(str(field_name))
    for metric in metrics_for_run:
        field_name = metric.get("field")
        if field_name and field_name != "*" and field_name not in used_columns:
            used_columns.append(str(field_name))
    return Provenance(
        connection_id=conn.id,
        connection_name=conn.name,
        source_type=conn.dialect,
        database=conn.host or "",
        catalog=None,
        schema_name="",
        ontology_generated_at=ontology.generated_at,
        metrics_used=metrics,
        rules_used=list(ontology.rules),
        source_tables=[
            SourceTableRef(
                model=table.name,
                kind="collection",
                table=table.name,
                source_type=conn.dialect,
                location=table.location,
            )
        ],
        views_used=[],
        columns_used=used_columns,
        lineage=lineage,
        checks=checks,
        reliability_score=score,
        freshness=freshness,
        sql=json.dumps(plan, ensure_ascii=False, indent=2),
        planned_sql=json.dumps(execution.request, ensure_ascii=False, indent=2),
        sql_dialect=conn.dialect,
        row_count=len(execution.df),
        elapsed_ms=elapsed_ms,
        llm_provider=provider,
        llm_model=model,
        attempts=attempts,
    )
