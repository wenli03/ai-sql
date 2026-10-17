from __future__ import annotations

import math
import time
from typing import Any

from .. import config
from ..core.trace import SPAN_CHART, SPAN_LLM, SPAN_PROVENANCE, Trace
from ..models import AskResponse, ConnectionConfig, TraceSpan
from ..nl2sql.llm import LLMClient, LLMError, extract_json
from ..nl2sql.pipeline import NL2SQLError
from ..semantic import store as semantic_store
from .base import VectorError
from .planner import chart_for_execution, execute_vector_plan, validate_vector_plan
from .prompt import build_vector_messages, build_vector_repair_messages
from .provenance import build_vector_provenance


class VectorNL2QueryError(NL2SQLError):
    def __init__(self, message: str, plan: str | None = None):
        super().__init__(message, sql=plan)


def ask_vector(
    conn: ConnectionConfig,
    question: str,
    *,
    max_rows: int | None = None,
    history: list[dict[str, str]] | None = None,
    trace: Trace | None = None,
) -> AskResponse:
    question = question.strip()
    if not question:
        raise VectorNL2QueryError("问题不能为空")
    trace = trace or Trace("vector_ask")
    started = time.perf_counter()

    semantic_store.ensure_built(conn)
    scan = semantic_store.load_schema(conn.id)
    ontology = semantic_store.load_ontology(conn.id)
    client = LLMClient()
    messages = build_vector_messages(question, scan, ontology, conn, history)

    warnings: list[str] = []
    attempts = 0
    last_error = ""
    plan: dict[str, Any] = {}
    usage: dict[str, Any] = {}
    provider = client.provider
    model = client.model

    for attempt in range(config.MAX_REPAIR_ATTEMPTS + 1):
        attempts = attempt + 1
        with trace.span(SPAN_LLM, attempt=attempt):
            result = client.chat(messages)
        provider, model = result.provider, result.model
        for key, value in (result.usage or {}).items():
            if isinstance(value, (int, float)):
                usage[key] = usage.get(key, 0) + value
        try:
            payload = extract_json(result.content)
            plan = validate_vector_plan(payload, scan)
            break
        except (LLMError, VectorError) as exc:
            last_error = str(exc)
            if attempt >= config.MAX_REPAIR_ATTEMPTS:
                raise VectorNL2QueryError(f"查询计划生成失败：{last_error}") from exc
            messages = build_vector_repair_messages(
                messages,
                payload if "payload" in locals() and isinstance(payload, dict) else {},
                last_error,
            )
    else:  # pragma: no cover
        raise VectorNL2QueryError(last_error or "查询计划生成失败")

    try:
        execution = execute_vector_plan(conn, scan, plan)
    except Exception as exc:
        raise VectorNL2QueryError(f"查询执行失败：{exc}", plan=str(plan)) from exc

    warnings.extend(execution.warnings)
    if len(execution.df) == 0:
        warnings.append("查询没有返回数据，请检查过滤条件或换个问法。")
    title = str(plan.get("title") or "查询结果").strip() or "查询结果"
    if title == "查询结果":
        if plan["mode"] == "search":
            title = f"相似度检索 · {(plan.get('search') or {}).get('text', '')}".strip()
        elif plan["mode"] == "list":
            title = "明细列表"
        else:
            title = question
    plan["title"] = title
    with trace.span(SPAN_CHART):
        chart = chart_for_execution(execution, question, plan)
    try:
        with trace.span(SPAN_PROVENANCE):
            provenance = build_vector_provenance(
                conn,
                scan,
                ontology,
                question,
                plan,
                execution,
                elapsed_ms=int(round(trace.wall_ms)),
                attempts=attempts,
                provider=provider,
                model=model,
            )
    except Exception as exc:
        warnings.append(f"溯源信息生成不完整：{exc}")
        provenance = None

    elapsed_ms = int(round(trace.wall_ms))
    explanation = str(plan.get("explanation") or "").strip()
    assumptions = plan.get("assumptions") or []
    if not isinstance(assumptions, list):
        assumptions = [str(assumptions)]
    rows = execution.df.to_dict(orient="records")
    # Make numpy/pandas scalar values JSON friendly.
    clean_rows = []
    for row in rows:
        clean = {}
        for key, value in row.items():
            if value is None:
                clean[str(key)] = None
            elif isinstance(value, float) and math.isnan(value):
                clean[str(key)] = None
            elif hasattr(value, "item"):
                try:
                    clean[str(key)] = value.item()
                except (ValueError, AttributeError):
                    clean[str(key)] = str(value)
            else:
                clean[str(key)] = value
        clean_rows.append(clean)
    return AskResponse(
        question=question,
        title=title,
        sql=str(provenance.sql if provenance else plan),
        planned_sql=str(provenance.planned_sql if provenance else ""),
        explanation=explanation,
        assumptions=[str(item) for item in assumptions if str(item).strip()],
        chart=chart,
        columns=[str(column) for column in execution.df.columns],
        rows=clean_rows,
        row_count=len(execution.df),
        elapsed_ms=elapsed_ms,
        attempts=attempts,
        warnings=warnings,
        provider=provider,
        model=model,
        token_usage=usage,
        provenance=provenance,
        trace=[TraceSpan.model_validate(span.to_dict()) for span in trace.spans],
        trace_aggregate=trace.aggregate(),
        context_ms=trace.context_ms(),
    )


def vector_suggestions(connection_id: str) -> list[str]:
    from ..store import get_connection

    conn = get_connection(connection_id)
    semantic_store.ensure_built(conn)
    ontology = semantic_store.load_ontology(connection_id)
    scan = semantic_store.load_schema(connection_id)
    dimensions = [dim for dim in ontology.dimensions]
    categorical = next((dim for dim in dimensions if dim.name not in ("created_at", "updated_at")), None)
    time_column = next(
        (col.name for table in scan.tables for col in table.columns if col.role == "time"),
        None,
    )
    suggestions: list[str] = []
    if categorical:
        suggestions.append(f"按 {categorical.name} 统计文档数量")
        suggestions.append(f"各 {categorical.name} 的文档数量占比")
    suggestions.append("找与退款政策最相似的5条文档")
    if time_column:
        suggestions.append("最近30天新增的文档")
    if len(ontology.entities) > 1:
        suggestions.append(f"在 {ontology.entities[0].name} 中列出最近的10条记录")
    return suggestions[:6]
