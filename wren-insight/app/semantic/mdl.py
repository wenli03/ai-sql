from __future__ import annotations

import base64
import json
import re
from pathlib import Path
from typing import Any

import yaml

from ..models import ConnectionConfig, Ontology, ScannedSchema, TableInfo
from .ontology import humanize_column, humanize_table, model_name


def _q(text: str) -> str:
    return "'" + text.replace("'", "''") + "'"


def _safe_yaml(data: dict[str, Any]) -> str:
    return yaml.safe_dump(data, sort_keys=False, allow_unicode=True, default_flow_style=False)


def _find(table: TableInfo | None, *names: str):
    if not table:
        return None
    lower = {c.name.lower(): c for c in table.columns}
    for name in names:
        col = lower.get(name.lower())
        if col:
            return col
    return None


def _model_map(scan: ScannedSchema) -> dict[str, str]:
    used: set[str] = set()
    return {table.name: model_name(table.name, used) for table in scan.tables}


def _table_by_lower(scan: ScannedSchema) -> dict[str, TableInfo]:
    return {t.name.lower(): t for t in scan.tables}


def _pick(scan: ScannedSchema, preferred: list[str], predicate) -> TableInfo | None:
    by_name = _table_by_lower(scan)
    for name in preferred:
        if name in by_name and predicate(by_name[name]):
            return by_name[name]
    for table in scan.tables:
        if predicate(table):
            return table
    return None


def _detect_roles(scan: ScannedSchema) -> dict[str, TableInfo | None]:
    def order_pred(t: TableInfo) -> bool:
        cols = {c.name.lower() for c in t.columns}
        return bool({"customer_id", "user_id"} & cols) and any(c.role == "measure" for c in t.columns)

    def item_pred(t: TableInfo) -> bool:
        cols = {c.name.lower() for c in t.columns}
        return {"order_id", "product_id"} <= cols and "quantity" in cols

    def product_pred(t: TableInfo) -> bool:
        cols = {c.name.lower() for c in t.columns}
        return "category" in cols and bool({"price", "cost"} & cols)

    def customer_pred(t: TableInfo) -> bool:
        cols = {c.name.lower() for c in t.columns}
        return bool({"name", "customer_name"} & cols) and bool({"level", "city", "register_date"} & cols)

    return {
        "order": _pick(scan, ["orders", "order", "sales", "transactions"], order_pred),
        "item": _pick(scan, ["order_items", "order_item", "order_lines", "order_details"], item_pred),
        "product": _pick(scan, ["products", "product", "items", "skus"], product_pred),
        "customer": _pick(scan, ["customers", "customer", "users", "members"], customer_pred),
    }


