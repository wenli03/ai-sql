from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

Dialect = Literal[
    "postgres", "mysql", "oracle", "mssql",
    "trino", "databricks", "athena", "duckdb", "clickhouse", "bigquery", "spark",
    "qdrant", "chroma", "milvus", "weaviate", "pinecone", "pgvector",
]
ColumnRole = Literal["key", "measure", "time", "dimension", "other"]
ChartType = Literal["line", "bar", "pie", "scatter", "kpi", "table"]

SENSITIVE_OPTION_HINTS = ("secret", "token", "password", "credential", "access_key", "private_key")


class ConnectionConfig(BaseModel):
    id: str
    name: str
    dialect: Dialect
    host: str = "127.0.0.1"
    port: int = 0
    database: str = ""
    user: str = ""
    password: str = ""
    schema_name: str = ""
    ssl_mode: str | None = None
    options: dict[str, str] = Field(default_factory=dict)

    def public_dict(self) -> dict[str, Any]:
        data = self.model_dump()
        data["password"] = "******" if self.password else ""
        data["has_password"] = bool(self.password)
        masked_options = {}
        for key, value in self.options.items():
            if any(hint in key.lower() for hint in SENSITIVE_OPTION_HINTS):
                masked_options[key] = "******" if value else ""
            else:
                masked_options[key] = value
        data["options"] = masked_options
        return data

    def _option(self, key: str, default: str = "") -> str:
        return str(self.options.get(key, default) or default)

    def wren_connection_info(self) -> dict[str, Any]:
        if self.dialect in ("postgres", "mysql"):
            info: dict[str, Any] = {
                "host": self.host,
                "port": self.port or (5432 if self.dialect == "postgres" else 3306),
                "database": self.database,
                "user": self.user,
                "password": self.password,
            }
            if self.dialect == "mysql":
                info["sslMode"] = self.ssl_mode or "DISABLED"
            if self.options:
                info["kwargs"] = dict(self.options)
            return info

        if self.dialect == "oracle":
            dsn = self._option("dsn")
            if dsn:
                return {
                    "dsn": dsn,
                    "user": self.user,
                    "password": self.password,
                }
            return {
                "host": self.host,
                "port": self.port or 1521,
                "database": self.database or "FREE",
                "user": self.user,
                "password": self.password,
            }

        if self.dialect == "mssql":
            info = {
                "host": self.host,
                "port": self.port or 1433,
                "database": self.database or "master",
                "user": self.user,
                "password": self.password,
                "driver": self._option("driver", "ODBC Driver 18 for SQL Server"),
            }
            kwargs = {
                key: value
                for key, value in self.options.items()
                if key not in ("driver",)
            }
            if kwargs:
                info["kwargs"] = kwargs
            return info

        if self.dialect == "trino":
            info = {
                "host": self.host,
                "port": self.port or 8080,
                "catalog": self._option("catalog", self.database) or "hive",
                "schema": self.schema_name or "default",
                "user": self.user or "wren",
            }
            if self.password:
                info["password"] = self.password
            kwargs = {
                key: self.options[key]
                for key in ("http_scheme", "verify", "access_token")
                if self.options.get(key)
            }
            if kwargs:
                info["kwargs"] = kwargs
            return info

        if self.dialect == "databricks":
            return {
                "serverHostname": self.host,
                "httpPath": self._option("http_path"),
                "catalog": self._option("catalog", self.database) or None,
                "accessToken": self.password or self._option("access_token"),
            }

        if self.dialect == "athena":
            info = {
                "s3_staging_dir": self._option("s3_staging_dir"),
                "schema_name": self.schema_name or "default",
                "region_name": self._option("region") or None,
            }
            mapping = {
                "aws_access_key_id": "aws_access_key_id",
                "aws_secret_access_key": "aws_secret_access_key",
                "aws_session_token": "aws_session_token",
                "role_arn": "role_arn",
                "role_session_name": "role_session_name",
            }
            for key, target in mapping.items():
                value = self._option(key)
                if value:
                    info[target] = value
            return {key: value for key, value in info.items() if value is not None}

        if self.dialect in ("duckdb", "local_file"):
            return {
                "url": self.database,
                "format": self._option("format", "duckdb"),
            }

        if self.dialect == "clickhouse":
            return {
                "host": self.host,
                "port": self.port or 8123,
                "database": self.database or "default",
                "user": self.user or "default",
                "password": self.password,
                "secure": self._option("secure", "false").lower() in ("1", "true", "yes"),
            }

        if self.dialect == "bigquery":
            info = {
                "bigquery_type": "dataset",
                "project_id": self._option("project_id", self.database),
                "dataset_id": self.schema_name,
            }
            credentials = self._option("credentials")
            if credentials:
                info["credentials"] = credentials
            return info

        if self.dialect == "spark":
            return {"host": self.host, "port": self.port or 15002}

        raise ValueError(f"Unsupported data source: {self.dialect}")


