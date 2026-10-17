from __future__ import annotations

from typing import Any


def _field(
    key: str,
    label: str,
    kind: str = "text",
    *,
    required: bool = False,
    placeholder: str = "",
    help: str = "",
    default: str = "",
    choices: list[dict[str, str]] | None = None,
    secret: bool = False,
    full: bool = False,
) -> dict[str, Any]:
    return {
        "key": key,
        "label": label,
        "type": kind,
        "required": required,
        "placeholder": placeholder,
        "help": help,
        "default": default,
        "choices": choices or [],
        "secret": secret,
        "full": full,
    }


COMMON_NAME = _field("name", "连接名称", required=True, placeholder="例如：生产湖仓 · Trino", full=True)

SOURCE_TYPES: list[dict[str, Any]] = [
    {
        "id": "postgres",
        "label": "PostgreSQL",
        "category": "关系型数据库",
        "description": "业务库 / OLTP / 数据集市",
        "badge": "PG",
        "fields": [
            COMMON_NAME,
            _field("host", "Host", required=True, default="127.0.0.1"),
            _field("port", "端口", "number", required=True, default="5432"),
            _field("database", "数据库", required=True, placeholder="demo"),
            _field("user", "用户名", required=True, placeholder="demo"),
            _field("password", "密码", "password", secret=True),
            _field("schema_name", "Schema", default="public"),
        ],
    },
    {
        "id": "mysql",
        "label": "MySQL",
        "category": "关系型数据库",
        "description": "业务库 / OLTP / 报表库",
        "badge": "MY",
        "fields": [
            COMMON_NAME,
            _field("host", "Host", required=True, default="127.0.0.1"),
            _field("port", "端口", "number", required=True, default="3306"),
            _field("database", "数据库", required=True, placeholder="demo"),
            _field("user", "用户名", required=True, placeholder="demo"),
            _field("password", "密码", "password", secret=True),
            _field("schema_name", "Schema", default="demo"),
        ],
    },
    {
        "id": "oracle",
        "label": "Oracle Database",
        "category": "关系型数据库",
        "description": "Oracle 11g+ / 19c / 21c / 23ai，支持 Service Name 或完整 DSN",
        "badge": "OR",
        "fields": [
            COMMON_NAME,
            _field("host", "Host", required=True, default="127.0.0.1"),
            _field("port", "端口", "number", required=True, default="1521"),
            _field("database", "Service Name", required=True, placeholder="FREEPDB1 / ORCLPDB1"),
            _field("user", "用户名", required=True, placeholder="SYSTEM"),
            _field("password", "密码", "password", secret=True),
            _field("schema_name", "Schema / Owner", placeholder="默认使用用户名大写"),
            _field("options.dsn", "完整 DSN（可选）", full=True, placeholder="host:port/service_name", help="填写后优先使用 DSN，忽略 Host/Port/Service Name"),
        ],
    },
    {
        "id": "mssql",
        "label": "SQL Server",
        "category": "关系型数据库",
        "description": "SQL Server 2016+ / Azure SQL，通过 ODBC 连接",
        "badge": "MS",
        "fields": [
            COMMON_NAME,
            _field("host", "Host", required=True, default="127.0.0.1"),
            _field("port", "端口", "number", required=True, default="1433"),
            _field("database", "数据库", required=True, placeholder="demo"),
            _field("user", "用户名", required=True, placeholder="sa"),
            _field("password", "密码", "password", secret=True),
            _field("schema_name", "Schema", default="dbo"),
            _field("options.driver", "ODBC Driver", default="ODBC Driver 18 for SQL Server", help="本机没有 18 时可改为 SQL Server 或 ODBC Driver 17 for SQL Server"),
            _field("options.Encrypt", "Encrypt", "select", default="yes", choices=[{"value": "yes", "label": "是"}, {"value": "no", "label": "否"}]),
            _field("options.TrustServerCertificate", "Trust Server Certificate", "select", default="yes", choices=[{"value": "yes", "label": "是"}, {"value": "no", "label": "否"}]),
        ],
    },
    {
        "id": "qdrant",
        "label": "Qdrant",
        "category": "向量数据库",
        "description": "高性能向量检索；支持 metadata 过滤、聚合与相似度搜索",
        "badge": "QD",
        "fields": [
            COMMON_NAME,
            _field("host", "Qdrant URL", required=True, default="http://127.0.0.1:6333", placeholder="http://127.0.0.1:6333", full=True),
            _field("password", "API Key", "password", secret=True, help="本地无鉴权可留空"),
            _field("options.collection", "Collection（可选）", placeholder="knowledge_docs", help="留空则扫描全部 collection"),
        ],
    },
    {
        "id": "chroma",
        "label": "Chroma",
        "category": "向量数据库",
        "description": "轻量级向量库；支持 metadata 过滤、统计与相似度搜索",
        "badge": "CH",
        "fields": [
            COMMON_NAME,
            _field("host", "Chroma URL", required=True, default="http://127.0.0.1:8001", placeholder="http://127.0.0.1:8001", full=True),
            _field("password", "API Token", "password", secret=True, help="本地无鉴权可留空"),
            _field("options.tenant", "Tenant", default="default_tenant"),
            _field("options.database", "Database", default="default_database"),
            _field("options.collection", "Collection（可选）", placeholder="knowledge_docs"),
        ],
    },
    {
        "id": "weaviate",
        "label": "Weaviate",
        "category": "向量数据库",
        "description": "支持 nearVector 相似度检索与 GraphQL metadata 聚合",
        "badge": "WV",
        "fields": [
            COMMON_NAME,
            _field("host", "Weaviate URL", required=True, default="http://127.0.0.1:8081", placeholder="http://127.0.0.1:8081", full=True),
            _field("password", "API Key", "password", secret=True, help="匿名访问可留空"),
            _field("options.collection", "Class（可选）", placeholder="KnowledgeDoc", help="留空则扫描全部 class"),
        ],
    },
    {
        "id": "trino",
        "label": "Trino / Presto",
        "category": "数据湖查询引擎",
        "description": "Iceberg / Delta Lake / Hudi / Hive，通过 catalog 访问",
        "badge": "TR",
        "fields": [
            COMMON_NAME,
            _field("host", "Coordinator Host", required=True, default="127.0.0.1"),
            _field("port", "端口", "number", required=True, default="8080"),
            _field("options.catalog", "Catalog", required=True, placeholder="iceberg / delta / hive", help="Trino 中配置的 lakehouse catalog 名称"),
            _field("schema_name", "Schema", default="default"),
            _field("user", "用户名", default="wren"),
            _field("password", "密码", "password", secret=True, help="Basic Auth 或 JWT 场景使用"),
            _field("options.http_scheme", "协议", "select", default="http", choices=[{"value": "http", "label": "HTTP"}, {"value": "https", "label": "HTTPS"}]),
        ],
    },
    {
        "id": "databricks",
        "label": "Databricks SQL",
        "category": "Lakehouse / Delta Lake",
        "description": "Unity Catalog + Delta Lake，通过 SQL Warehouse 查询",
        "badge": "DB",
        "fields": [
            COMMON_NAME,
            _field("host", "Server Hostname", required=True, placeholder="dbc-xxx.cloud.databricks.com"),
            _field("options.http_path", "HTTP Path", required=True, placeholder="/sql/1.0/warehouses/xxx"),
            _field("options.catalog", "Catalog", default="main"),
            _field("schema_name", "Schema", default="default"),
            _field("password", "Access Token", "password", required=True, secret=True, help="dapi...，编辑时留空保持不变"),
        ],
    },
    {
        "id": "athena",
        "label": "Amazon Athena",
        "category": "数据湖 / S3 + Glue",
        "description": "S3 上的 Parquet / ORC / JSON / Iceberg 表",
        "badge": "AT",
        "fields": [
            COMMON_NAME,
            _field("options.s3_staging_dir", "S3 Staging Directory", required=True, placeholder="s3://bucket/athena-staging/", full=True),
            _field("schema_name", "Database", required=True, placeholder="default"),
            _field("options.region", "Region", placeholder="us-east-1"),
            _field("options.aws_access_key_id", "AWS Access Key ID", secret=True, help="留空则使用环境变量 / IAM Role"),
            _field("options.aws_secret_access_key", "AWS Secret Access Key", "password", secret=True),
            _field("options.aws_session_token", "AWS Session Token", "password", secret=True),
            _field("options.role_arn", "Role ARN", full=True, help="可选：AssumeRole"),
        ],
    },
    {
        "id": "duckdb",
        "label": "DuckDB + Parquet",
        "category": "本地数据湖 / Lakehouse",
        "description": "目录下的 .duckdb 文件；可在库内用视图读取 Parquet/CSV",
        "badge": "DU",
        "fields": [
            COMMON_NAME,
            _field("database", "DuckDB 目录", required=True, placeholder="C:\\study\\ai-sql\\infra\\lake", help="Wren 会 attach 目录下所有 .duckdb 文件", full=True),
            _field("schema_name", "Schema", default="main"),
            _field("options.catalog", "Attach 别名", placeholder="lakehouse", help="可选：只分析指定 .duckdb 文件的别名"),
            _field("options.format", "格式", "select", default="duckdb", choices=[{"value": "duckdb", "label": "DuckDB 文件"}]),
        ],
    },
    {
        "id": "clickhouse",
        "label": "ClickHouse",
        "category": "分析型数据库 / 湖仓加速层",
        "description": "实时分析、宽表、物化视图",
        "badge": "CH",
        "fields": [
            COMMON_NAME,
            _field("host", "Host", required=True, default="127.0.0.1"),
            _field("port", "HTTP 端口", "number", required=True, default="8123"),
            _field("database", "数据库", required=True, default="default"),
            _field("user", "用户名", default="default"),
            _field("password", "密码", "password", secret=True),
            _field("options.secure", "HTTPS", "select", default="false", choices=[{"value": "false", "label": "否"}, {"value": "true", "label": "是"}]),
        ],
    },
    {
        "id": "bigquery",
        "label": "BigQuery",
        "category": "云数据仓库",
        "description": "Project + Dataset，使用服务账号或 ADC",
        "badge": "BQ",
        "fields": [
            COMMON_NAME,
            _field("options.project_id", "Project ID", required=True, placeholder="my-project"),
            _field("schema_name", "Dataset", required=True, placeholder="analytics"),
            _field("options.credentials", "Service Account JSON (Base64)", "textarea", secret=True, full=True, help="留空则使用 Application Default Credentials"),
        ],
    },
    {
        "id": "spark",
        "label": "Spark Thrift Server",
        "category": "数据湖计算引擎",
        "description": "Iceberg / Delta / Hudi，通过 Spark Thrift Server 查询",
        "badge": "SP",
        "fields": [
            COMMON_NAME,
            _field("host", "Host", required=True, default="127.0.0.1"),
            _field("port", "端口", "number", required=True, default="15002"),
            _field("schema_name", "Database / Schema", default="default"),
        ],
    },
]


def source_labels() -> dict[str, str]:
    return {item["id"]: item["label"] for item in SOURCE_TYPES}


def source_definition(source_id: str) -> dict[str, Any] | None:
    for item in SOURCE_TYPES:
        if item["id"] == source_id:
            return item
    return None
