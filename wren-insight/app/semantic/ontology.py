from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any

from ..db.introspect import infer_role
from ..models import (
    ColumnInfo,
    Ontology,
    OntologyAttribute,
    OntologyDimension,
    OntologyEntity,
    OntologyExample,
    OntologyMetric,
    OntologyRelationship,
    ScannedSchema,
    TableInfo,
)
from ..vector.base import VECTOR_SOURCES

TABLE_LABELS = {
    "customers": "客户", "customer": "客户", "users": "用户", "user": "用户",
    "members": "会员", "member": "会员",
    "orders": "订单", "order": "订单", "sales": "销售", "transactions": "交易",
    "order_items": "订单明细", "order_item": "订单明细", "order_lines": "订单明细",
    "products": "商品", "product": "商品", "items": "商品", "skus": "商品",
    "payments": "支付", "payment": "支付", "refunds": "退款", "refund": "退款",
    "categories": "品类", "category": "品类", "stores": "门店", "store": "门店",
    "channels": "渠道", "channel": "渠道", "suppliers": "供应商", "supplier": "供应商",
}

TABLE_SYNONYMS = {
    "customers": ["客户", "用户", "消费者", "会员", "buyer", "customer"],
    "orders": ["订单", "交易", "购买记录", "order", "sales"],
    "order_items": ["订单明细", "订单行", "商品明细", "line item", "order line"],
    "products": ["商品", "产品", "SKU", "货品", "item", "product"],
    "payments": ["支付", "付款", "收款", "payment", "transaction"],
    "refunds": ["退款", "退货", "refund"],
}

COLUMN_LABELS = {
    "id": "编号", "name": "名称", "customer_id": "客户编号", "order_id": "订单编号",
    "product_id": "商品编号", "order_date": "下单时间", "created_at": "创建时间",
    "updated_at": "更新时间", "status": "状态", "channel": "渠道", "province": "省份",
    "city": "城市", "region": "区域", "total_amount": "订单金额", "amount": "金额",
    "discount_amount": "优惠金额", "discount": "优惠金额", "price": "价格",
    "unit_price": "成交单价", "cost": "成本", "category": "品类",
    "sub_category": "二级品类", "quantity": "数量", "pay_method": "支付方式",
    "paid_at": "支付时间", "refunded": "是否退款", "level": "客户等级",
    "gender": "性别", "age": "年龄", "register_date": "注册日期", "email": "邮箱",
    "launch_date": "上架日期", "phone": "手机号", "brand": "品牌",
    "order_status": "订单状态", "payment_method": "支付方式",
    "source": "来源", "title": "标题", "content": "内容", "document": "文档",
    "priority": "优先级", "language": "语言", "created_at": "创建时间",
    "updated_at": "更新时间", "score": "相似度分数", "topic": "主题",
}

COLUMN_SYNONYMS = {
    "total_amount": ["销售额", "订单金额", "交易金额", "营收", "GMV"],
    "amount": ["金额", "交易金额", "销售额"],
    "order_date": ["下单时间", "购买时间", "交易日期", "日期"],
    "paid_at": ["支付时间", "付款时间"],
    "level": ["客户等级", "会员等级", "等级"],
    "category": ["品类", "类目", "商品类别"],
    "sub_category": ["二级品类", "子类目"],
    "province": ["省份", "地区", "区域"],
    "city": ["城市"],
    "channel": ["渠道", "来源"],
    "status": ["状态", "订单状态"],
    "pay_method": ["支付方式", "付款方式"],
    "quantity": ["数量", "销量", "件数"],
    "price": ["价格", "单价", "售价"],
    "cost": ["成本", "进价"],
    "revenue": ["销售额", "营收", "收入", "GMV"],
}

DEFAULT_EXCLUDE_STATUSES = [
    "cancelled", "canceled", "refunded", "closed", "已取消", "已退款", "取消", "退款",
]


def humanize_table(table: TableInfo) -> str:
    if table.comment:
        return table.comment.strip()[:40]
    return TABLE_LABELS.get(table.name.lower(), table.name)


def humanize_column(col: ColumnInfo) -> str:
    if col.comment:
        return col.comment.strip()[:40]
    return COLUMN_LABELS.get(col.name.lower(), col.name)


def model_name(table_name: str, used: set[str] | None = None) -> str:
    name = re.sub(r"[^0-9a-zA-Z_]+", "_", table_name).strip("_").lower()
    if not name:
        name = "model"
    if name[0].isdigit():
        name = "m_" + name
    if used is not None:
        base = name
        i = 2
        while name in used:
            name = f"{base}_{i}"
            i += 1
        used.add(name)
    return name


def _lower_map(tables: list[TableInfo]) -> dict[str, TableInfo]:
    return {t.name.lower(): t for t in tables}


