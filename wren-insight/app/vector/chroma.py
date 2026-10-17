from __future__ import annotations

from typing import Any
from urllib.parse import quote

import httpx

from ..models import ConnectionConfig
from .base import VectorAdapter, VectorCollection, VectorError


def _base_url(conn: ConnectionConfig) -> str:
    url = (conn.host or "").strip() or "http://127.0.0.1:8001"
    if not url.startswith(("http://", "https://")):
        url = "http://" + url
    return url.rstrip("/")


class ChromaAdapter(VectorAdapter):
    source_type = "chroma"

    def __init__(self, conn: ConnectionConfig) -> None:
        self.conn = conn
        self.base_url = _base_url(conn)
        self.tenant = conn.options.get("tenant") or "default_tenant"
        self.database = conn.options.get("database") or "default_database"
        self.collection_filter = conn.options.get("collection") or ""
        self.api_key = conn.password or conn.options.get("api_key") or ""
        self._collection_cache: dict[str, dict[str, Any]] = {}

    @property
    def api_prefix(self) -> str:
        return (
            f"/api/v2/tenants/{quote(self.tenant, safe='')}"
            f"/databases/{quote(self.database, safe='')}"
        )

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["x-chroma-token"] = self.api_key
        return headers

    def _request(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
        *,
        timeout: float = 30.0,
    ) -> Any:
        try:
            with httpx.Client(timeout=timeout) as client:
                response = client.request(
                    method,
                    self.base_url + path,
                    headers=self._headers(),
                    json=payload,
                )
        except httpx.HTTPError as exc:
            raise VectorError(f"无法连接 Chroma: {exc}") from exc
        if response.status_code >= 400:
            raise VectorError(
                f"Chroma API HTTP {response.status_code}: {response.text[:500]}"
            )
        if not response.content:
            return {}
        try:
            return response.json()
        except ValueError as exc:
            raise VectorError(f"Chroma 返回非 JSON: {response.text[:300]}") from exc

    def test(self) -> dict[str, Any]:
        self._request("GET", "/api/v2/heartbeat")
        return {"ok": True, "source": "chroma", "url": self.base_url}

    def _load_collections(self) -> list[dict[str, Any]]:
        data = self._request("GET", f"{self.api_prefix}/collections")
        collections = data if isinstance(data, list) else []
        self._collection_cache = {str(item.get("name")): item for item in collections}
        return collections

    def list_collections(self) -> list[str]:
        collections = self._load_collections()
        names = [str(item["name"]) for item in collections if item.get("name")]
        if self.collection_filter:
            names = [name for name in names if name == self.collection_filter]
        return names

    def _collection(self, name: str) -> dict[str, Any]:
        if not self._collection_cache:
            self._load_collections()
        item = self._collection_cache.get(name)
        if not item:
            raise VectorError(f"Chroma collection 不存在: {name}")
        return item

    def _collection_path(self, name: str) -> str:
        item = self._collection(name)
        collection_id = item.get("id") or name
        return f"{self.api_prefix}/collections/{quote(str(collection_id), safe='')}"

    def _metric(self, item: dict[str, Any]) -> str:
        config = item.get("configuration_json") or {}
        hnsw = config.get("hnsw") or {}
        return str(hnsw.get("space") or "cosine")

    def describe(self, name: str) -> VectorCollection:
        item = self._collection(name)
        count = self.count(name)
        sample = self.scan(name, max_records=5)
        dimension = item.get("dimension")
        if dimension is None:
            try:
                payload = {
                    "limit": 1,
                    "include": ["embeddings", "metadatas", "documents"],
                }
                data = self._request("POST", f"{self._collection_path(name)}/get", payload)
                embeddings = data.get("embeddings") or []
                if embeddings and embeddings[0]:
                    dimension = len(embeddings[0])
            except Exception:
                pass
        fields: dict[str, str] = {}
        for record in sample:
            for key, value in record.items():
                if key in ("id", "document", "score", "distance"):
                    continue
                if key not in fields:
                    if isinstance(value, bool):
                        fields[key] = "BOOLEAN"
                    elif isinstance(value, int):
                        fields[key] = "INTEGER"
                    elif isinstance(value, float):
                        fields[key] = "DOUBLE"
                    else:
                        fields[key] = "VARCHAR"
        return VectorCollection(
            name=name,
            location=f"{self.base_url}/#/collections/{name}",
            vector_field="embedding",
            dimension=int(dimension) if dimension else None,
            metric=self._metric(item),
            count=count,
            metadata_fields=fields,
            sample=sample,
            raw=item,
        )

    def _where(self, filters: list[dict[str, Any]] | None) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
        clauses = []
        document_where = None
        for item in self._validate_filters(filters):
            field_name = item["field"]
            op = item["op"]
            value = item["value"]
            if field_name in ("document", "content", "text") and op == "contains":
                document_where = {"$contains": str(value)}
                continue
            if op == "eq":
                clauses.append({field_name: {"$eq": value}})
            elif op == "in":
                values = value if isinstance(value, list) else [value]
                clauses.append({field_name: {"$in": values}})
            elif op == "gte":
                clauses.append({field_name: {"$gte": value}})
            elif op == "lte":
                clauses.append({field_name: {"$lte": value}})
            elif op == "contains":
                raise VectorError("Chroma metadata 不支持 contains；请改用 eq/in，或对 document 字段使用 contains")
        if not clauses:
            where = None
        elif len(clauses) == 1:
            where = clauses[0]
        else:
            where = {"$and": clauses}
        return where, document_where

    def _records(self, data: dict[str, Any]) -> list[dict[str, Any]]:
        ids = data.get("ids") or []
        metadatas = data.get("metadatas") or []
        documents = data.get("documents") or []
        distances = data.get("distances")
        records = []
        for index, record_id in enumerate(ids):
            metadata = metadatas[index] if index < len(metadatas) and metadatas[index] else {}
            record = {"id": str(record_id)}
            record.update(metadata)
            if index < len(documents) and documents[index] is not None:
                record["document"] = str(documents[index])
            if distances and index < len(distances) and distances[index] is not None:
                distance = float(distances[index])
                record["distance"] = distance
                record["score"] = round(1.0 - distance, 6)
            records.append(record)
        return records

    def scan(
        self,
        name: str,
        filters: list[dict[str, Any]] | None = None,
        max_records: int = 5000,
    ) -> list[dict[str, Any]]:
        where, where_document = self._where(filters)
        payload: dict[str, Any] = {
            "limit": min(max_records, 1000),
            "include": ["metadatas", "documents"],
        }
        if where:
            payload["where"] = where
        if where_document:
            payload["where_document"] = where_document
        data = self._request("POST", f"{self._collection_path(name)}/get", payload)
        records = self._records(data)
        if len(records) < max_records and len(records) >= 1000:
            # Best-effort pagination for servers that accept offset.
            offset = len(records)
            while offset < max_records:
                page_payload = dict(payload)
                page_payload["offset"] = offset
                try:
                    page = self._request("POST", f"{self._collection_path(name)}/get", page_payload)
                except Exception:
                    break
                page_records = self._records(page)
                if not page_records:
                    break
                records.extend(page_records)
                offset += len(page_records)
                if len(page_records) < 1000:
                    break
        return records[:max_records]

    def count(self, name: str, filters: list[dict[str, Any]] | None = None) -> int:
        if filters:
            return len(self.scan(name, filters, max_records=100000))
        data = self._request("GET", f"{self._collection_path(name)}/count")
        if isinstance(data, dict):
            return int(data.get("count") or data.get("result") or 0)
        return int(data or 0)

    def search(
        self,
        name: str,
        vector: list[float],
        top_k: int,
        filters: list[dict[str, Any]] | None = None,
    ) -> list[dict[str, Any]]:
        where, where_document = self._where(filters)
        payload: dict[str, Any] = {
            "query_embeddings": [vector],
            "n_results": top_k,
            "include": ["metadatas", "documents", "distances"],
        }
        if where:
            payload["where"] = where
        if where_document:
            payload["where_document"] = where_document
        data = self._request("POST", f"{self._collection_path(name)}/query", payload)
        ids = (data.get("ids") or [[]])[0]
        metadatas = (data.get("metadatas") or [[]])[0]
        documents = (data.get("documents") or [[]])[0]
        distances = (data.get("distances") or [[]])[0]
        return self._records({
            "ids": ids,
            "metadatas": metadatas,
            "documents": documents,
            "distances": distances,
        })
