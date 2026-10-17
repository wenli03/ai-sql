from __future__ import annotations

import json
import re
import threading
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from . import config
from .models import (
    SENSITIVE_OPTION_HINTS,
    ConnectionConfig,
    ConnectionInput,
    Report,
    ReportBlock,
)

_lock = threading.RLock()


def _read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return default


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    tmp.replace(path)


def slugify(text: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9一-鿿]+", "-", text.strip().lower()).strip("-")
    slug = re.sub(r"-+", "-", slug)
    return slug[:48] or "connection"


# ---------------------------------------------------------------- connections


def ensure_demo_connections() -> None:
    with _lock:
        data = _read_json(config.CONNECTIONS_FILE, None)
        if data is None:
            data = []
        existing = {item.get("id") for item in data if isinstance(item, dict)}
        changed = False
        for demo in config.DEMO_CONNECTIONS:
            if demo["id"] not in existing:
                data.append(demo)
                changed = True
        if changed or not config.CONNECTIONS_FILE.exists():
            _write_json(config.CONNECTIONS_FILE, data)


def list_connections() -> list[ConnectionConfig]:
    ensure_demo_connections()
    raw = _read_json(config.CONNECTIONS_FILE, [])
    result = []
    for item in raw:
        try:
            result.append(ConnectionConfig.model_validate(item))
        except Exception:
            continue
    return result


def get_connection(connection_id: str) -> ConnectionConfig:
    for conn in list_connections():
        if conn.id == connection_id:
            return conn
    raise KeyError(f"连接不存在: {connection_id}")


def default_port(dialect: str) -> int:
    return {
        "postgres": 5432, "mysql": 3306, "oracle": 1521, "mssql": 1433,
        "trino": 8080, "clickhouse": 8123, "spark": 15002, "databricks": 443,
    }.get(dialect, 0)


def default_schema(dialect: str, database: str) -> str:
    if dialect == "postgres":
        return "public"
    if dialect in ("mysql", "clickhouse"):
        return database or "default"
    if dialect == "mssql":
        return "dbo"
    if dialect in ("trino", "databricks", "athena", "spark"):
        return "default"
    if dialect == "duckdb":
        return "main"
    if dialect == "bigquery":
        return database or "default"
    return ""


def save_connection(payload: ConnectionInput) -> ConnectionConfig:
    with _lock:
        conns = list_connections()
        existing = next((c for c in conns if c.id == payload.id), None)
        port = payload.port or (existing.port if existing else default_port(payload.dialect))
        schema_name = (payload.schema_name or (existing.schema_name if existing else "") or
                       default_schema(payload.dialect, payload.database))
        if payload.dialect == "oracle" and not schema_name:
            schema_name = (payload.user or (existing.user if existing else "")).upper()
        if existing and not payload.password:
            password = existing.password
        else:
            password = payload.password
        options = dict(payload.options or {})
        if existing:
            for key, value in list(options.items()):
                if value == "******":
                    options[key] = existing.options.get(key, "")
            # Preserve sensitive options the UI left blank, but only when the
            # data source type did not change (otherwise stale cloud secrets
            # would leak into an unrelated connector).
            if existing.dialect == payload.dialect:
                for key, value in existing.options.items():
                    if not value or key in options:
                        continue
                    if any(hint in key.lower() for hint in SENSITIVE_OPTION_HINTS):
                        options[key] = value
        connection_id = payload.id or f"{slugify(payload.name)}-{uuid.uuid4().hex[:6]}"
        conn = ConnectionConfig(
            id=connection_id,
            name=payload.name.strip(),
            dialect=payload.dialect,
            host=(payload.host or "").strip(),
            port=port,
            database=payload.database.strip(),
            user=(payload.user or "").strip(),
            password=password,
            schema_name=schema_name,
            ssl_mode=payload.ssl_mode,
            options=options,
        )
        replaced = False
        for i, item in enumerate(conns):
            if item.id == connection_id:
                conns[i] = conn
                replaced = True
                break
        if not replaced:
            conns.append(conn)
        _write_json(config.CONNECTIONS_FILE, [c.model_dump() for c in conns])
        return conn


def delete_connection(connection_id: str) -> None:
    with _lock:
        conns = [c for c in list_connections() if c.id != connection_id]
        _write_json(config.CONNECTIONS_FILE, [c.model_dump() for c in conns])


# -------------------------------------------------------------------- reports


def _report_path(report_id: str) -> Path:
    safe = re.sub(r"[^a-zA-Z0-9_-]", "", report_id)
    return config.REPORTS_DIR / f"{safe}.json"


def list_reports() -> list[dict[str, Any]]:
    items = []
    for path in sorted(config.REPORTS_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        data = _read_json(path, None)
        if not data:
            continue
        items.append({
            "id": data.get("id"),
            "title": data.get("title"),
            "connection_id": data.get("connection_id"),
            "connection_name": data.get("connection_name"),
            "dialect": data.get("dialect"),
            "created_at": data.get("created_at"),
            "updated_at": data.get("updated_at"),
            "block_count": len(data.get("blocks", [])),
        })
    return items


def get_report(report_id: str) -> Report:
    data = _read_json(_report_path(report_id), None)
    if not data:
        raise KeyError(f"报告不存在: {report_id}")
    return Report.model_validate(data)


def save_report(report: Report) -> Report:
    report.updated_at = datetime.now()
    _write_json(_report_path(report.id), report.model_dump(mode="json"))
    return report


def create_report(title: str, connection_id: str, connection_name: str, dialect: str) -> Report:
    report = Report(
        id=f"rep-{datetime.now().strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex[:5]}",
        title=title,
        connection_id=connection_id,
        connection_name=connection_name,
        dialect=dialect,  # type: ignore[arg-type]
    )
    return save_report(report)


def add_block(report_id: str, block: ReportBlock) -> Report:
    report = get_report(report_id)
    report.blocks.append(block)
    return save_report(report)


def delete_report(report_id: str) -> None:
    path = _report_path(report_id)
    if path.exists():
        path.unlink()