def _find_column(table: TableInfo, *candidates: str) -> ColumnInfo | None:
    lower = {c.name.lower(): c for c in table.columns}
    for candidate in candidates:
        if candidate.lower() in lower:
            return lower[candidate.lower()]
    return None


def _first_of_role(table: TableInfo, role: str) -> ColumnInfo | None:
    for col in table.columns:
        if col.role == role:
            return col
    return None


def _pick_table(tables: list[TableInfo], preferred: list[str], predicate) -> TableInfo | None:
    by_name = _lower_map(tables)
    for name in preferred:
        if name in by_name and predicate(by_name[name]):
            return by_name[name]
    for table in tables:
        if predicate(table):
            return table
    return None


def _order_table(tables: list[TableInfo]) -> TableInfo | None:
    def pred(t: TableInfo) -> bool:
        cols = {c.name.lower() for c in t.columns}
        has_amount = any(c.role == "measure" for c in t.columns)
        return bool({"customer_id", "user_id"} & cols) and has_amount and any(c.role == "time" for c in t.columns)

    return _pick_table(tables, ["orders", "order", "sales", "transactions", "purchases"], pred)


def _item_table(tables: list[TableInfo]) -> TableInfo | None:
    def pred(t: TableInfo) -> bool:
        cols = {c.name.lower() for c in t.columns}
        return {"order_id", "product_id"} <= cols and "quantity" in cols

    return _pick_table(tables, ["order_items", "order_item", "order_lines", "order_details", "sales_items"], pred)


def _product_table(tables: list[TableInfo]) -> TableInfo | None:
    def pred(t: TableInfo) -> bool:
        cols = {c.name.lower() for c in t.columns}
        return "category" in cols and bool({"price", "cost"} & cols)

    return _pick_table(tables, ["products", "product", "items", "skus"], pred)


def _customer_table(tables: list[TableInfo]) -> TableInfo | None:
    def pred(t: TableInfo) -> bool:
        cols = {c.name.lower() for c in t.columns}
        return bool({"name", "customer_name"} & cols) and bool({"level", "city", "register_date", "gender"} & cols)

    return _pick_table(tables, ["customers", "customer", "users", "user", "members"], pred)


def _payment_table(tables: list[TableInfo]) -> TableInfo | None:
    def pred(t: TableInfo) -> bool:
        cols = {c.name.lower() for c in t.columns}
        return "order_id" in cols and bool({"pay_method", "payment_method", "paid_at", "amount"} & cols)

    return _pick_table(tables, ["payments", "payment", "transactions"], pred)


def _excluded_statuses(order_table: TableInfo | None) -> list[str]:
    if not order_table:
        return []
    status = _find_column(order_table, "status", "order_status", "state")
    if not status:
        return []
    found = []
    for value in status.sample_values:
        low = value.lower()
        if any(token in low for token in ("cancel", "refund", "closed", "取消", "退款", "关闭", "作废")):
            found.append(value)
    if found:
        return sorted(set(found))
    # Lakehouse metadata scans often skip expensive distinct-value probing, so
    # fall back to the common English/Chinese status values.
    return list(DEFAULT_EXCLUDE_STATUSES)


def _sql_literal_list(values: list[str]) -> str:
    return ", ".join("'" + v.replace("'", "''") + "'" for v in values)


def _time_expressions(dialect: str, col: str) -> tuple[str, str, str, str]:
    """Return (date, month, quarter, year) SQL expressions for a time column."""
    if dialect == "postgres":
        return (
            f"DATE({col})",
            f"TO_CHAR({col}, 'YYYY-MM')",
            f"CONCAT(EXTRACT(YEAR FROM {col}), '-Q', EXTRACT(QUARTER FROM {col}))",
            f"EXTRACT(YEAR FROM {col})",
        )
    if dialect == "mysql":
        return (
            f"DATE({col})",
            f"DATE_FORMAT({col}, '%Y-%m')",
            f"CONCAT(YEAR({col}), '-Q', QUARTER({col}))",
            f"YEAR({col})",
        )
    if dialect == "oracle":
        return (
            f"TRUNC({col})",
            f"TO_CHAR({col}, 'YYYY-MM')",
            f"TO_CHAR({col}, 'YYYY-\"Q\"Q')",
            f"EXTRACT(YEAR FROM {col})",
        )
    if dialect == "mssql":
        return (
            f"CAST({col} AS DATE)",
            f"FORMAT({col}, 'yyyy-MM')",
            f"CONCAT(YEAR({col}), '-Q', DATEPART(quarter, {col}))",
            f"YEAR({col})",
        )
    if dialect == "trino":
        return (
            f"CAST({col} AS DATE)",
            f"date_format({col}, '%Y-%m')",
            f"concat(CAST(year({col}) AS varchar), '-Q', CAST(quarter({col}) AS varchar))",
            f"year({col})",
        )
    if dialect in ("databricks", "spark"):
        return (
            f"CAST({col} AS DATE)",
            f"date_format({col}, 'yyyy-MM')",
            f"CONCAT(YEAR({col}), '-Q', QUARTER({col}))",
            f"YEAR({col})",
        )
    if dialect == "athena":
        return (
            f"CAST({col} AS DATE)",
            f"date_format({col}, '%Y-%m')",
            f"concat(CAST(year({col}) AS varchar), '-Q', CAST(quarter({col}) AS varchar))",
            f"year({col})",
        )
    if dialect == "duckdb":
        return (
            f"CAST({col} AS DATE)",
            f"strftime({col}, '%Y-%m')",
            f"CONCAT(YEAR({col}), '-Q', QUARTER({col}))",
            f"YEAR({col})",
        )
    if dialect == "clickhouse":
        return (
            f"toDate({col})",
            f"formatDateTime({col}, '%Y-%m')",
            f"concat(toString(toYear({col})), '-Q', toString(toQuarter({col})))",
            f"toYear({col})",
        )
    if dialect == "bigquery":
        return (
            f"DATE({col})",
            f"FORMAT_DATE('%Y-%m', {col})",
            f"CONCAT(CAST(EXTRACT(YEAR FROM {col}) AS STRING), '-Q', CAST(EXTRACT(QUARTER FROM {col}) AS STRING))",
            f"EXTRACT(YEAR FROM {col})",
        )
    return (
        f"CAST({col} AS DATE)",
        f"SUBSTR(CAST({col} AS STRING), 1, 7)",
        f"CAST({col} AS STRING)",
        f"CAST({col} AS STRING)",
    )


