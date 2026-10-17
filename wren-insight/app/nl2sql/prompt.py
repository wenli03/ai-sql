from __future__ import annotations

import json
from typing import Any

from ..models import ConnectionConfig, Ontology, ScannedSchema

DIALECT_LABEL = {
    "postgres": "PostgreSQL",
    "mysql": "MySQL",
    "oracle": "Oracle Database",
    "mssql": "SQL Server",
    "trino": "Trino",
    "databricks": "Databricks SQL",
    "athena": "Amazon Athena",
    "duckdb": "DuckDB",
    "clickhouse": "ClickHouse",
    "bigquery": "BigQuery",
    "spark": "Spark SQL",
}

DIALECT_NOTES = {
    "postgres": (
        "PostgreSQL 语法：月份使用 TO_CHAR(col, 'YYYY-MM')；日期截断使用 DATE_TRUNC；"
        "相对时间使用 CURRENT_DATE - INTERVAL '6 months' 或 CURRENT_DATE - INTERVAL '90 days'；"
        "整数除法需转 numeric；字符串连接使用 ||。"
    ),
    "mysql": (
        "MySQL 语法：月份使用 DATE_FORMAT(col, '%Y-%m')；相对时间使用 "
        "DATE_SUB(CURDATE(), INTERVAL 6 MONTH)；日期使用 DATE(col)；字符串连接使用 CONCAT。"
    ),
    "oracle": (
        "Oracle 语法：月份使用 TO_CHAR(col, 'YYYY-MM')；相对时间使用 "
        "ADD_MONTHS(SYSDATE, -6)；日期截断使用 TRUNC(col, 'MM')；"
        "限制行数使用 FETCH FIRST n ROWS ONLY；空值使用 NVL；标识符使用双引号。"
    ),
    "mssql": (
        "SQL Server 语法：月份使用 FORMAT(col, 'yyyy-MM') 或 CONVERT(varchar(7), col, 120)；"
        "相对时间使用 DATEADD(month, -6, GETDATE())；限制行数使用 TOP n；"
        "空值使用 ISNULL；标识符使用方括号。"
    ),
    "trino": (
        "Trino 语法：月份使用 date_format(col, '%Y-%m')；相对时间使用 "
        "current_date - INTERVAL '6' month；日期转换使用 CAST(col AS DATE)；"
        "标识符使用双引号。数据湖表通常需要 catalog.schema.table 三段式名称。"
    ),
    "databricks": (
        "Databricks SQL 语法：月份使用 date_format(col, 'yyyy-MM')；相对时间使用 "
        "current_date() - INTERVAL 6 MONTH；日期转换使用 CAST(col AS DATE)；"
        "标识符使用反引号。Delta Lake 表使用 catalog.schema.table 三段式名称。"
    ),
    "athena": (
        "Athena / Presto 语法：月份使用 date_format(col, '%Y-%m')；相对时间使用 "
        "date_add('month', -6, current_date)；日期转换使用 CAST(col AS DATE)；"
        "标识符使用双引号。"
    ),
    "duckdb": (
        "DuckDB 语法：月份使用 strftime(col, '%Y-%m')；相对时间使用 "
        "CURRENT_DATE - INTERVAL 6 MONTH；日期转换使用 CAST(col AS DATE)；"
        "可以直接使用 read_parquet/read_csv_auto 读取数据湖文件。"
    ),
    "clickhouse": (
        "ClickHouse 语法：月份使用 formatDateTime(col, '%Y-%m')；相对时间使用 "
        "now() - INTERVAL 6 MONTH；日期转换使用 toDate(col)；"
        "标识符使用反引号；聚合前注意 Nullable 字段。"
    ),
    "bigquery": (
        "BigQuery 语法：月份使用 FORMAT_DATE('%Y-%m', col)；相对时间使用 "
        "DATE_SUB(CURRENT_DATE(), INTERVAL 6 MONTH)；日期转换使用 DATE(col)；"
        "标识符使用反引号；表名使用 project.dataset.table 三段式名称。"
    ),
    "spark": (
        "Spark SQL 语法：月份使用 date_format(col, 'yyyy-MM')；相对时间使用 "
        "current_date() - INTERVAL 6 MONTH；日期转换使用 CAST(col AS DATE)；"
        "标识符使用反引号。"
    ),
}


def _join(items: list[str], empty: str = "无") -> str:
    return "、".join(items) if items else empty