class ConnectionInput(BaseModel):
    id: str | None = None
    name: str
    dialect: Dialect
    host: str = "127.0.0.1"
    port: int | None = None
    database: str = ""
    user: str = ""
    password: str = ""
    schema_name: str | None = None
    ssl_mode: str | None = None
    options: dict[str, str] = Field(default_factory=dict)


class ColumnInfo(BaseModel):
    name: str
    data_type: str
    mdl_type: str
    nullable: bool = True
    default: str | None = None
    is_primary_key: bool = False
    is_foreign_key: bool = False
    comment: str | None = None
    ordinal: int = 0
    sample_values: list[str] = Field(default_factory=list)
    role: ColumnRole = "other"


class ForeignKeyInfo(BaseModel):
    name: str
    columns: list[str]
    ref_table: str
    ref_columns: list[str]


class TableInfo(BaseModel):
    name: str
    schema_name: str
    catalog: str | None = None
    comment: str | None = None
    row_count: int = 0
    row_count_estimated: bool = True
    table_type: str = "TABLE"
    location: str | None = None
    vector_field: str | None = None
    vector_dimension: int | None = None
    vector_metric: str | None = None
    columns: list[ColumnInfo] = Field(default_factory=list)
    foreign_keys: list[ForeignKeyInfo] = Field(default_factory=list)
    primary_key: list[str] = Field(default_factory=list)


class ScannedSchema(BaseModel):
    dialect: Dialect
    database: str = ""
    catalog: str | None = None
    schema_name: str = ""
    tables: list[TableInfo]
    scanned_at: datetime = Field(default_factory=datetime.now)


class OntologyAttribute(BaseModel):
    name: str
    column: str
    label: str
    synonyms: list[str] = Field(default_factory=list)
    description: str = ""
    role: ColumnRole = "other"


class OntologyEntity(BaseModel):
    name: str
    model: str
    label: str
    synonyms: list[str] = Field(default_factory=list)
    description: str = ""
    key: str | None = None
    attributes: list[OntologyAttribute] = Field(default_factory=list)


class OntologyRelationship(BaseModel):
    name: str
    from_model: str
    from_column: str
    to_model: str
    to_column: str
    join_type: str = "MANY_TO_ONE"
    description: str = ""


class OntologyMetric(BaseModel):
    name: str
    label: str
    synonyms: list[str] = Field(default_factory=list)
    expression: str
    description: str = ""
    filters: str | None = None
    related_models: list[str] = Field(default_factory=list)
    unit: str = ""


class OntologyDimension(BaseModel):
    name: str
    label: str
    synonyms: list[str] = Field(default_factory=list)
    expression: str
    model: str
    description: str = ""


class OntologyExample(BaseModel):
    question: str
    sql: str
    tags: list[str] = Field(default_factory=list)


class Ontology(BaseModel):
    version: int = 1
    domain: str = "通用业务数据"
    dialect: Dialect = "postgres"
    generated_at: datetime = Field(default_factory=datetime.now)
    entities: list[OntologyEntity] = Field(default_factory=list)
    relationships: list[OntologyRelationship] = Field(default_factory=list)
    metrics: list[OntologyMetric] = Field(default_factory=list)
    dimensions: list[OntologyDimension] = Field(default_factory=list)
    rules: list[str] = Field(default_factory=list)
    examples: list[OntologyExample] = Field(default_factory=list)
    glossary: dict[str, str] = Field(default_factory=dict)


