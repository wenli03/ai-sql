from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

APP_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = APP_ROOT.parent
DATA_DIR = APP_ROOT / "data"
PROJECTS_DIR = APP_ROOT / "projects"
REPORTS_DIR = DATA_DIR / "reports"
WEB_DIR = APP_ROOT / "web"
VENDOR_DIR = WEB_DIR / "vendor"
LAKE_DIR = REPO_ROOT / "infra" / "lake"

# Load local overrides first, then the repo-level .env (without overriding).
load_dotenv(APP_ROOT / ".env")
load_dotenv(REPO_ROOT / ".env")

for directory in (DATA_DIR, PROJECTS_DIR, REPORTS_DIR, VENDOR_DIR):
    directory.mkdir(parents=True, exist_ok=True)

CONNECTIONS_FILE = DATA_DIR / "connections.json"
APP_SETTINGS_FILE = DATA_DIR / "settings.json"

LLM_BASE_URL = os.getenv("LLM_BASE_URL", "https://api.deepseek.com").rstrip("/")
LLM_API_KEY = os.getenv("LLM_API_KEY") or os.getenv("OPENAI_API_KEY") or ""
LLM_MODEL = os.getenv("LLM_MODEL", "deepseek-chat")
LLM_TEMPERATURE = float(os.getenv("LLM_TEMPERATURE", "0.1"))
LLM_TIMEOUT_SECONDS = float(os.getenv("LLM_TIMEOUT_SECONDS", "120"))

MAX_ROWS = int(os.getenv("WREN_INSIGHT_MAX_ROWS", "500"))
MAX_REPAIR_ATTEMPTS = int(os.getenv("WREN_INSIGHT_REPAIR_ATTEMPTS", "2"))
QUERY_TIMEOUT_SECONDS = int(os.getenv("WREN_INSIGHT_QUERY_TIMEOUT", "60"))

# Vector databases: text -> embedding -> similarity search.
EMBEDDING_BASE_URL = os.getenv("EMBEDDING_BASE_URL", "http://127.0.0.1:11434/v1").rstrip("/")
EMBEDDING_API_KEY = os.getenv("EMBEDDING_API_KEY", "")
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "nomic-embed-text")
VECTOR_MAX_SCAN = int(os.getenv("WREN_INSIGHT_VECTOR_MAX_SCAN", "5000"))
VECTOR_MAX_TOP_K = int(os.getenv("WREN_INSIGHT_VECTOR_MAX_TOP_K", "50"))

APP_TITLE = "数语"
APP_SUBTITLE = "本体增强的中文自然语言数据分析"

DEMO_CONNECTIONS = [
    {
        "id": "demo-postgres",
        "name": "演示库 · PostgreSQL",
        "dialect": "postgres",
        "host": "127.0.0.1",
        "port": 5432,
        "database": "demo",
        "user": "demo",
        "password": "demo123",
        "schema_name": "public",
    },
    {
        "id": "demo-mysql",
        "name": "演示库 · MySQL",
        "dialect": "mysql",
        "host": "127.0.0.1",
        "port": 3306,
        "database": "demo",
        "user": "demo",
        "password": "demo123",
        "schema_name": "demo",
    },
    {
        "id": "demo-duckdb",
        "name": "演示湖仓 · DuckDB + Parquet",
        "dialect": "duckdb",
        "host": "",
        "port": 0,
        "database": str(LAKE_DIR),
        "user": "",
        "password": "",
        "schema_name": "main",
        "options": {"format": "duckdb", "catalog": "lakehouse"},
    },
    {
        "id": "demo-chroma",
        "name": "演示向量库 · Chroma",
        "dialect": "chroma",
        "host": "http://127.0.0.1:8001",
        "port": 8001,
        "database": "",
        "user": "",
        "password": "",
        "schema_name": "",
        "options": {
            "tenant": "default_tenant",
            "database": "default_database",
            "collection": "knowledge_docs",
        },
    },
    {
        "id": "demo-qdrant",
        "name": "演示向量库 · Qdrant",
        "dialect": "qdrant",
        "host": "http://127.0.0.1:6333",
        "port": 6333,
        "database": "",
        "user": "",
        "password": "",
        "schema_name": "",
        "options": {"collection": "knowledge_docs"},
    },
]