def _months_ago_filter(dialect: str, col: str, months: int = 6) -> str:
    if dialect == "postgres":
        return f"{col} >= CURRENT_DATE - INTERVAL '{months} months'"
    if dialect == "mysql":
        return f"{col} >= DATE_SUB(CURDATE(), INTERVAL {months} MONTH)"
    if dialect == "oracle":
        return f"{col} >= ADD_MONTHS(SYSDATE, -{months})"
    if dialect == "mssql":
        return f"{col} >= DATEADD(month, -{months}, GETDATE())"
    if dialect == "trino":
        return f"{col} >= current_date - INTERVAL '{months}' month"
    if dialect in ("databricks", "spark"):
        return f"{col} >= current_date() - INTERVAL {months} MONTH"
    if dialect == "athena":
        return f"{col} >= date_add('month', -{months}, current_date)"
    if dialect == "duckdb":
        return f"{col} >= CURRENT_DATE - INTERVAL {months} MONTH"
    if dialect == "clickhouse":
        return f"{col} >= now() - INTERVAL {months} MONTH"
    if dialect == "bigquery":
        return f"{col} >= DATE_SUB(CURRENT_DATE(), INTERVAL {months} MONTH)"
    return f"{col} >= CURRENT_DATE - INTERVAL '{months}' MONTH"