def write_project(project_dir: Path, conn: ConnectionConfig, scan: ScannedSchema, ontology: Ontology) -> Path:
    project_dir.mkdir(parents=True, exist_ok=True)
    models_dir = project_dir / "models"
    views_dir = project_dir / "views"
    knowledge_dir = project_dir / "knowledge"
    for directory in (models_dir, views_dir, knowledge_dir / "rules", knowledge_dir / "sql"):
        directory.mkdir(parents=True, exist_ok=True)

    name_map = _model_map(scan)
    roles = _detect_roles(scan)
    table_by_name = {t.name: t for t in scan.tables}

    config = {
        "schema_version": 5,
        "name": re.sub(r"[^a-zA-Z0-9_]+", "_", conn.id).strip("_") or "wren_insight",
        "catalog": "wren",
        "schema": scan.schema_name or "public",
        "data_source": scan.dialect,
    }
    (project_dir / "wren_project.yml").write_text(_safe_yaml(config), encoding="utf-8")

    # ---- models ---------------------------------------------------------
    for table in scan.tables:
        model = name_map[table.name]
        columns = []
        for col in table.columns:
            columns.append({
                "name": col.name,
                "type": col.mdl_type,
                "is_calculated": False,
                "not_null": not col.nullable,
                "is_primary_key": col.is_primary_key,
                "properties": {
                    "description": " ".join(
                        part for part in [humanize_column(col), col.comment or ""] if part
                    ).strip(),
                },
            })
        metadata: dict[str, Any] = {
            "name": model,
            "table_reference": {
                "catalog": table.catalog or "",
                "schema": table.schema_name or scan.schema_name,
                "table": table.name,
            },
            "columns": columns,
            "cached": False,
            "properties": {
                "description": " ".join(
                    part for part in [
                        table.comment or f"{humanize_table(table)}（{table.name}）",
                        f"[{table.table_type}]" if table.table_type == "VIEW" else "",
                        f"location={table.location}" if table.location else "",
                    ] if part
                ),
            },
        }
        if table.primary_key:
            metadata["primary_key"] = table.primary_key[0]
        model_dir = models_dir / model
        model_dir.mkdir(parents=True, exist_ok=True)
        (model_dir / "metadata.yml").write_text(_safe_yaml(metadata), encoding="utf-8")

    # ---- relationships --------------------------------------------------
    relationships = []
    seen: set[tuple[str, str, str, str]] = set()
    for rel in ontology.relationships:
        key = (rel.from_model, rel.from_column, rel.to_model, rel.to_column)
        if key in seen or rel.from_model == rel.to_model:
            continue
        seen.add(key)
        relationships.append({
            "name": f"{rel.from_model}_{rel.to_model}_{len(relationships) + 1}",
            "models": [rel.from_model, rel.to_model],
            "join_type": rel.join_type or "MANY_TO_ONE",
            "condition": f"{rel.from_model}.{rel.from_column} = {rel.to_model}.{rel.to_column}",
        })
    (project_dir / "relationships.yml").write_text(
        _safe_yaml({"relationships": relationships}), encoding="utf-8"
    )

    # ---- views ----------------------------------------------------------
    order = roles["order"]
    item = roles["item"]
    product = roles["product"]
    customer = roles["customer"]

    def write_view(view_name: str, statement: str, description: str) -> None:
        view_dir = views_dir / view_name
        view_dir.mkdir(parents=True, exist_ok=True)
        (view_dir / "metadata.yml").write_text(
            _safe_yaml({
                "name": view_name,
                "statement": statement.strip() + "\n",
                "properties": {"description": description},
            }),
            encoding="utf-8",
        )

    if order and customer:
        om = name_map[order.name]
        cm = name_map[customer.name]
        o_id = _find(order, "id", "order_id")
        o_cust = _find(order, "customer_id", "user_id", "member_id")
        c_id = _find(customer, "id", "customer_id")
        if o_id and o_cust and c_id:
            selects = [
                f"o.{o_id.name} AS order_id",
                f"o.{o_cust.name} AS customer_id",
            ]
            for col in order.columns:
                if col.name in {o_id.name, o_cust.name}:
                    continue
                selects.append(f"o.{col.name}")
            for alias, candidates in [
                ("customer_name", ("name", "customer_name")),
                ("customer_level", ("level", "member_level")),
                ("customer_city", ("city",)),
                ("customer_gender", ("gender",)),
            ]:
                col = _find(customer, *candidates)
                if col:
                    selects.append(f"c.{col.name} AS {alias}")
            statement = (
                "SELECT " + ", ".join(selects) + "\n"
                f"FROM {om} o\n"
                f"LEFT JOIN {cm} c ON o.{o_cust.name} = c.{c_id.name}"
            )
            write_view(
                "order_wide", statement,
                "订单宽表：一行一个订单，已关联客户基础信息。适合订单级指标与客户维度分析。",
            )

    if order and item and product:
        om, im, pm = name_map[order.name], name_map[item.name], name_map[product.name]
        o_id = _find(order, "id", "order_id")
        i_order = _find(item, "order_id")
        i_product = _find(item, "product_id")
        p_id = _find(product, "id", "product_id")
        qty = _find(item, "quantity", "qty")
        price = _find(item, "unit_price", "price")
        discount = _find(item, "discount", "discount_amount")
        if o_id and i_order and i_product and p_id and qty and price:
            selects = [
                f"oi.{_find(item, 'id').name} AS line_id" if _find(item, "id") else "1 AS line_id",
                f"o.{o_id.name} AS order_id",
            ]
            for col in order.columns:
                if col.name == o_id.name:
                    continue
                if col.name.lower() in {"customer_id", "id"}:
                    continue
                selects.append(f"o.{col.name}")
            if customer:
                c_id = _find(customer, "id", "customer_id")
                o_cust = _find(order, "customer_id", "user_id", "member_id")
                if c_id and o_cust:
                    selects.append(f"o.{o_cust.name} AS customer_id")
                    for alias, candidates in [
                        ("customer_name", ("name", "customer_name")),
                        ("customer_level", ("level", "member_level")),
                        ("customer_city", ("city",)),
                    ]:
                        col = _find(customer, *candidates)
                        if col:
                            selects.append(f"c.{col.name} AS {alias}")
            selects.extend([
                f"p.{p_id.name} AS product_id",
            ])
            for alias, candidates in [
                ("product_name", ("name", "product_name")),
                ("category", ("category",)),
                ("sub_category", ("sub_category", "subcategory")),
            ]:
                col = _find(product, *candidates)
                if col:
                    selects.append(f"p.{col.name} AS {alias}")
            selects.extend([
                f"oi.{qty.name} AS quantity",
                f"oi.{price.name} AS unit_price",
            ])
            if discount:
                selects.append(f"oi.{discount.name} AS discount")
            line_amount = f"oi.{qty.name} * oi.{price.name}"
            if discount:
                line_amount += f" - COALESCE(oi.{discount.name}, 0)"
            selects.append(f"({line_amount}) AS line_amount")
            cost = _find(product, "cost", "unit_cost")
            if cost:
                selects.append(f"(oi.{qty.name} * p.{cost.name}) AS line_cost")
            joins = [
                f"FROM {im} oi",
                f"JOIN {om} o ON oi.{i_order.name} = o.{o_id.name}",
                f"JOIN {pm} p ON oi.{i_product.name} = p.{p_id.name}",
            ]
            if customer:
                o_cust = _find(order, "customer_id", "user_id", "member_id")
                c_id = _find(customer, "id", "customer_id")
                if o_cust and c_id:
                    joins.append(f"LEFT JOIN {name_map[customer.name]} c ON o.{o_cust.name} = c.{c_id.name}")
            statement = "SELECT " + ", ".join(selects) + "\n" + "\n".join(joins)
            write_view(
                "sales_line", statement,
                "销售明细宽表：一行一个订单商品行，已关联订单、客户、商品。适合品类/商品维度分析，行金额字段为 line_amount。",
            )

    # ---- knowledge ------------------------------------------------------
    (knowledge_dir / "knowledge.yml").write_text(
        _safe_yaml({"schema_version": 1}), encoding="utf-8"
    )
    rules_text = "# 业务规则\n\n" + "\n".join(f"- {rule}" for rule in ontology.rules) + "\n"
    (knowledge_dir / "rules" / "business-rules.md").write_text(rules_text, encoding="utf-8")
    for index, example in enumerate(ontology.examples, start=1):
        slug = re.sub(r"[^a-zA-Z0-9一-鿿]+", "-", example.question).strip("-")[:40] or f"example-{index}"
        content = (
            "---\n"
            f"nl: {example.question}\n"
            "sql: |\n"
            + "\n".join("  " + line for line in example.sql.splitlines())
            + "\n"
            "source: user\n"
            "tags:\n"
            + "\n".join(f"  - {tag}" for tag in example.tags)
            + "\n---\n"
        )
        (knowledge_dir / "sql" / f"{index:02d}-{slug}.md").write_text(content, encoding="utf-8")

    return project_dir


def build_manifest(project_dir: Path) -> dict[str, Any]:
    from wren.context import build_json, save_target

    manifest = build_json(project_dir)
    save_target(manifest, project_dir)
    return manifest


def manifest_to_base64(manifest: dict[str, Any]) -> str:
    raw = json.dumps(manifest, ensure_ascii=False).encode("utf-8")
    return base64.b64encode(raw).decode("ascii")


def load_manifest(project_dir: Path) -> dict[str, Any]:
    target = project_dir / "target" / "mdl.json"
    if not target.exists():
        return build_manifest(project_dir)
    return json.loads(target.read_text(encoding="utf-8"))
