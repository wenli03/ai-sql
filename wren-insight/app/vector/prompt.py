from __future__ import annotations

import json
from typing import Any

from ..models import ConnectionConfig, Ontology, ScannedSchema

SOURCE_LABELS = {
    "qdrant": "Qdrant",
    "chroma": "Chroma",
    "weaviate": "Weaviate",
    "milvus": "Milvus",
    "pinecone": "Pinecone",
    "pgvector": "pgvector",
}


def render_vector_context(
    scan: ScannedSchema,
    ontology: Ontology,
    conn: ConnectionConfig,
) -> str:
    lines: list[str] = [
        f"向量数据库：{SOURCE_LABELS.get(scan.dialect, scan.dialect)}",
        f"地址：{conn.host or ''}",
        "",
        "## 可查询的集合（只能使用这些名称）",
    ]
    for table in scan.tables:
        lines.append(f"### {table.name}")
        lines.append(
            f"记录数：{table.row_count}；向量字段：{table.vector_field or 'vector'}；"
            f"维度：{table.vector_dimension or '?'}；距离/相似度：{table.vector_metric or 'cosine'}"
        )
        if table.comment:
            lines.append(f"说明：{table.comment}")
        lines.append("字段：")
        for col in table.columns:
            if col.mdl_type == "VECTOR":
                continue
            sample = ""
            if col.sample_values:
                sample = "；样例：" + " / ".join(col.sample_values[:6])
            lines.append(f"- {col.name} ({col.mdl_type}) [{col.role}]{sample}")
        lines.append("")

    lines.append("## 指标口径")
    for metric in ontology.metrics:
        line = f"- {metric.label}（同义词：{'、'.join(metric.synonyms) or metric.label}）：{metric.expression}"
        if metric.description:
            line += f"；{metric.description}"
        lines.append(line)
    lines.append("")

    lines.append("## 可用维度")
    for dim in ontology.dimensions:
        lines.append(f"- {dim.label}（同义词：{'、'.join(dim.synonyms) or dim.label}）：{dim.expression}")
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
        lines.append("## 查询计划示例")
        for example in ontology.examples[:6]:
            lines.append(f"问题：{example.question}")
            lines.append("```json")
            lines.append(example.sql.strip())
            lines.append("```")
        lines.append("")
    return "\n".join(lines)


VECTOR_SYSTEM_PROMPT = """你是一名向量数据库数据分析师，精通 Chroma / Qdrant / Weaviate 等向量数据库的 metadata 统计与相似度检索。

你的任务：把用户问题转换成**一个只读的查询计划 JSON**，由程序执行。

必须遵守：
1. 只能使用上下文中列出的集合、metadata 字段和向量字段。禁止编造集合或字段。
2. 不生成 SQL；只输出 JSON 查询计划。禁止写入、删除、更新、创建 collection/index。
3. 统计、分组、计数、求和、平均、占比 → mode = "aggregate"。
4. 明细列表、按条件筛选记录 → mode = "list"。
5. 语义相似 / 找相关文档 / 相似推荐 → mode = "search"，把检索语义放进 search.text，不要把整句问题原样放入。
6. search.top_k 不超过 50；list/aggregate 的 limit 不超过 500；聚合最多扫描 5000 条 metadata。
7. 过滤条件只能使用 metadata 字段；op 只允许 eq / in / gte / lte / contains。
8. 不要输出原始向量；相似度搜索默认返回 id、score/distance、metadata 和文档摘要。
9. 只输出一个 JSON 对象，不要输出 Markdown 代码围栏或额外解释。

JSON 格式：
{
  "title": "简洁中文标题",
  "mode": "aggregate | list | search",
  "collection": "集合名称",
  "filters": [
    {"field": "category", "op": "eq | in | gte | lte | contains", "value": "..." }
  ],
  "group_by": ["category"],
  "metrics": [
    {"op": "count | sum | avg | min | max | distinct_count", "field": "*", "as": "doc_count"}
  ],
  "order_by": [{"field": "doc_count", "direction": "desc"}],
  "limit": 100,
  "search": {"text": "检索语义", "top_k": 5},
  "chart": {
    "type": "line | bar | pie | scatter | kpi | table",
    "x": "分组字段或 null",
    "y": ["指标字段"],
    "series": null,
    "reason": "为什么选择这个图表"
  },
  "explanation": "1-3 句口径与假设",
  "assumptions": ["需要用户知道的假设"]
}

图表选择：
- 分类对比 → bar；占比 → pie；单值 → kpi；明细/相似度结果 → table。
- 聚合结果只有一个数值 → kpi。
"""


def build_vector_messages(
    question: str,
    scan: ScannedSchema,
    ontology: Ontology,
    conn: ConnectionConfig,
    history: list[dict[str, str]] | None = None,
) -> list[dict[str, str]]:
    context = render_vector_context(scan, ontology, conn)
    history_text = ""
    if history:
        recent = history[-4:]
        lines = ["## 最近对话（仅用于理解指代）"]
        for item in recent:
            role = "用户" if item.get("role") == "user" else "助手"
            lines.append(f"{role}：{item.get('content', '')[:500]}")
        history_text = "\n".join(lines)
    user = (
        f"{context}\n\n{history_text}\n\n"
        f"## 用户问题\n{question}\n\n"
        "请输出查询计划 JSON。"
    )
    return [
        {"role": "system", "content": VECTOR_SYSTEM_PROMPT},
        {"role": "user", "content": user},
    ]


def build_vector_repair_messages(
    messages: list[dict[str, str]],
    previous: dict[str, Any],
    error: str,
) -> list[dict[str, str]]:
    repaired = list(messages)
    repaired.append({"role": "assistant", "content": json.dumps(previous, ensure_ascii=False)})
    repaired.append(
        {
            "role": "user",
            "content": (
                "上面的查询计划执行失败。错误信息如下：\n"
                f"{error[:1500]}\n\n"
                "请修复查询计划，只使用上下文中列出的集合和字段，"
                "保持只读，并重新输出完整 JSON。"
            ),
        }
    )
    return repaired
