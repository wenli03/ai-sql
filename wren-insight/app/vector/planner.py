from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

import pandas as pd

from .. import config
from ..models import ConnectionConfig, ScannedSchema, TableInfo
from ..nl2sql.chart import recommend_chart
from .base import VectorError
from .embeddings import EmbeddingClient
from .factory import get_adapter

ALLOWED_MODES = {"aggregate", "list", "search"}
ALLOWED_METRICS = {"count", "sum", "avg", "min", "max", "distinct_count"}
ALLOWED_CHARTS = {"line", "bar", "pie", "scatter", "kpi", "table"}


def _table(scan: ScannedSchema, name: str) -> TableInfo:
    for table in scan.tables:
        if table.name == name:
            return table
    raise VectorError(f"集合不存在: {name}。可用集合：{', '.join(t.name for t in scan.tables)}")


def _field_info(table: TableInfo) -> dict[str, Any]:
    return {
        col.name: col
        for col in table.columns
        if col.mdl_type != "VECTOR"
    }


def _is_metadata_field(table: TableInfo, name: str) -> bool:
    if name in ("id", "document"):
        return True
    return name in _field_info(table)


def validate_vector_plan(plan: dict[str, Any], scan: ScannedSchema) -> dict[str, Any]:
    if not isinstance(plan, dict):
        raise VectorError("查询计划必须是 JSON 对象")
    collection = str(plan.get("collection") or "").strip()
    table = _table(scan, collection)
    mode = str(plan.get("mode") or "aggregate").lower()
    if mode not in ALLOWED_MODES:
        raise VectorError(f"不支持的 mode: {mode}")
    fields = _field_info(table)
    cleaned: dict[str, Any] = {
        "title": str(plan.get("title") or "查询结果"),
        "mode": mode,
        "collection": collection,
        "filters": [],
        "group_by": [],
        "metrics": [],
        "order_by": [],
        "limit": min(int(plan.get("limit") or 100), 500),
        "chart": plan.get("chart") if isinstance(plan.get("chart"), dict) else {},
        "explanation": str(plan.get("explanation") or ""),
        "assumptions": plan.get("assumptions") if isinstance(plan.get("assumptions"), list) else [],
    }
    for item in plan.get("filters") or []:
        field_name = str(item.get("field") or "")
        op = str(item.get("op") or "eq").lower()
        if field_name not in fields and field_name not in ("document", "content", "text"):
            raise VectorError(f"过滤字段不存在: {field_name}")
        if op not in {"eq", "in", "gte", "lte", "contains"}:
            raise VectorError(f"不支持的过滤操作: {op}")
        cleaned["filters"].append({"field": field_name, "op": op, "value": item.get("value")})
    for field_name in plan.get("group_by") or []:
        field_name = str(field_name)
        if field_name not in fields:
            raise VectorError(f"分组字段不存在: {field_name}")
        cleaned["group_by"].append(field_name)
    for item in plan.get("metrics") or []:
        op = str(item.get("op") or "count").lower()
        field_name = str(item.get("field") or "*")
        if op not in ALLOWED_METRICS:
            raise VectorError(f"不支持的指标操作: {op}")
        if op == "count" and field_name not in ("*", "id"):
            if field_name not in fields:
                raise VectorError(f"计数字段不存在: {field_name}")
        elif op != "count" and field_name not in fields:
            raise VectorError(f"指标字段不存在: {field_name}")
        if op in {"sum", "avg", "min", "max"}:
            col = fields.get(field_name)
            if col and col.mdl_type not in ("INTEGER", "BIGINT", "DOUBLE", "DECIMAL"):
                raise VectorError(f"字段 {field_name} 不是数值字段，不能做 {op}")
        cleaned["metrics"].append({
            "op": op,
            "field": field_name,
            "as": str(item.get("as") or f"{op}_{field_name}"),
        })
    if not cleaned["metrics"] and mode == "aggregate":
        cleaned["metrics"] = [{"op": "count", "field": "*", "as": "count"}]
    if mode == "aggregate" and cleaned["metrics"]:
        for item in cleaned["metrics"]:
            pass
    for item in plan.get("order_by") or []:
        cleaned["order_by"].append({
            "field": str(item.get("field") or ""),
            "direction": "asc" if str(item.get("direction") or "").lower() == "asc" else "desc",
        })
    search = plan.get("search") if isinstance(plan.get("search"), dict) else {}
    top_k = int(search.get("top_k") or 10)
    if mode == "search":
        if not str(search.get("text") or "").strip():
            raise VectorError("search 模式必须提供 search.text")
        if top_k < 1 or top_k > config.VECTOR_MAX_TOP_K:
            raise VectorError(f"top_k 必须在 1..{config.VECTOR_MAX_TOP_K} 之间")
        cleaned["search"] = {
            "text": str(search.get("text")),
            "top_k": top_k,
        }
    chart = cleaned["chart"]
    if chart.get("type") not in ALLOWED_CHARTS:
        chart["type"] = "table"
    return cleaned


