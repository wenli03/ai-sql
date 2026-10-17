from __future__ import annotations

import re
from typing import Any

import pandas as pd
from sqlglot import exp, parse

from .. import config
from ..core.dialects import sqlglot_dialect
from ..core.trace import (
    SPAN_CHART,
    SPAN_ENSURE_BUILT,
    SPAN_EXECUTE,
    SPAN_EXECUTE_DRY_PLAN,
    SPAN_EXECUTE_QUERY,
    SPAN_LLM,
    SPAN_LOAD_ONTOLOGY,
    SPAN_LOAD_SCHEMA,
    SPAN_MANIFEST,
    SPAN_MANIFEST_B64,
    SPAN_PROMPT,
    SPAN_PROVENANCE,
    SPAN_VALIDATE,
    Trace,
)
from ..models import AskResponse, ConnectionConfig, Ontology, ScannedSchema, TraceSpan
from ..provenance import build_provenance
from ..semantic import store as semantic_store
from .chart import dataframe_records, recommend_chart
from .llm import LLMClient, LLMError, extract_json
from .prompt import build_ask_messages, build_repair_messages


class NL2SQLError(RuntimeError):
    def __init__(self, message: str, sql: str | None = None, detail: str | None = None):
        super().__init__(message)
        self.sql = sql
        self.detail = detail


def allowed_names(manifest: dict[str, Any]) -> set[str]:
    names = {m.get("name", "").lower() for m in manifest.get("models", [])}
    names |= {v.get("name", "").lower() for v in manifest.get("views", [])}
    names.discard("")
    return names


def _reject_node_types() -> tuple[type, ...]:
    names = [
        "Insert", "Update", "Delete", "Create", "Drop", "Alter", "TruncateTable",
        "Merge", "Command", "Copy", "Use", "Set", "Transaction", "Commit",
        "Rollback", "Grant", "Call", "Into", "LoadData",
    ]
    result = []
    for name in names:
        node = getattr(exp, name, None)
        if isinstance(node, type):
            result.append(node)
    return tuple(result)


FORBIDDEN_NODES = _reject_node_types()
ALLOWED_ROOTS = tuple(
    node for node in (
        getattr(exp, "Select", None), getattr(exp, "Union", None),
        getattr(exp, "Intersect", None), getattr(exp, "Except", None),
        getattr(exp, "Subquery", None),
    ) if isinstance(node, type)
)


def validate_sql(sql: str, dialect: str, manifest: dict[str, Any]) -> tuple[str, list[str]]:
    """Validate read-only SQL and table allowlist. Returns (clean_sql, warnings)."""
    warnings: list[str] = []
    clean = sql.strip().rstrip(";").strip()
    if not clean:
        raise NL2SQLError("模型返回了空 SQL")
    try:
        expressions = parse(clean, dialect=sqlglot_dialect(dialect))
    except Exception as exc:
        raise NL2SQLError(f"SQL 语法解析失败: {exc}", sql=clean) from exc
    if len(expressions) != 1:
        raise NL2SQLError("只允许一条 SQL 语句", sql=clean)
    root = expressions[0]
    if not isinstance(root, ALLOWED_ROOTS):
        raise NL2SQLError("只允许 SELECT / WITH 查询", sql=clean)
    for node in root.walk():
        if isinstance(node, FORBIDDEN_NODES):
            raise NL2SQLError(f"不允许的 SQL 操作: {type(node).__name__}", sql=clean)

    cte_names = {cte.alias_or_name.lower() for cte in root.find_all(exp.CTE) if cte.alias_or_name}
    allowed = allowed_names(manifest)
    for table in root.find_all(exp.Table):
        name = table.name
        if not name:
            continue
        lower = name.lower()
        if lower in cte_names or lower in allowed:
            continue
        raise NL2SQLError(
            f"SQL 引用了未建模的表「{name}」。只能使用：{', '.join(sorted(allowed))}",
            sql=clean,
        )
    if any(isinstance(select, exp.Select) and any(isinstance(p, exp.Star) for p in select.expressions) for select in root.find_all(exp.Select)):
        warnings.append("SQL 使用了 SELECT *，建议显式列出字段。")
    return clean, warnings


