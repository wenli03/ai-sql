"""Common vector-store adapter contract.

The adapters deliberately return plain records (dicts) rather than vendor
objects, so the planner, chart recommender and provenance layer never depend on
a specific vector database SDK.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

VECTOR_SOURCES = {"qdrant", "chroma", "weaviate", "milvus", "pinecone", "pgvector"}

#: Common filter operators shared by every adapter.
FILTER_OPS = {"eq", "in", "gte", "lte", "contains"}


class VectorError(RuntimeError):
    pass


@dataclass
class VectorCollection:
    name: str
    location: str
    vector_field: str | None = None
    dimension: int | None = None
    metric: str = "cosine"
    count: int = 0
    metadata_fields: dict[str, str] = field(default_factory=dict)
    sample: list[dict[str, Any]] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)


class VectorAdapter:
    source_type = ""

    def test(self) -> dict[str, Any]:
        raise NotImplementedError

    def list_collections(self) -> list[str]:
        raise NotImplementedError

    def describe(self, name: str) -> VectorCollection:
        raise NotImplementedError

    def scan(
        self,
        name: str,
        filters: list[dict[str, Any]] | None = None,
        max_records: int = 5000,
    ) -> list[dict[str, Any]]:
        raise NotImplementedError

    def count(self, name: str, filters: list[dict[str, Any]] | None = None) -> int:
        raise NotImplementedError

    def search(
        self,
        name: str,
        vector: list[float],
        top_k: int,
        filters: list[dict[str, Any]] | None = None,
    ) -> list[dict[str, Any]]:
        raise NotImplementedError

    def close(self) -> None:
        pass

    @staticmethod
    def _validate_filters(filters: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
        cleaned = []
        for item in filters or []:
            field_name = str(item.get("field") or "").strip()
            op = str(item.get("op") or "eq").lower()
            if not field_name or op not in FILTER_OPS:
                raise VectorError(f"不支持的过滤条件: {item}")
            cleaned.append({"field": field_name, "op": op, "value": item.get("value")})
        return cleaned