def _metric_frame(df: pd.DataFrame, group_by: list[str], metric: dict[str, Any]) -> pd.DataFrame:
    op = metric["op"]
    field_name = metric["field"]
    alias = metric["as"]
    if not group_by:
        if op == "count":
            value = len(df)
        elif op == "distinct_count":
            value = df[field_name].nunique(dropna=True) if field_name in df else 0
        else:
            series = pd.to_numeric(df[field_name], errors="coerce") if field_name in df else pd.Series(dtype=float)
            method = {"avg": "mean", "sum": "sum", "min": "min", "max": "max"}[op]
            value = getattr(series, method)() if len(series) else 0
        return pd.DataFrame([{alias: value}])
    grouped = df.groupby(group_by, dropna=False)
    if op == "count":
        series = grouped.size()
    elif op == "distinct_count":
        series = grouped[field_name].nunique(dropna=True)
    else:
        numeric = pd.to_numeric(df[field_name], errors="coerce")
        work = df.assign(__value=numeric)
        grouped = work.groupby(group_by, dropna=False)
        method = {"avg": "mean", "sum": "sum", "min": "min", "max": "max"}[op]
        series = getattr(grouped["__value"], method)()
    return series.rename(alias).reset_index()


def aggregate_records(records: list[dict[str, Any]], plan: dict[str, Any]) -> pd.DataFrame:
    group_by = plan.get("group_by") or []
    metrics = plan.get("metrics") or []
    if not records:
        columns = group_by + [m["as"] for m in metrics]
        return pd.DataFrame(columns=columns)
    df = pd.DataFrame(records)
    frames = [_metric_frame(df, group_by, metric) for metric in metrics]
    if not frames:
        return pd.DataFrame()
    if group_by:
        result = frames[0]
        for frame in frames[1:]:
            result = result.merge(frame, on=group_by, how="outer")
    else:
        result = pd.concat(frames, axis=1)
    order_by = plan.get("order_by") or []
    if order_by:
        by = [item["field"] for item in order_by if item["field"] in result.columns]
        ascending = [item["direction"] != "desc" for item in order_by if item["field"] in result.columns]
        if by:
            result = result.sort_values(by=by, ascending=ascending, na_position="last")
    elif group_by and metrics:
        alias = metrics[0]["as"]
        if alias in result.columns:
            result = result.sort_values(alias, ascending=False, na_position="last")
    return result.head(int(plan.get("limit") or 100)).reset_index(drop=True)


@dataclass
class VectorExecution:
    df: pd.DataFrame
    collection: TableInfo
    request: dict[str, Any]
    warnings: list[str] = field(default_factory=list)


def _clean_records(records: list[dict[str, Any]], limit: int | None = None) -> pd.DataFrame:
    if not records:
        return pd.DataFrame()
    df = pd.DataFrame(records)
    if "document" in df.columns:
        df["document"] = df["document"].map(lambda value: str(value)[:500] if value is not None else None)
    if limit:
        df = df.head(limit)
    return df.reset_index(drop=True)


_STOPWORDS = {
    "the", "a", "an", "and", "or", "of", "to", "in", "for", "on", "with",
    "is", "are", "what", "which", "how", "show", "find", "list", "me",
}


def _query_terms(text: str) -> list[str]:
    # Chinese is split into characters; Latin words are kept as tokens.
    raw = re.findall(r"[一-鿿]|[A-Za-z0-9_]+", (text or "").lower())
    return [term for term in raw if term not in _STOPWORDS]


