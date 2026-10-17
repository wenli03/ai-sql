from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel

from . import config, store
from .db.introspect import scan_schema, test_connection
from .models import (
    AskRequest,
    AskResponse,
    ChartSpec,
    ChartType,
    ConnectionConfig,
    ConnectionInput,
    Report,
    ReportCreate,
    SENSITIVE_OPTION_HINTS,
)
from .nl2sql.chart import recommend_chart
from .nl2sql.llm import LLMClient, LLMError
from .nl2sql.pipeline import NL2SQLError, ask, suggestions
from .provenance import probe_freshness
from .report import block_from_answer, export_html
from .semantic import store as semantic_store
from .sources import SOURCE_TYPES

router = APIRouter(prefix="/api")


def _to_config(payload: ConnectionInput) -> ConnectionConfig:
    if payload.id:
        try:
            existing = store.get_connection(payload.id)
        except KeyError:
            existing = None
    else:
        existing = None
    port = payload.port or (existing.port if existing else store.default_port(payload.dialect))
    schema_name = (
        payload.schema_name
        or (existing.schema_name if existing else "")
        or store.default_schema(payload.dialect, payload.database)
    )
    if payload.dialect == "oracle" and not schema_name:
        schema_name = (payload.user or "").upper()
    options = dict(payload.options or {})
    if existing:
        for key, value in list(options.items()):
            if value == "******":
                options[key] = existing.options.get(key, "")
        if existing.dialect == payload.dialect:
            for key, value in existing.options.items():
                if key in options or not value:
                    continue
                if any(hint in key.lower() for hint in SENSITIVE_OPTION_HINTS):
                    options[key] = value
    return ConnectionConfig(
        id=payload.id or "test",
        name=payload.name or "test",
        dialect=payload.dialect,
        host=(payload.host or "").strip(),
        port=port,
        database=payload.database,
        user=payload.user,
        password=payload.password or (existing.password if existing else ""),
        schema_name=schema_name,
        ssl_mode=payload.ssl_mode or (existing.ssl_mode if existing else None),
        options=options,
    )


def _validate_connection(payload: ConnectionInput) -> str | None:
    if not payload.name.strip():
        return "连接名称不能为空"
    dialect = payload.dialect
    options = payload.options or {}
    if dialect in ("postgres", "mysql"):
        if not payload.database.strip() or not payload.user.strip():
            return "数据库和用户名不能为空"
    elif dialect == "oracle":
        if not payload.host.strip() or not payload.user.strip() or not payload.password:
            return "Oracle 需要 Host、用户名和密码"
        if not payload.database.strip() and not options.get("dsn"):
            return "Oracle 需要 Service Name 或完整 DSN"
    elif dialect == "mssql":
        if not payload.host.strip() or not payload.database.strip():
            return "SQL Server 需要 Host 和数据库"
        if not payload.user.strip() or not payload.password:
            return "SQL Server 需要用户名和密码（或使用 Windows 集成认证的独立配置）"
    elif dialect in ("qdrant", "chroma", "weaviate"):
        if not payload.host.strip():
            return "向量数据库需要 URL"
    elif dialect == "trino":
        if not payload.host.strip() or not (options.get("catalog") or payload.database).strip():
            return "Trino 需要 Host 和 Catalog"
    elif dialect == "databricks":
        if not payload.host.strip() or not options.get("http_path", "").strip():
            return "Databricks 需要 Host 和 HTTP Path"
        if not payload.password and not options.get("access_token"):
            return "Databricks 需要 Access Token"
    elif dialect == "athena":
        if not options.get("s3_staging_dir", "").strip():
            return "Athena 需要 S3 Staging Directory"
        if not payload.schema_name:
            return "Athena 需要数据库（Schema）"
    elif dialect == "duckdb":
        if not payload.database.strip():
            return "DuckDB 需要数据库目录或文件路径"
    elif dialect == "clickhouse":
        if not payload.host.strip() or not payload.database.strip():
            return "ClickHouse 需要 Host 和 Database"
    elif dialect == "bigquery":
        if not (options.get("project_id") or payload.database).strip() or not payload.schema_name:
            return "BigQuery 需要 Project 和 Dataset"
    elif dialect == "spark":
        if not payload.host.strip():
            return "Spark 需要 Host"
    return None


@router.get("/health")
def health() -> dict[str, Any]:
    client = LLMClient()
    primary = client.providers[0] if client.providers else None
    return {
        "ok": True,
        "app": config.APP_TITLE,
        "llm": {
            "configured": client.configured,
            "provider": primary.name if primary else "未配置",
            "model": primary.model if primary else "",
            "base_url": primary.base_url if primary else "",
            "fallbacks": [provider.name for provider in client.providers[1:]],
        },
        "connections": len(store.list_connections()),
    }


@router.get("/connections")
def list_connections() -> list[dict[str, Any]]:
    return [conn.public_dict() for conn in store.list_connections()]


@router.get("/source-types")
def source_types() -> list[dict[str, Any]]:
    return SOURCE_TYPES


@router.post("/connections/test")
def test_connection_endpoint(payload: ConnectionInput) -> dict[str, Any]:
    conn = _to_config(payload)
    try:
        return test_connection(conn)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"连接失败: {exc}") from exc


@router.post("/connections")
def save_connection(payload: ConnectionInput) -> dict[str, Any]:
    problem = _validate_connection(payload)
    if problem:
        raise HTTPException(status_code=400, detail=problem)
    conn = store.save_connection(payload)
    return conn.public_dict()


@router.delete("/connections/{connection_id}")
def delete_connection(connection_id: str) -> dict[str, Any]:
    store.delete_connection(connection_id)
    return {"ok": True}