def _build_vector_ontology(scan: ScannedSchema) -> Ontology:
    entities: list[OntologyEntity] = []
    metrics: list[OntologyMetric] = []
    dimensions: list[OntologyDimension] = []
    examples: list[OntologyExample] = []

    for table in scan.tables:
        collection = table.name
        attributes = []
        for col in table.columns:
            if col.mdl_type == "VECTOR":
                continue
            label = humanize_column(col)
            attributes.append(
                OntologyAttribute(
                    name=label,
                    column=col.name,
                    label=label,
                    synonyms=COLUMN_SYNONYMS.get(col.name.lower(), [col.name]),
                    description=col.comment or "",
                    role=col.role,
                )
            )
        entities.append(
            OntologyEntity(
                name=collection,
                model=collection,
                label=collection,
                synonyms=[collection, collection.replace("_", " ")],
                description=table.comment or f"向量集合 {collection}",
                key="id",
                attributes=attributes,
            )
        )
        metrics.append(
            OntologyMetric(
                name="记录数",
                label="记录数",
                synonyms=["记录数", "文档数", "数量", "条数", "count", "总数"],
                expression="COUNT(*)",
                description=f"集合 {collection} 的记录总数。",
                related_models=[collection],
                unit="条",
            )
        )
        numeric_columns = [c for c in table.columns if c.role == "measure"]
        for col in numeric_columns:
            label = humanize_column(col)
            metrics.append(
                OntologyMetric(
                    name=f"{label}平均值",
                    label=f"{label}平均值",
                    synonyms=[f"平均{label}", f"avg {col.name}"],
                    expression=f"AVG({col.name})",
                    description=f"{collection} 中 {col.name} 的平均值。",
                    related_models=[collection],
                )
            )
            metrics.append(
                OntologyMetric(
                    name=f"{label}总和",
                    label=f"{label}总和",
                    synonyms=[f"{label}总和", f"sum {col.name}"],
                    expression=f"SUM({col.name})",
                    description=f"{collection} 中 {col.name} 的总和。",
                    related_models=[collection],
                )
            )
        dim_columns = [
            c for c in table.columns
            if c.role in ("dimension", "time") and c.name not in ("id", "document")
        ]
        if dim_columns:
            first = next((c for c in dim_columns if c.role == "dimension"), dim_columns[0])
            metrics.append(
                OntologyMetric(
                    name=f"{humanize_column(first)}种类数",
                    label=f"{humanize_column(first)}种类数",
                    synonyms=[f"{humanize_column(first)}种类数", f"distinct {first.name}"],
                    expression=f"COUNT(DISTINCT {first.name})",
                    description=f"{collection} 中 {first.name} 的去重数量。",
                    related_models=[collection],
                )
            )
        for col in dim_columns:
            label = humanize_column(col)
            dimensions.append(
                OntologyDimension(
                    name=col.name,
                    label=label,
                    synonyms=COLUMN_SYNONYMS.get(col.name.lower(), [col.name]),
                    expression=f"{collection}.{col.name}",
                    model=collection,
                    description=col.comment or "",
                )
            )
        categorical = next((c for c in dim_columns if c.role == "dimension"), None)
        time_col = next((c for c in dim_columns if c.role == "time"), None)
        if categorical:
            examples.append(
                OntologyExample(
                    question=f"按 {categorical.name} 统计文档数量",
                    sql=json.dumps(
                        {
                            "mode": "aggregate",
                            "collection": collection,
                            "group_by": [categorical.name],
                            "metrics": [{"op": "count", "field": "*", "as": "doc_count"}],
                            "order_by": [{"field": "doc_count", "direction": "desc"}],
                            "chart": {"type": "bar", "x": categorical.name, "y": ["doc_count"]},
                        },
                        ensure_ascii=False,
                    ),
                    tags=["聚合", "统计", categorical.name],
                )
            )
            examples.append(
                OntologyExample(
                    question=f"各 {categorical.name} 的文档数量占比",
                    sql=json.dumps(
                        {
                            "mode": "aggregate",
                            "collection": collection,
                            "group_by": [categorical.name],
                            "metrics": [{"op": "count", "field": "*", "as": "doc_count"}],
                            "chart": {"type": "pie", "x": categorical.name, "y": ["doc_count"]},
                        },
                        ensure_ascii=False,
                    ),
                    tags=["占比", categorical.name],
                )
            )
        examples.append(
            OntologyExample(
                question="找与退款政策最相似的5条文档",
                sql=json.dumps(
                    {
                        "mode": "search",
                        "collection": collection,
                        "search": {"text": "退款政策", "top_k": 5},
                        "chart": {"type": "table"},
                    },
                    ensure_ascii=False,
                ),
                tags=["相似度", "向量检索"],
            )
        )
        if time_col:
            examples.append(
                OntologyExample(
                    question="最近30天新增的文档",
                    sql=json.dumps(
                        {
                            "mode": "list",
                            "collection": collection,
                            "filters": [{"field": time_col.name, "op": "gte", "value": "<30天前的 ISO 时间>"}],
                            "limit": 50,
                            "chart": {"type": "table"},
                        },
                        ensure_ascii=False,
                    ),
                    tags=["时间", "列表"],
                )
            )

    rules = [
        "向量数据库不执行 SQL；请输出查询计划 JSON。",
        "相似度搜索必须指定 search.text 与 top_k，top_k 不超过 50。",
        "聚合/列表默认最多扫描 5000 条记录，limit 不超过 500。",
        "过滤条件只能使用已列出的 metadata 字段；不要过滤或输出原始向量。",
        "cosine/dot 相似度分数越高越相似；euclid/manhattan 距离越小越相似。",
        "默认返回 metadata 与文档摘要，不返回完整向量。",
        "相似度搜索时把检索语义放进 search.text，不要把整句问题原样放入。",
        "聚合统计基于 metadata；超大集合建议在向量库侧建立索引或物化统计。",
    ]
    glossary = {
        "向量检索": "把问题文本转成向量，在向量库中查找距离最近/分数最高的记录。",
        "top_k": "相似度搜索返回的记录数量上限。",
        "metadata": "与向量一起存储的结构化字段，可用于过滤、分组和统计。",
        "collection": "向量库中的集合 / class / index，对应关系数据库中的表。",
    }
    return Ontology(
        version=1,
        domain="知识库 / 向量检索",
        dialect=scan.dialect,
        generated_at=datetime.now(),
        entities=entities,
        relationships=[],
        metrics=metrics,
        dimensions=dimensions,
        rules=rules,
        examples=examples,
        glossary=glossary,
    )


