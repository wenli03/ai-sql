from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

import yaml

from .. import config, store
from ..db.introspect import scan_schema
from ..models import ConnectionConfig, Ontology, ScannedSchema
from ..vector.base import VECTOR_SOURCES
from ..vector.introspect import scan_vector
from .mdl import build_manifest, load_manifest, manifest_to_base64, write_project
from .ontology import build_ontology

_locks: dict[str, threading.RLock] = {}
_locks_guard = threading.Lock()


def _lock_for(connection_id: str) -> threading.RLock:
    with _locks_guard:
        return _locks.setdefault(connection_id, threading.RLock())


def project_dir(connection_id: str) -> Path:
    return config.PROJECTS_DIR / connection_id


def _schema_path(connection_id: str) -> Path:
    return project_dir(connection_id) / "schema.json"


def _ontology_path(connection_id: str) -> Path:
    return project_dir(connection_id) / "ontology.yml"


def _write_artifacts(connection_id: str, scan: ScannedSchema, ontology: Ontology) -> None:
    _schema_path(connection_id).write_text(
        json.dumps(scan.model_dump(mode="json"), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    _ontology_path(connection_id).write_text(
        yaml.safe_dump(
            ontology.model_dump(mode="json"),
            sort_keys=False,
            allow_unicode=True,
            default_flow_style=False,
        ),
        encoding="utf-8",
    )


def build(conn: ConnectionConfig) -> dict[str, Any]:
    with _lock_for(conn.id):
        if conn.dialect in VECTOR_SOURCES:
            scan = scan_vector(conn)
            ontology = build_ontology(scan)
            directory = project_dir(conn.id)
            directory.mkdir(parents=True, exist_ok=True)
            _write_artifacts(conn.id, scan, ontology)
            return {
                "connection_id": conn.id,
                "project_dir": str(directory),
                "tables": len(scan.tables),
                "entities": len(ontology.entities),
                "metrics": len(ontology.metrics),
                "dimensions": len(ontology.dimensions),
                "relationships": 0,
                "model_count": 0,
                "view_count": 0,
            }

        scan = scan_schema(conn)
        ontology = build_ontology(scan)
        directory = project_dir(conn.id)
        write_project(directory, conn, scan, ontology)
        manifest = build_manifest(directory)
        _write_artifacts(conn.id, scan, ontology)
        return {
            "connection_id": conn.id,
            "project_dir": str(directory),
            "tables": len(scan.tables),
            "entities": len(ontology.entities),
            "metrics": len(ontology.metrics),
            "dimensions": len(ontology.dimensions),
            "relationships": len(ontology.relationships),
            "model_count": len(manifest.get("models", [])),
            "view_count": len(manifest.get("views", [])),
        }


def ensure_built(conn: ConnectionConfig) -> None:
    if conn.dialect in VECTOR_SOURCES:
        if not (_schema_path(conn.id).exists() and _ontology_path(conn.id).exists()):
            build(conn)
        return
    if not (project_dir(conn.id) / "target" / "mdl.json").exists():
        build(conn)


def load_schema(connection_id: str) -> ScannedSchema:
    path = _schema_path(connection_id)
    if not path.exists():
        raise FileNotFoundError("尚未生成语义模型，请先扫描数据库")
    return ScannedSchema.model_validate(json.loads(path.read_text(encoding="utf-8")))


def load_ontology(connection_id: str) -> Ontology:
    path = _ontology_path(connection_id)
    if not path.exists():
        raise FileNotFoundError("尚未生成本体，请先扫描数据库")
    return Ontology.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))


def save_ontology(connection_id: str, ontology: Ontology) -> dict[str, Any]:
    with _lock_for(connection_id):
        conn = store.get_connection(connection_id)
        scan = load_schema(connection_id)
        _ontology_path(connection_id).write_text(
            yaml.safe_dump(
                ontology.model_dump(mode="json"),
                sort_keys=False,
                allow_unicode=True,
                default_flow_style=False,
            ),
            encoding="utf-8",
        )
        if conn.dialect in VECTOR_SOURCES:
            return {
                "tables": len(scan.tables),
                "entities": len(ontology.entities),
                "metrics": len(ontology.metrics),
                "dimensions": len(ontology.dimensions),
                "relationships": 0,
                "model_count": 0,
                "view_count": 0,
            }
        directory = project_dir(connection_id)
        write_project(directory, conn, scan, ontology)
        manifest_data = build_manifest(directory)
        return {
            "tables": len(scan.tables),
            "entities": len(ontology.entities),
            "metrics": len(ontology.metrics),
            "dimensions": len(ontology.dimensions),
            "relationships": len(ontology.relationships),
            "model_count": len(manifest_data.get("models", [])),
            "view_count": len(manifest_data.get("views", [])),
        }


def save_ontology_yaml(connection_id: str, text: str) -> dict[str, Any]:
    data = yaml.safe_load(text)
    ontology = Ontology.model_validate(data)
    return save_ontology(connection_id, ontology)


def ontology_yaml(connection_id: str) -> str:
    path = _ontology_path(connection_id)
    if not path.exists():
        raise FileNotFoundError("尚未生成本体，请先扫描数据库")
    return path.read_text(encoding="utf-8")


def manifest(connection_id: str) -> dict[str, Any]:
    conn = store.get_connection(connection_id)
    if conn.dialect in VECTOR_SOURCES:
        raise ValueError("向量数据库不使用 Wren MDL；请使用查询计划执行。")
    directory = project_dir(connection_id)
    if not (directory / "target" / "mdl.json").exists():
        build(conn)
    return load_manifest(directory)


def manifest_b64(connection_id: str) -> str:
    return manifest_to_base64(manifest(connection_id))


def project_summary(connection_id: str) -> dict[str, Any]:
    directory = project_dir(connection_id)
    conn = store.get_connection(connection_id)
    is_vector = conn.dialect in VECTOR_SOURCES
    built = (
        _schema_path(connection_id).exists() and _ontology_path(connection_id).exists()
        if is_vector
        else (directory / "target" / "mdl.json").exists()
    )
    result: dict[str, Any] = {"built": built, "is_vector": is_vector, "source_type": conn.dialect}
    if not built:
        return result
    try:
        ontology = load_ontology(connection_id)
        scanned = load_schema(connection_id)
        result.update({
            "domain": ontology.domain,
            "dialect": ontology.dialect,
            "tables": [t.name for t in scanned.tables],
            "table_rows": {t.name: t.row_count for t in scanned.tables},
            "table_types": {t.name: t.table_type for t in scanned.tables},
            "vector_fields": {
                t.name: {
                    "field": t.vector_field,
                    "dimension": t.vector_dimension,
                    "metric": t.vector_metric,
                }
                for t in scanned.tables
                if t.vector_field
            },
            "entities": [{"name": e.name, "model": e.model, "synonyms": e.synonyms} for e in ontology.entities],
            "metrics": [
                {"name": m.name, "label": m.label, "synonyms": m.synonyms, "unit": m.unit}
                for m in ontology.metrics
            ],
            "dimensions": [
                {"name": d.name, "label": d.label, "synonyms": d.synonyms}
                for d in ontology.dimensions
            ],
            "rules": ontology.rules,
            "generated_at": ontology.generated_at.isoformat(),
        })
    except Exception as exc:  # pragma: no cover - defensive
        result["error"] = str(exc)
    return result