def _render_models(scan: ScannedSchema, ontology: Ontology, manifest: dict[str, Any]) -> str:
    entity_by_model = {e.model: e for e in ontology.entities}
    lines: list[str] = ["## 可查询的模型（只能使用这些名称）", ""]
    for table in scan.tables:
        # Find the entity/model name via table_reference stored in the manifest.
        model = None
        for m in manifest.get("models", []):
            ref = m.get("tableReference") or {}
            if ref.get("table") == table.name:
                model = m.get("name")
                break
        if not model:
            continue
        entity = entity_by_model.get(model)
        label = entity.label if entity else table.name
        synonyms = _join(entity.synonyms if entity else [], "")
        desc = (entity.description if entity else table.comment) or ""
        lines.append(f"### {model} · {label}")
        if synonyms:
            lines.append(f"业务同义词：{synonyms}")
        if desc:
            lines.append(f"说明：{desc}")
        lines.append("字段：")
        for col in table.columns:
            flags = []
            if col.is_primary_key:
                flags.append("主键")
            if col.is_foreign_key:
                flags.append("外键")
            if col.comment:
                flags.append(col.comment)
            sample = ""
            if col.sample_values:
                sample = "；样例：" + " / ".join(col.sample_values[:6])
            flag_text = f" [{'，'.join(flags)}]" if flags else ""
            lines.append(f"- {col.name} ({col.mdl_type}){flag_text}{sample}")
        lines.append("")

    views = manifest.get("views", [])
    if views:
        lines.append("## 可查询的视图（优先用于多表分析）")
        lines.append("")
        for view in views:
            name = view.get("name")
            desc = (view.get("properties") or {}).get("description", "")
            statement = view.get("statement", "")
            lines.append(f"### {name}")
            if desc:
                lines.append(f"说明：{desc}")
            lines.append("SQL：")
            lines.append("```sql")
            lines.append(statement.strip())
            lines.append("```")
            lines.append("")
    return "\n".join(lines)


def _render_ontology(ontology: Ontology) -> str:
    lines: list[str] = []
    lines.append("## 业务关系")
    if ontology.relationships:
        for rel in ontology.relationships:
            lines.append(
                f"- {rel.from_model}.{rel.from_column} = {rel.to_model}.{rel.to_column}"
                f"（{rel.join_type}）{rel.description}"
            )
    else:
        lines.append("- 无显式关系")
    lines.append("")

    lines.append("## 指标口径（必须优先使用）")
    if ontology.metrics:
        for metric in ontology.metrics:
            line = f"- {metric.label}（同义词：{_join(metric.synonyms)}）"
            if metric.unit:
                line += f"，单位：{metric.unit}"
            line += f"：{metric.expression}"
            if metric.filters:
                line += f"；过滤条件：{metric.filters}"
            if metric.description:
                line += f"；说明：{metric.description}"
            if metric.related_models:
                line += f"；涉及模型：{_join(metric.related_models)}"
            lines.append(line)
    else:
        lines.append("- 无预定义指标，请根据字段含义自行计算。")
    lines.append("")

    lines.append("## 维度")
    if ontology.dimensions:
        for dim in ontology.dimensions:
            line = f"- {dim.label}（同义词：{_join(dim.synonyms)}）：{dim.expression}"
            if dim.description:
                line += f"；{dim.description}"
            lines.append(line)
    else:
        lines.append("- 无预定义维度。")
    lines.append("")

    lines.append("## 业务规则（必须遵守）")
    for rule in ontology.rules:
        lines.append(f"- {rule}")
    lines.append("")

    if ontology.glossary:
        lines.append("## 术语表")
        for term, definition in ontology.glossary.items():
            lines.append(f"- {term}：{definition}")
        lines.append("")

    if ontology.examples:
        lines.append("## 已确认的 NL→SQL 示例")
        for example in ontology.examples[:8]:
            lines.append(f"问题：{example.question}")
            lines.append("```sql")
            lines.append(example.sql.strip())
            lines.append("```")
        lines.append("")
    return "\n".join(lines)


def render_context(
    scan: ScannedSchema, ontology: Ontology, manifest: dict[str, Any], conn: ConnectionConfig
) -> str:
    dialect = DIALECT_LABEL.get(scan.dialect, scan.dialect)
    parts = [
        f"目标数据库：{dialect}，数据库 {conn.database}，schema {scan.schema_name}。",
        "所有 SQL 必须使用下面列出的 MDL 模型名/视图名，不能直接使用物理表名。",
        DIALECT_NOTES.get(scan.dialect, ""),
        "",
        _render_models(scan, ontology, manifest),
        _render_ontology(ontology),
    ]
    return "\n".join(part for part in parts if part)


