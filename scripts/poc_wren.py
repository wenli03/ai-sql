"""Proof of concept: build a Wren MDL project and query Postgres/MySQL."""

from __future__ import annotations

import base64
import json
from pathlib import Path

import yaml

from wren.context import build_json, save_target
from wren.engine import WrenEngine
from wren.model.data_source import DataSource

PROJECT = Path(__file__).resolve().parents[1] / "wren-insight" / "projects" / "poc"


def write_project(data_source: str) -> None:
    PROJECT.mkdir(parents=True, exist_ok=True)
    (PROJECT / "models" / "customers").mkdir(parents=True, exist_ok=True)
    (PROJECT / "models" / "orders").mkdir(parents=True, exist_ok=True)
    config = {
        "schema_version": 5,
        "name": f"poc_{data_source}",
        "catalog": "wren",
        "schema": "public" if data_source == "postgres" else "demo",
        "data_source": data_source,
    }
    (PROJECT / "wren_project.yml").write_text(
        yaml.safe_dump(config, sort_keys=False, allow_unicode=True), encoding="utf-8"
    )
    (PROJECT / "models" / "customers" / "metadata.yml").write_text(
        yaml.safe_dump(
            {
                "name": "customers",
                "table_reference": {"catalog": "", "schema": "public" if data_source == "postgres" else "demo", "table": "customers"},
                "columns": [
                    {"name": "id", "type": "INTEGER", "is_primary_key": True},
                    {"name": "name", "type": "VARCHAR"},
                    {"name": "level", "type": "VARCHAR"},
                    {"name": "city", "type": "VARCHAR"},
                ],
                "primary_key": "id",
            },
            sort_keys=False,
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    (PROJECT / "models" / "orders" / "metadata.yml").write_text(
        yaml.safe_dump(
            {
                "name": "orders",
                "table_reference": {"catalog": "", "schema": "public" if data_source == "postgres" else "demo", "table": "orders"},
                "columns": [
                    {"name": "id", "type": "INTEGER", "is_primary_key": True},
                    {"name": "customer_id", "type": "INTEGER"},
                    {"name": "order_date", "type": "TIMESTAMP"},
                    {"name": "status", "type": "VARCHAR"},
                    {"name": "total_amount", "type": "DECIMAL"},
                ],
                "primary_key": "id",
            },
            sort_keys=False,
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    (PROJECT / "relationships.yml").write_text(
        yaml.safe_dump(
            {
                "relationships": [
                    {
                        "name": "orders_customer",
                        "models": ["orders", "customers"],
                        "join_type": "MANY_TO_ONE",
                        "condition": "orders.customer_id = customers.id",
                    }
                ]
            },
            sort_keys=False,
            allow_unicode=True,
        ),
        encoding="utf-8",
    )


def run(data_source: str) -> None:
    write_project(data_source)
    manifest = build_json(PROJECT)
    save_target(manifest, PROJECT)
    raw = json.dumps(manifest, ensure_ascii=False).encode("utf-8")
    manifest_str = base64.b64encode(raw).decode("ascii")

    if data_source == "postgres":
        ds = DataSource.postgres
        info = {"host": "127.0.0.1", "port": 5432, "database": "demo", "user": "demo", "password": "demo123"}
    else:
        ds = DataSource.mysql
        info = {"host": "127.0.0.1", "port": 3306, "database": "demo", "user": "demo", "password": "demo123", "sslMode": "DISABLED"}

    sql = (
        'SELECT status, COUNT(*) AS order_count, ROUND(SUM(total_amount), 2) AS revenue '
        'FROM orders GROUP BY status ORDER BY revenue DESC'
    )
    with WrenEngine(manifest_str, ds, info) as engine:
        planned = engine.dry_plan(sql)
        print(f"--- {data_source} planned SQL ---")
        print(planned)
        table = engine.query(sql, limit=50)
        print(table.to_pandas().to_string(index=False))


if __name__ == "__main__":
    run("postgres")
    run("mysql")
