from __future__ import annotations

from .base import VECTOR_SOURCES, VectorAdapter, VectorError


def get_adapter(conn) -> VectorAdapter:
    if conn.dialect == "qdrant":
        from .qdrant import QdrantAdapter

        return QdrantAdapter(conn)
    if conn.dialect == "chroma":
        from .chroma import ChromaAdapter

        return ChromaAdapter(conn)
    if conn.dialect == "weaviate":
        from .weaviate import WeaviateAdapter

        return WeaviateAdapter(conn)
    raise VectorError(f"暂不支持的向量数据库: {conn.dialect}")