def check_metric_alignment(sql: str, question: str, ontology: Ontology) -> list[str]:
    """Heuristic guard: if the question names a governed metric, its defining
    operator must appear in the SQL. This catches common cases where the model
    writes SUM(amount) and calls it 客单价/AOV without dividing by order count."""
    lower_question = question.lower()
    lower_sql = sql.lower()
    asks_detail = bool(re.search(r"有哪些|哪些|列表|明细|是谁|列出|清单|明细表", question))
    include_all = bool(re.search(r"包含|包括|全部|所有|含取消|含退款|不过滤", question))
    issues: list[str] = []
    for metric in ontology.metrics:
        terms = [metric.name, metric.label, *metric.synonyms]
        if not any(term and term.lower() in lower_question for term in terms):
            continue
        expression = metric.expression.lower()
        if "/" in expression and "/" not in lower_sql:
            issues.append(
                f"问题涉及指标「{metric.label}」，其口径是 `{metric.expression}`，"
                "但当前 SQL 没有按该口径做除法。请直接使用指标表达式。"
            )
        if "count(distinct" in expression and "count(distinct" not in lower_sql and not asks_detail:
            issues.append(
                f"问题涉及指标「{metric.label}」，其口径是 `{metric.expression}`，"
                "但当前 SQL 缺少去重计数。请直接使用指标表达式。"
            )
        if metric.filters and not include_all:
            filter_match = re.search(r"([A-Za-z_][\w.]*)\s+NOT\s+IN\s*\(([^)]*)\)", metric.filters, re.I)
            if filter_match:
                column = filter_match.group(1).split(".")[-1].lower()
                excluded_values = [
                    value.strip().strip("'\"")
                    for value in filter_match.group(2).split(",")
                    if value.strip()
                ]
                has_exclusion = (
                    column in lower_sql
                    and (
                        "not in" in lower_sql
                        or all(value.lower() not in lower_sql for value in excluded_values)
                    )
                )
                if not has_exclusion:
                    issues.append(
                        f"问题涉及指标「{metric.label}」，必须应用其过滤条件 "
                        f"`{metric.filters}`（除非用户明确要求包含取消/退款订单）。"
                    )
    return list(dict.fromkeys(issues))


def _execute(
    conn: ConnectionConfig,
    manifest_b64: str,
    sql: str,
    max_rows: int,
    trace: Trace | None = None,
) -> tuple[pd.DataFrame, str]:
    from wren.engine import WrenEngine
    from wren.model.data_source import DataSource

    trace = trace or Trace("execute")
    # Engine construction is inside the span on purpose: the restart target is
    # to reuse a pooled engine instead of paying this on every single ask.
    with trace.span(SPAN_EXECUTE):
        engine = WrenEngine(
            manifest_str=manifest_b64,
            data_source=DataSource(conn.dialect),
            connection_info=conn.wren_connection_info(),
        )
    try:
        with trace.span(SPAN_EXECUTE_DRY_PLAN):
            planned = engine.dry_plan(sql)
        with trace.span(SPAN_EXECUTE_QUERY):
            table = engine.query(sql, limit=max_rows)
            df = table.to_pandas()
    except Exception as exc:
        raise NL2SQLError(f"SQL 执行失败: {str(exc)[:1200]}", sql=sql) from exc
    finally:
        engine.close()
    df.columns = [str(column) for column in df.columns]
    # Arrow decimals often arrive as Python Decimal objects; coerce numeric-looking
    # object columns so chart detection and JSON output behave predictably.
    for column in df.columns:
        if df[column].dtype == object:
            converted = pd.to_numeric(df[column], errors="coerce")
            if len(df) == 0 or converted.notna().sum() >= max(1, int(len(df) * 0.8)):
                df[column] = converted
    return df, planned


def _sanitize_chart_payload(payload: dict[str, Any]) -> dict[str, Any]:
    chart = payload.get("chart")
    if not isinstance(chart, dict):
        return {}
    return chart