SYSTEM_PROMPT = """你是一名资深数据分析师，同时精通 Wren 语义层（MDL）和目标数据库 SQL。
你的任务：把用户的业务问题转换成一条可执行的只读 SQL，并选择最合适的图表。

必须遵守：
1. 只能使用上下文中列出的模型名、视图名和字段名。禁止编造表、字段或枚举值。
2. 只生成一条 SELECT 或 WITH...SELECT 查询。禁止 INSERT、UPDATE、DELETE、DROP、ALTER、CREATE、TRUNCATE、CALL、事务等任何写操作。
3. 金额单位默认为人民币元。除非用户明确要求，统计销售额/订单数/客户数/客单价时排除已取消、已退款订单；指标 expression 自带的 filters 必须原样加入 WHERE。
4. 订单级指标按订单主键去重计数；多表 JOIN 后不要直接 COUNT(*)。
5. 商品/品类分析优先使用 sales_line 视图（行金额字段 line_amount），不要重复累加订单总额。
6. 时间筛选和格式化必须使用目标数据库方言（见上下文中的方言说明）。
7. SELECT 的每个结果列都要有英文 snake_case 别名，别名要与 chart.x / chart.y 一致。
8. 明细列表类问题加 LIMIT 500；聚合结果通常不需要 LIMIT。
9. 不要使用 SELECT *；显式列出字段。
10. 当问题明确提到某个业务指标（销售额、订单数、客户数、客单价、毛利率、退款率等）时，必须直接使用上下文中该指标的 expression 和过滤条件，禁止自行改写口径。
11. 如果模型名、表名或字段名包含空格、中文或特殊字符，请按目标方言引用标识符（PostgreSQL 用双引号，MySQL 用反引号）。
12. 只输出一个 JSON 对象，不要输出 Markdown 代码围栏或额外解释。

JSON 格式：
{
  "title": "简洁的中文图表标题",
  "sql": "一条可直接执行的 SQL",
  "explanation": "用 1-3 句话说明统计口径、关键过滤和假设",
  "assumptions": ["需要用户知道的假设，没有则为空数组"],
  "chart": {
    "type": "line | bar | pie | scatter | kpi | table",
    "x": "x 轴或分类字段名，可为 null",
    "y": ["一个或多个数值字段名"],
    "series": "可选的系列字段名，可为 null",
    "reason": "为什么选择这个图表"
  }
}

图表选择规则：
- 时间趋势 → line；分类对比 → bar；占比/构成 → pie；两个数值字段相关性 → scatter。
- 结果只有一行一个数值 → kpi；用户问“有哪些/明细/列表” → table。
- 如果用户问两个指标之间的关系/相关性，先按时间或分类维度聚合，返回多行数据，再选择 scatter；不要只返回一行总计。
"""


def build_ask_messages(
    question: str,
    scan: ScannedSchema,
    ontology: Ontology,
    manifest: dict[str, Any],
    conn: ConnectionConfig,
    history: list[dict[str, str]] | None = None,
) -> list[dict[str, str]]:
    context = render_context(scan, ontology, manifest, conn)
    history_text = ""
    if history:
        recent = history[-4:]
        lines = ["## 最近对话（仅用于理解指代，不要照搬 SQL）"]
        for item in recent:
            role = "用户" if item.get("role") == "user" else "助手"
            lines.append(f"{role}：{item.get('content', '')[:600]}")
        history_text = "\n".join(lines)
    user = (
        f"{context}\n\n"
        f"{history_text}\n\n"
        f"## 用户问题\n{question}\n\n"
        "请输出 JSON。"
    )
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user},
    ]


def build_repair_messages(
    messages: list[dict[str, str]],
    previous: dict[str, Any],
    error: str,
) -> list[dict[str, str]]:
    repaired = list(messages)
    repaired.append(
        {
            "role": "assistant",
            "content": json.dumps(previous, ensure_ascii=False),
        }
    )
    repaired.append(
        {
            "role": "user",
            "content": (
                "上面的 SQL 执行失败。错误信息如下：\n"
                f"{error[:1500]}\n\n"
                "请根据错误修复 SQL。只使用上下文中列出的模型/视图和字段，"
                "保持只读 SELECT，并重新输出完整的 JSON（title、sql、explanation、assumptions、chart）。"
            ),
        }
    )
    return repaired