def build_ontology(scan: ScannedSchema) -> Ontology:
    if scan.dialect in VECTOR_SOURCES:
        return _build_vector_ontology(scan)
    dialect = scan.dialect
    tables = scan.tables
    used_names: set[str] = set()
    name_by_table: dict[str, str] = {}
    for table in tables:
        name_by_table[table.name] = model_name(table.name, used_names)

    # ---- entities -------------------------------------------------------
    entities: list[OntologyEntity] = []
    for table in tables:
        attributes = []
        for col in table.columns:
            role = infer_role(col.name, col.mdl_type, col.is_primary_key or col.is_foreign_key)
            attributes.append(
                OntologyAttribute(
                    name=humanize_column(col),
                    column=col.name,
                    label=humanize_column(col),
                    synonyms=COLUMN_SYNONYMS.get(col.name.lower(), []),
                    description=(col.comment or "").strip(),
                    role=role,
                )
            )
        pk = table.primary_key[0] if table.primary_key else (table.columns[0].name if table.columns else None)
        label = humanize_table(table)
        synonyms = list(TABLE_SYNONYMS.get(table.name.lower(), []))
        if label not in synonyms:
            synonyms.insert(0, label)
        entities.append(
            OntologyEntity(
                name=label,
                model=name_by_table[table.name],
                label=label,
                synonyms=synonyms,
                description=(
                    table.comment
                    or f"{label}表，约 {table.row_count} 行，包含 {len(table.columns)} 个字段。"
                ),
                key=pk,
                attributes=attributes,
            )
        )

    # ---- relationships --------------------------------------------------
    relationships: list[OntologyRelationship] = []
    seen_rel: set[tuple[str, str, str, str]] = set()

    def add_rel(from_model: str, from_col: str, to_model: str, to_col: str, description: str = "") -> None:
        key = (from_model, from_col, to_model, to_col)
        if key in seen_rel or from_model == to_model:
            return
        seen_rel.add(key)
        relationships.append(
            OntologyRelationship(
                name=f"{from_model}_{to_model}",
                from_model=from_model,
                from_column=from_col,
                to_model=to_model,
                to_column=to_col,
                join_type="MANY_TO_ONE",
                description=description,
            )
        )

    table_by_name = {t.name: t for t in tables}
    for table in tables:
        from_model = name_by_table[table.name]
        for fk in table.foreign_keys:
            if fk.ref_table not in table_by_name or not fk.columns or not fk.ref_columns:
                continue
            add_rel(
                from_model, fk.columns[0], name_by_table[fk.ref_table], fk.ref_columns[0],
                f"{humanize_table(table)} → {humanize_table(table_by_name[fk.ref_table])}",
            )
        # Heuristic for databases without FK metadata.
        for col in table.columns:
            low = col.name.lower()
            if not low.endswith("_id") or col.is_foreign_key:
                continue
            base = low[:-3]
            candidates = [base, base + "s", base[:-1] + "ies" if base.endswith("y") else base]
            for candidate in candidates:
                if candidate in _lower_map(tables) and candidate != table.name.lower():
                    ref = _lower_map(tables)[candidate]
                    ref_key = ref.primary_key[0] if ref.primary_key else ("id" if _find_column(ref, "id") else None)
                    if ref_key:
                        add_rel(
                            from_model, col.name, name_by_table[ref.name], ref_key,
                            f"{humanize_table(table)} → {humanize_table(ref)}",
                        )
                    break

    # ---- metrics & dimensions ------------------------------------------
    order = _order_table(tables)
    items = _item_table(tables)
    products = _product_table(tables)
    customers = _customer_table(tables)
    payments = _payment_table(tables)

    metrics: list[OntologyMetric] = []
    dimensions: list[OntologyDimension] = []
    rules: list[str] = []
    examples: list[OntologyExample] = []

    order_model = name_by_table[order.name] if order else None
    item_model = name_by_table[items.name] if items else None
    product_model = name_by_table[products.name] if products else None
    customer_model = name_by_table[customers.name] if customers else None
    payment_model = name_by_table[payments.name] if payments else None

    order_amount = _find_column(order, "total_amount", "amount", "order_amount", "payment_amount") if order else None
    order_status = _find_column(order, "status", "order_status", "state") if order else None
    order_date = _find_column(order, "order_date", "created_at", "paid_at", "date") if order else None
    order_customer = _find_column(order, "customer_id", "user_id", "member_id") if order else None
    order_pk = _find_column(order, "id", "order_id") if order else None
    excluded = _excluded_statuses(order)
    revenue_filter = ""
    if order_model and order_status and excluded:
        revenue_filter = f"{order_model}.{order_status.name} NOT IN ({_sql_literal_list(excluded)})"

    if order_model and order_amount:
        metrics.append(
            OntologyMetric(
                name="销售额",
                label="销售额",
                synonyms=["销售额", "营收", "收入", "销售金额", "GMV", "revenue", "sales"],
                expression=f"SUM({order_model}.{order_amount.name})",
                description="统计有效订单的成交金额总和，默认排除已取消和已退款订单。",
                filters=revenue_filter or None,
                related_models=[order_model],
                unit="元",
            )
        )
    if order_model and order_pk:
        metrics.append(
            OntologyMetric(
                name="订单数",
                label="订单数",
                synonyms=["订单数", "订单量", "单量", "交易笔数", "order count"],
                expression=f"COUNT(DISTINCT {order_model}.{order_pk.name})",
                description="有效订单的去重数量。",
                filters=revenue_filter or None,
                related_models=[order_model],
                unit="单",
            )
        )
    if order_model and order_customer:
        metrics.append(
            OntologyMetric(
                name="客户数",
                label="客户数",
                synonyms=["客户数", "买家数", "下单用户数", "customer count", "buyers"],
                expression=f"COUNT(DISTINCT {order_model}.{order_customer.name})",
                description="产生有效订单的去重客户数量。",
                filters=revenue_filter or None,
                related_models=[order_model],
                unit="人",
            )
        )
    if order_model and order_amount and order_pk:
        metrics.append(
            OntologyMetric(
                name="客单价",
                label="客单价",
                synonyms=["客单价", "平均订单金额", "AOV", "average order value"],
                expression=(
                    f"SUM({order_model}.{order_amount.name}) "
                    f"/ NULLIF(COUNT(DISTINCT {order_model}.{order_pk.name}), 0)"
                ),
                description="销售额 ÷ 订单数。",
                filters=revenue_filter or None,
                related_models=[order_model],
                unit="元",
            )
        )
    if order_model and order_status:
        refunded_values = [
            v for v in order_status.sample_values if "退" in v or "refund" in v.lower()
        ] or ["refunded", "已退款"]
        cancelled_values = [
            v for v in order_status.sample_values if "cancel" in v.lower() or "取消" in v
        ]
        refund_filter = (
            f"{order_model}.{order_status.name} NOT IN ({_sql_literal_list(cancelled_values)})"
            if cancelled_values
            else None
        )
        metrics.append(
            OntologyMetric(
                name="退款率",
                label="退款率",
                synonyms=["退款率", "退货率", "refund rate"],
                expression=(
                    f"SUM(CASE WHEN {order_model}.{order_status.name} IN "
                    f"({_sql_literal_list(refunded_values)}) "
                    f"THEN 1 ELSE 0 END) * 100.0 / NULLIF(COUNT(*), 0)"
                ),
                description="退款订单数 ÷ 有效订单数 × 100%，默认排除已取消订单。",
                filters=refund_filter,
                related_models=[order_model],
                unit="%",
            )
        )

    if item_model:
        qty = _find_column(items, "quantity", "qty") if items else None
        if qty:
            metrics.append(
                OntologyMetric(
                    name="销量",
                    label="销量",
                    synonyms=["销量", "销售件数", "商品件数", "quantity sold"],
                    expression=f"SUM({item_model}.{qty.name})",
                    description="订单明细中的商品件数总和。",
                    related_models=[item_model],
                    unit="件",
                )
            )
        unit_price = _find_column(items, "unit_price", "price") if items else None
        discount = _find_column(items, "discount", "discount_amount") if items else None
        if qty and unit_price:
            line_amount = f"{item_model}.{qty.name} * {item_model}.{unit_price.name}"
            if discount:
                line_amount += f" - COALESCE({item_model}.{discount.name}, 0)"
            metrics.append(
                OntologyMetric(
                    name="商品销售额",
                    label="商品销售额",
                    synonyms=["商品销售额", "明细销售额", "行金额", "line revenue"],
                    expression=f"SUM({line_amount})",
                    description="按订单明细行计算的商品成交金额，适合与商品、品类维度一起使用。",
                    related_models=[item_model],
                    unit="元",
                )
            )
            if product_model:
                cost = _find_column(products, "cost", "unit_cost") if products else None
                if cost and qty:
                    metrics.append(
                        OntologyMetric(
                            name="销售成本",
                            label="销售成本",
                            synonyms=["销售成本", "商品成本", "COGS"],
                            expression=f"SUM({item_model}.{qty.name} * {product_model}.{cost.name})",
                            description="按商品成本计算的销售成本，需要关联商品表。",
                            related_models=[item_model, product_model],
                            unit="元",
                        )
                    )
                    metrics.append(
                        OntologyMetric(
                            name="毛利率",
                            label="毛利率",
                            synonyms=["毛利率", "毛利", "gross margin"],
                            expression=(
                                f"(SUM({line_amount}) - SUM({item_model}.{qty.name} * {product_model}.{cost.name})) "
                                f"* 100.0 / NULLIF(SUM({line_amount}), 0)"
                            ),
                            description="(商品销售额 - 销售成本) ÷ 商品销售额 × 100%。",
                            related_models=[item_model, product_model],
                            unit="%",
                        )
                    )

    if customer_model:
        cust_pk = _find_column(customers, "id", "customer_id") if customers else None
        if cust_pk:
            metrics.append(
                OntologyMetric(
                    name="客户总数",
                    label="客户总数",
                    synonyms=["客户总数", "用户总数", "会员总数"],
                    expression=f"COUNT(DISTINCT {customer_model}.{cust_pk.name})",
                    description="客户表中的去重客户数量。",
                    related_models=[customer_model],
                    unit="人",
                )
            )
        level = _find_column(customers, "level", "member_level", "tier") if customers else None
        if level and cust_pk:
            metrics.append(
                OntologyMetric(
                    name="高价值客户数",
                    label="高价值客户数",
                    synonyms=["高价值客户", "VIP客户", "核心客户", "high value customer"],
                    expression=(
                        f"COUNT(DISTINCT CASE WHEN {customer_model}.{level.name} IN ('金卡','黑卡') "
                        f"THEN {customer_model}.{cust_pk.name} END)"
                    ),
                    description="客户等级为金卡或黑卡的客户数量；高价值客户也可按近90天消费金额定义。",
                    related_models=[customer_model],
                    unit="人",
                )
            )

    if payment_model:
        pay_amount = _find_column(payments, "amount", "total_amount") if payments else None
        if pay_amount:
            metrics.append(
                OntologyMetric(
                    name="支付金额",
                    label="支付金额",
                    synonyms=["支付金额", "收款金额", "实收金额"],
                    expression=f"SUM({payment_model}.{pay_amount.name})",
                    description="支付流水中的成功支付金额。",
                    related_models=[payment_model],
                    unit="元",
                )
            )

    # Dimensions -----------------------------------------------------------
    def add_dimension(name: str, label: str, synonyms: list[str], expression: str, model: str, description: str = "") -> None:
        if any(d.name == name for d in dimensions):
            return
        dimensions.append(
            OntologyDimension(
                name=name, label=label, synonyms=synonyms, expression=expression,
                model=model, description=description,
            )
        )

    if order_model and order_date:
        col = f"{order_model}.{order_date.name}"
        date_expr, month_expr, quarter_expr, year_expr = _time_expressions(dialect, col)
        add_dimension("下单日期", "下单日期", ["日期", "天", "daily"], date_expr, order_model)
        add_dimension("下单月份", "下单月份", ["月份", "月度", "monthly"], month_expr, order_model)
        add_dimension("下单季度", "下单季度", ["季度", "quarter"], quarter_expr, order_model)
        add_dimension("下单年份", "下单年份", ["年份", "年度", "yearly"], year_expr, order_model)

    def add_categorical_dimensions(table: TableInfo | None, model: str | None, columns: list[str]) -> None:
        if not table or not model:
            return
        for column in columns:
            col = _find_column(table, column)
            if not col:
                continue
            add_dimension(
                col.name, humanize_column(col), COLUMN_SYNONYMS.get(col.name.lower(), [col.name]),
                f"{model}.{col.name}", model,
            )

    add_categorical_dimensions(order, order_model, ["status", "channel", "province", "city"])
    add_categorical_dimensions(customers, customer_model, ["level", "city", "province", "gender"])
    add_categorical_dimensions(products, product_model, ["category", "sub_category", "brand"])
    add_categorical_dimensions(payments, payment_model, ["pay_method", "payment_method"])

    # ---- business rules --------------------------------------------------
    rules.extend([
        "金额单位默认为人民币元。",
        "统计销售额、订单数、客户数、客单价时，默认排除已取消和已退款订单（除非用户明确要求包含）。",
        "订单级指标必须按订单主键去重；不要在多表 JOIN 后直接 COUNT(*)。",
        "商品/品类分析使用订单明细行金额（数量 × 成交单价 - 优惠），不要直接累加订单总额，避免明细行重复计算。",
    ])
    if excluded:
        rules.append(f"当前数据中应排除的订单状态：{', '.join(excluded)}。")
    if customer_model and _find_column(customers, "level"):
        rules.append("高价值客户默认定义为客户等级为「金卡」或「黑卡」；如按消费金额定义，需先按客户聚合近90天消费。")
    if order_model and order_amount:
        rules.append(f"销售额字段为 {order_model}.{order_amount.name}。")
    if order_model and order_date:
        rules.append(f"时间字段为 {order_model}.{order_date.name}，趋势分析优先按月聚合。")

    # ---- examples --------------------------------------------------------
    if order_model and order_amount and order_date:
        _, month_expr, _, _ = _time_expressions(dialect, f"{order_model}.{order_date.name}")
        where_parts = [_months_ago_filter(dialect, f"{order_model}.{order_date.name}", 6)]
        if order_status and excluded:
            where_parts.append(
                f"{order_model}.{order_status.name} NOT IN ({_sql_literal_list(excluded)})"
            )
        examples.append(
            OntologyExample(
                question="最近6个月每月的销售额趋势",
                sql=(
                    f"SELECT {month_expr} AS month, "
                    f"ROUND(SUM({order_model}.{order_amount.name}), 2) AS revenue "
                    f"FROM {order_model} WHERE {' AND '.join(where_parts)} "
                    "GROUP BY 1 ORDER BY 1"
                ),
                tags=["趋势", "销售额", "月份"],
            )
        )

    if item_model and product_model and items and products:
        qty = _find_column(items, "quantity")
        price = _find_column(items, "unit_price", "price")
        disc = _find_column(items, "discount")
        category = _find_column(products, "category")
        order_id_col = _find_column(items, "order_id")
        order_pk_col = _find_column(order, "id") if order else None
        product_id_col = _find_column(items, "product_id")
        product_pk_col = _find_column(products, "id")
        if qty and price and category and order_id_col and order_pk_col and product_id_col and product_pk_col:
            amount_expr = f"{item_model}.{qty.name} * {item_model}.{price.name}"
            if disc:
                amount_expr += f" - COALESCE({item_model}.{disc.name}, 0)"
            where = ""
            if order_status and excluded:
                where = f"WHERE {order_model}.{order_status.name} NOT IN ({_sql_literal_list(excluded)}) "
            examples.append(
                OntologyExample(
                    question="各品类销售额排行",
                    sql=(
                        f"SELECT {product_model}.{category.name} AS category, "
                        f"ROUND(SUM({amount_expr}), 2) AS revenue "
                        f"FROM {item_model} "
                        f"JOIN {order_model} ON {item_model}.{order_id_col.name} = {order_model}.{order_pk_col.name} "
                        f"JOIN {product_model} ON {item_model}.{product_id_col.name} = {product_model}.{product_pk_col.name} "
                        + where
                        + "GROUP BY 1 ORDER BY revenue DESC"
                    ),
                    tags=["品类", "销售额", "排行"],
                )
            )

    if order_model and order_amount and order_pk:
        channel = _find_column(order, "channel", "source", "order_source") if order else None
        if channel:
            where = ""
            if order_status and excluded:
                where = f"WHERE {order_model}.{order_status.name} NOT IN ({_sql_literal_list(excluded)}) "
            if dialect == "postgres":
                example_sql = (
                    f"SELECT {order_model}.{channel.name} AS channel, "
                    f"COUNT(DISTINCT {order_model}.{order_pk.name}) AS order_count, "
                    f"ROUND(SUM({order_model}.{order_amount.name})::numeric "
                    f"/ NULLIF(COUNT(DISTINCT {order_model}.{order_pk.name}), 0), 2) AS aov "
                    f"FROM {order_model} {where}GROUP BY 1 ORDER BY order_count DESC"
                )
            else:
                example_sql = (
                    f"SELECT {order_model}.{channel.name} AS channel, "
                    f"COUNT(DISTINCT {order_model}.{order_pk.name}) AS order_count, "
                    f"ROUND(SUM({order_model}.{order_amount.name}) "
                    f"/ NULLIF(COUNT(DISTINCT {order_model}.{order_pk.name}), 0), 2) AS aov "
                    f"FROM {order_model} {where}GROUP BY 1 ORDER BY order_count DESC"
                )
            examples.append(
                OntologyExample(
                    question="各渠道的订单数和客单价对比",
                    sql=example_sql,
                    tags=["渠道", "订单数", "客单价"],
                )
            )

    if customer_model and customers:
        level = _find_column(customers, "level", "member_level", "tier")
        pk = _find_column(customers, "id", "customer_id")
        name = _find_column(customers, "name", "customer_name")
        if level and pk:
            cols = [f"{customer_model}.{pk.name}"]
            if name:
                cols.append(f"{customer_model}.{name.name}")
            cols.append(f"{customer_model}.{level.name}")
            examples.append(
                OntologyExample(
                    question="高价值客户有哪些",
                    sql=(
                        f"SELECT {', '.join(cols)} FROM {customer_model} "
                        f"WHERE {customer_model}.{level.name} IN ('金卡','黑卡') "
                        f"ORDER BY {customer_model}.{level.name} LIMIT 100"
                    ),
                    tags=["高价值客户", "客户"],
                )
            )

    glossary = {
        "销售额": "有效订单的成交金额总和，默认排除已取消和已退款订单。",
        "订单数": "有效订单的去重数量。",
        "客单价": "销售额除以订单数，反映平均每笔订单的金额。",
        "高价值客户": "客户等级为金卡或黑卡的客户；也可按近90天消费金额定义。",
        "商品销售额": "按订单明细行计算的商品成交金额，适合与品类、商品维度一起分析。",
    }

    return Ontology(
        version=1,
        domain="电商零售" if order_model and product_model else "业务数据",
        dialect=dialect,
        generated_at=datetime.now(),
        entities=entities,
        relationships=relationships,
        metrics=metrics,
        dimensions=dimensions,
        rules=rules,
        examples=examples,
        glossary=glossary,
    )