def _lexical_score(query: str, record: dict[str, Any]) -> float:
    terms = list(dict.fromkeys(_query_terms(query)))
    if not terms:
        return 0.0
    haystack = " ".join(
        str(record.get(key) or "")
        for key in ("title", "document", "topic", "category", "source", "language")
    ).lower()
    matched = sum(1 for term in terms if term in haystack)
    return matched / len(terms)


def _hybrid_rerank(
    records: list[dict[str, Any]],
    query: str,
    top_k: int,
) -> list[dict[str, Any]]:
    ranked = []
    for record in records:
        distance = record.get("distance")
        if distance is not None:
            try:
                vector_sim = max(0.0, 1.0 - float(distance))
            except (TypeError, ValueError):
                vector_sim = 0.0
        else:
            try:
                vector_sim = float(record.get("score") or 0.0)
            except (TypeError, ValueError):
                vector_sim = 0.0
        lexical = _lexical_score(query, record)
        record["_hybrid_score"] = vector_sim + 0.35 * lexical
        ranked.append(record)
    ranked.sort(key=lambda item: item.get("_hybrid_score", 0.0), reverse=True)
    for record in ranked:
        record.pop("_hybrid_score", None)
    return ranked[:top_k]


def execute_vector_plan(
    conn: ConnectionConfig,
    scan: ScannedSchema,
    plan: dict[str, Any],
) -> VectorExecution:
    table = _table(scan, plan["collection"])
    adapter = get_adapter(conn)
    warnings: list[str] = []
    summary: dict[str, Any] = {
        "source": conn.dialect,
        "endpoint": conn.host,
        "collection": table.name,
        "mode": plan["mode"],
        "filters": plan.get("filters") or [],
        "group_by": plan.get("group_by") or [],
        "metrics": plan.get("metrics") or [],
        "limit": plan.get("limit"),
    }
    try:
        if plan["mode"] == "aggregate":
            records = adapter.scan(
                table.name,
                plan.get("filters"),
                max_records=config.VECTOR_MAX_SCAN,
            )
            if len(records) >= config.VECTOR_MAX_SCAN:
                warnings.append(
                    f"聚合基于前 {config.VECTOR_MAX_SCAN} 条记录；超大集合建议在向量库侧做物化统计。"
                )
            df = aggregate_records(records, plan)
            summary["scanned_records"] = len(records)
        elif plan["mode"] == "list":
            records = adapter.scan(
                table.name,
                plan.get("filters"),
                max_records=min(int(plan.get("limit") or 100), 500),
            )
            df = _clean_records(records, int(plan.get("limit") or 100))
            summary["scanned_records"] = len(records)
        else:
            search = plan.get("search") or {}
            vector = EmbeddingClient().embed_one(search.get("text") or "")
            if table.vector_dimension and len(vector) != table.vector_dimension:
                raise VectorError(
                    f"向量维度不匹配：集合 {table.name} 是 {table.vector_dimension} 维，"
                    f"向量化模型输出 {len(vector)} 维。请更换 EMBEDDING_MODEL。"
                )
            top_k = int(search.get("top_k") or 10)
            candidate_k = min(max(top_k * 3, top_k), config.VECTOR_MAX_TOP_K)
            records = adapter.search(
                table.name,
                vector,
                candidate_k,
                plan.get("filters"),
            )
            records = _hybrid_rerank(records, search.get("text") or "", top_k)
            df = _clean_records(records, top_k)
            summary["top_k"] = top_k
            summary["candidate_k"] = candidate_k
            summary["rerank"] = "vector similarity + keyword overlap"
            summary["embedding_model"] = config.EMBEDDING_MODEL
            summary["vector_dimension"] = len(vector)
        return VectorExecution(df=df, collection=table, request=summary, warnings=warnings)
    finally:
        adapter.close()


def chart_for_execution(
    execution: VectorExecution,
    question: str,
    plan: dict[str, Any],
) -> Any:
    chart_payload = plan.get("chart") if isinstance(plan.get("chart"), dict) else {}
    override = None
    if plan["mode"] in ("search", "list"):
        override = "table"
    return recommend_chart(
        execution.df,
        question,
        chart_payload,
        override=override,
        title=plan.get("title") or "查询结果",
        kpi_label=(plan.get("metrics") or [{}])[0].get("as") if plan["mode"] == "aggregate" else None,
    )