def ask(
    conn: ConnectionConfig,
    question: str,
    *,
    max_rows: int | None = None,
    chart_type: str | None = None,
    history: list[dict[str, str]] | None = None,
    trace: Trace | None = None,
) -> AskResponse:
    question = question.strip()
    if not question:
        raise NL2SQLError("问题不能为空")
    from ..vector.base import VECTOR_SOURCES

    if conn.dialect in VECTOR_SOURCES:
        from ..vector.pipeline import ask_vector

        return ask_vector(
            conn,
            question,
            max_rows=max_rows,
            history=history,
            trace=trace,
        )
    trace = trace or Trace()
    with trace.span(SPAN_ENSURE_BUILT):
        semantic_store.ensure_built(conn)
    with trace.span(SPAN_LOAD_SCHEMA):
        scan: ScannedSchema = semantic_store.load_schema(conn.id)
    with trace.span(SPAN_LOAD_ONTOLOGY):
        ontology: Ontology = semantic_store.load_ontology(conn.id)
    with trace.span(SPAN_MANIFEST):
        manifest = semantic_store.manifest(conn.id)
    with trace.span(SPAN_MANIFEST_B64):
        manifest_b64 = semantic_store.manifest_b64(conn.id)

    limit = min(max_rows or config.MAX_ROWS, 2000)
    client = LLMClient()
    with trace.span(SPAN_PROMPT):
        messages = build_ask_messages(question, scan, ontology, manifest, conn, history)

    warnings: list[str] = []
    attempts = 0
    last_error = ""
    last_sql = ""
    payload: dict[str, Any] = {}
    usage: dict[str, Any] = {}
    provider = client.provider
    model = client.model

    for attempt in range(config.MAX_REPAIR_ATTEMPTS + 1):
        attempts = attempt + 1
        with trace.span(SPAN_LLM, attempt=attempt):
            result = client.chat(messages, trace=trace)
        provider, model = result.provider, result.model
        for key, value in (result.usage or {}).items():
            if isinstance(value, (int, float)):
                usage[key] = usage.get(key, 0) + value
        try:
            payload = extract_json(result.content)
        except LLMError as exc:
            last_error = str(exc)
            if attempt >= config.MAX_REPAIR_ATTEMPTS:
                raise NL2SQLError(f"模型输出无法解析: {exc}") from exc
            messages = build_repair_messages(
                messages, {"sql": last_sql or result.content[:800]}, f"输出不是合法 JSON：{exc}"
            )
            continue

        sql = str(payload.get("sql") or "").strip()
        last_sql = sql
        try:
            with trace.span(SPAN_VALIDATE):
                clean_sql, sql_warnings = validate_sql(sql, conn.dialect, manifest)
                alignment_issues = check_metric_alignment(clean_sql, question, ontology)
            if alignment_issues:
                raise NL2SQLError("；".join(alignment_issues), sql=clean_sql)
            df, planned = _execute(conn, manifest_b64, clean_sql, limit, trace=trace)
            warnings.extend(sql_warnings)
            break
        except NL2SQLError as exc:
            last_error = str(exc)
            if attempt >= config.MAX_REPAIR_ATTEMPTS:
                raise NL2SQLError(
                    f"SQL 生成失败：{last_error}", sql=last_sql, detail=last_error
                ) from exc
            messages = build_repair_messages(messages, payload, last_error)
    else:  # pragma: no cover - loop always breaks or raises
        raise NL2SQLError(last_error or "SQL 生成失败", sql=last_sql)

    # End-to-end wall time of ask(), i.e. what the user actually waits for.
    elapsed_ms = int(round(trace.wall_ms))
    if len(df) >= limit:
        warnings.append(f"结果达到行数上限 {limit}，已截断。")
    if len(df) == 0:
        warnings.append("查询没有返回数据，请检查筛选条件或换个问法。")

    title = str(payload.get("title") or "查询结果").strip() or "查询结果"
    kpi_label = None
    lower_question = question.lower()
    for metric in ontology.metrics:
        terms = [metric.label, *metric.synonyms]
        if any(term and term.lower() in lower_question for term in terms):
            kpi_label = metric.label
            break
    with trace.span(SPAN_CHART):
        chart = recommend_chart(
            df,
            question,
            _sanitize_chart_payload(payload),
            override=chart_type,
            title=title,
            kpi_label=kpi_label,
        )
    assumptions = payload.get("assumptions") or []
    if not isinstance(assumptions, list):
        assumptions = [str(assumptions)]
    explanation = str(payload.get("explanation") or "").strip()

    try:
        with trace.span(SPAN_PROVENANCE):
            provenance = build_provenance(
                conn,
                scan,
                ontology,
                manifest,
                question,
                clean_sql,
                planned,
                [str(c) for c in df.columns],
                elapsed_ms=elapsed_ms,
                attempts=attempts,
                provider=provider,
                model=model,
                row_count=len(df),
                limit=limit,
                # Keep asks fast and resilient: freshness is a scan-time snapshot by
                # default. The provenance panel can probe live freshness on demand.
                probe_freshness_enabled=False,
            )
    except Exception as exc:
        warnings.append(f"溯源信息生成不完整：{exc}")
        provenance = None

    return AskResponse(
        question=question,
        title=title,
        sql=clean_sql,
        planned_sql=planned,
        explanation=explanation,
        assumptions=[str(item) for item in assumptions if str(item).strip()],
        chart=chart,
        columns=[str(c) for c in df.columns],
        rows=dataframe_records(df),
        row_count=len(df),
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


def suggestions(connection_id: str) -> list[str]:
    from ..store import get_connection
    from ..vector.base import VECTOR_SOURCES

    conn = get_connection(connection_id)
    if conn.dialect in VECTOR_SOURCES:
        from ..vector.pipeline import vector_suggestions

        return vector_suggestions(connection_id)
    semantic_store.ensure_built(conn)
    ontology = semantic_store.load_ontology(connection_id)
    metric_names = {m.name for m in ontology.metrics}
    dim_names = {d.name for d in ontology.dimensions}
    candidates: list[str] = []
    if "销售额" in metric_names and "下单月份" in dim_names:
        candidates.append("最近6个月每月的销售额趋势")
    if "商品销售额" in metric_names and any(d.name == "category" for d in ontology.dimensions):
        candidates.append("各品类销售额排行")
    if "订单数" in metric_names and any(d.name == "channel" for d in ontology.dimensions):
        candidates.append("各渠道的订单数和客单价对比")
    if "高价值客户数" in metric_names:
        candidates.append("高价值客户有哪些")
    if "销售额" in metric_names and any(d.name == "province" for d in ontology.dimensions):
        candidates.append("各省份销售额分布")
    if "客单价" in metric_names and any(d.name == "level" for d in ontology.dimensions):
        candidates.append("不同客户等级的客单价对比")
    if "退款率" in metric_names:
        candidates.append("各渠道退款率对比")
    if not candidates:
        for metric in ontology.metrics[:4]:
            candidates.append(f"按维度查看{metric.label}")
    return candidates[:6]