class ChartSpec(BaseModel):
    type: ChartType
    title: str
    x: str | None = None
    y: list[str] = Field(default_factory=list)
    series: str | None = None
    option: dict[str, Any] | None = None
    reason: str | None = None


class SourceTableRef(BaseModel):
    model: str
    kind: Literal["model", "view", "collection"] = "model"
    catalog: str | None = None
    schema_name: str | None = None
    table: str
    source_type: str
    location: str | None = None


class LineageEdge(BaseModel):
    output_column: str
    source_catalog: str | None = None
    source_schema: str | None = None
    source_table: str
    source_column: str
    expression: str | None = None


class ReliabilityCheck(BaseModel):
    name: str
    label: str
    status: Literal["pass", "warn", "fail"]
    detail: str = ""


class FreshnessInfo(BaseModel):
    model: str
    table: str
    row_count: int | None = None
    max_time_column: str | None = None
    max_time_value: str | None = None
    checked_at: datetime | None = None
    note: str = ""


class Provenance(BaseModel):
    generated_at: datetime = Field(default_factory=datetime.now)
    connection_id: str
    connection_name: str
    source_type: str
    database: str = ""
    catalog: str | None = None
    schema_name: str = ""
    ontology_generated_at: datetime | None = None
    metrics_used: list[dict[str, Any]] = Field(default_factory=list)
    rules_used: list[str] = Field(default_factory=list)
    source_tables: list[SourceTableRef] = Field(default_factory=list)
    views_used: list[str] = Field(default_factory=list)
    columns_used: list[str] = Field(default_factory=list)
    lineage: list[LineageEdge] = Field(default_factory=list)
    checks: list[ReliabilityCheck] = Field(default_factory=list)
    reliability_score: int = 0
    freshness: list[FreshnessInfo] = Field(default_factory=list)
    sql: str = ""
    planned_sql: str | None = None
    sql_dialect: str = ""
    row_count: int = 0
    elapsed_ms: int = 0
    llm_provider: str = ""
    llm_model: str = ""
    attempts: int = 1


class AskRequest(BaseModel):
    connection_id: str
    question: str
    max_rows: int | None = None
    chart_type: ChartType | None = None
    history: list[dict[str, str]] = Field(default_factory=list)


class TraceSpan(BaseModel):
    """One instrumented step of an ask. Field names mirror `core.trace.Span`."""

    name: str
    ms: float = 0.0
    status: str = "ok"
    meta: dict[str, Any] = Field(default_factory=dict)


class AskResponse(BaseModel):
    question: str
    title: str
    sql: str
    planned_sql: str | None = None
    explanation: str = ""
    assumptions: list[str] = Field(default_factory=list)
    chart: ChartSpec
    columns: list[str] = Field(default_factory=list)
    rows: list[dict[str, Any]] = Field(default_factory=list)
    row_count: int = 0
    elapsed_ms: int = 0
    attempts: int = 1
    warnings: list[str] = Field(default_factory=list)
    provider: str = ""
    model: str = ""
    token_usage: dict[str, Any] = Field(default_factory=dict)
    provenance: Provenance | None = None
    # Instrumentation. `elapsed_ms` is the end-to-end wall time of `ask()`;
    # `trace` breaks it down so the benchmark can attribute regressions.
    trace: list[TraceSpan] = Field(default_factory=list)
    trace_aggregate: dict[str, dict[str, float]] = Field(default_factory=dict)
    context_ms: float = 0.0


class ReportBlock(BaseModel):
    id: str
    question: str
    title: str
    sql: str
    explanation: str = ""
    chart: ChartSpec
    columns: list[str] = Field(default_factory=list)
    rows: list[dict[str, Any]] = Field(default_factory=list)
    provenance: Provenance | None = None
    created_at: datetime = Field(default_factory=datetime.now)


class Report(BaseModel):
    id: str
    title: str
    connection_id: str
    connection_name: str = ""
    dialect: Dialect = "postgres"
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)
    blocks: list[ReportBlock] = Field(default_factory=list)


class ReportCreate(BaseModel):
    title: str | None = None
    connection_id: str