@router.post("/connections/{connection_id}/scan")
def scan(connection_id: str) -> dict[str, Any]:
    conn = store.get_connection(connection_id)
    try:
        summary = semantic_store.build(conn)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"扫描或建模失败: {exc}") from exc
    project = semantic_store.project_summary(connection_id)
    return {"summary": summary, "project": project}


@router.get("/connections/{connection_id}/schema")
def get_schema(connection_id: str) -> dict[str, Any]:
    try:
        return semantic_store.load_schema(connection_id).model_dump(mode="json")
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/connections/{connection_id}/summary")
def get_summary(connection_id: str) -> dict[str, Any]:
    try:
        return semantic_store.project_summary(connection_id)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


class FreshnessRequest(BaseModel):
    models: list[str] = []


@router.post("/connections/{connection_id}/freshness")
def refresh_freshness(connection_id: str, payload: FreshnessRequest) -> dict[str, Any]:
    try:
        conn = store.get_connection(connection_id)
        scan = semantic_store.load_schema(connection_id)
        models = payload.models or [table.name for table in scan.tables]
        items = probe_freshness(conn, scan, models)
        return {"freshness": [item.model_dump(mode="json") for item in items]}
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"新鲜度探测失败: {exc}") from exc


@router.get("/connections/{connection_id}/ontology")
def get_ontology(connection_id: str) -> dict[str, Any]:
    try:
        return {
            "yaml": semantic_store.ontology_yaml(connection_id),
            "project": semantic_store.project_summary(connection_id),
        }
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


class OntologyUpdate(BaseModel):
    yaml: str


@router.put("/connections/{connection_id}/ontology")
def update_ontology(connection_id: str, payload: OntologyUpdate) -> dict[str, Any]:
    try:
        summary = semantic_store.save_ontology_yaml(connection_id, payload.yaml)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"本体保存失败: {exc}") from exc
    return {"summary": summary, "project": semantic_store.project_summary(connection_id)}


@router.get("/suggestions")
def get_suggestions(connection_id: str) -> dict[str, Any]:
    try:
        return {"suggestions": suggestions(connection_id)}
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


class RechartRequest(BaseModel):
    rows: list[dict[str, Any]]
    columns: list[str]
    chart_type: ChartType
    question: str = ""
    title: str = "查询结果"


@router.post("/rechart", response_model=ChartSpec)
def rechart(payload: RechartRequest) -> ChartSpec:
    try:
        import pandas as pd

        df = pd.DataFrame(payload.rows, columns=payload.columns)
        for column in df.columns:
            if df[column].dtype == object:
                converted = pd.to_numeric(df[column], errors="coerce")
                if len(df) == 0 or converted.notna().sum() >= max(1, int(len(df) * 0.8)):
                    df[column] = converted
        return recommend_chart(
            df, payload.question, {}, override=payload.chart_type, title=payload.title
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"切换图表失败: {exc}") from exc


@router.post("/ask", response_model=AskResponse)
def ask_endpoint(payload: AskRequest) -> AskResponse:
    try:
        conn = store.get_connection(payload.connection_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    try:
        return ask(
            conn,
            payload.question,
            max_rows=payload.max_rows,
            chart_type=payload.chart_type,
            history=payload.history,
        )
    except NL2SQLError as exc:
        raise HTTPException(
            status_code=400,
            detail={"message": str(exc), "sql": exc.sql},
        ) from exc
    except LLMError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"分析失败: {exc}") from exc


@router.get("/reports")
def list_reports() -> list[dict[str, Any]]:
    return store.list_reports()


@router.post("/reports", response_model=Report)
def create_report(payload: ReportCreate) -> Report:
    try:
        conn = store.get_connection(payload.connection_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    title = payload.title or f"{conn.name} 分析报告"
    return store.create_report(title, conn.id, conn.name, conn.dialect)


@router.get("/reports/{report_id}", response_model=Report)
def get_report(report_id: str) -> Report:
    try:
        return store.get_report(report_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.delete("/reports/{report_id}")
def delete_report(report_id: str) -> dict[str, Any]:
    store.delete_report(report_id)
    return {"ok": True}


class AddBlockRequest(BaseModel):
    answer: AskResponse


@router.post("/reports/{report_id}/blocks", response_model=Report)
def add_block(report_id: str, payload: AddBlockRequest) -> Report:
    try:
        return store.add_block(report_id, block_from_answer(payload.answer))
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/reports/{report_id}/export")
def export_report(report_id: str) -> dict[str, Any]:
    try:
        report = store.get_report(report_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    echarts_path = config.VENDOR_DIR / "echarts.min.js"
    if not echarts_path.exists():
        raise HTTPException(status_code=500, detail="缺少 echarts.min.js，请先下载前端依赖")
    html = export_html(report, echarts_path.read_text(encoding="utf-8"))
    out = config.REPORTS_DIR / f"{report_id}.html"
    out.write_text(html, encoding="utf-8")
    return {"url": f"/api/reports/{report_id}/html", "path": str(out)}


@router.get("/reports/{report_id}/html", response_class=HTMLResponse)
def report_html(report_id: str) -> HTMLResponse:
    out = config.REPORTS_DIR / f"{report_id}.html"
    if not out.exists():
        try:
            export_report(report_id)
        except HTTPException:
            raise
    return HTMLResponse(out.read_text(encoding="utf-8"))


@router.get("/reports/{report_id}/download")
def download_report(report_id: str) -> FileResponse:
    out = config.REPORTS_DIR / f"{report_id}.html"
    if not out.exists():
        export_report(report_id)
    return FileResponse(out, media_type="text/html", filename=f"{report_id}.html")
