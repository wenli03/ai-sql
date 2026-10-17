from __future__ import annotations

from typing import Any
from urllib.parse import quote

import httpx

from ..models import ConnectionConfig
from .base import VectorAdapter, VectorCollection, VectorError


def _base_url(conn: ConnectionConfig) -> str:
    url = (conn.host or "").strip() or "http://127.0.0.1:6333"
    if not url.startswith(("http://", "https://")):
        url = "http://" + url
    return url.rstrip("/")


def _payload_fields(payload: dict[str, Any] | None) -> dict[str, Any]:
    result = {}
    for key, value in (payload or {}).items():
        if isinstance(value, (str, int, float, bool)) or value is None:
            result[str(key)] = value
        else:
            result[str(key)] = str(value)
    return result


class QdrantAdapter(VectorAdapter):
    source_type = "qdrant"

    def __init__(self, conn: ConnectionConfig) -> None:
        self.conn = conn
        self.base_url = _base_url(conn)
        self.api_key = conn.password or conn.options.get("api_key") or ""
        self.collection_filter = conn.options.get("collection") or ""

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["api-key"] = self.api_key
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
            raise VectorError(f"无法连接 Qdrant: {exc}") from exc
        if response.status_code >= 400:
            raise VectorError(
                f"Qdrant API HTTP {response.status_code}: {response.text[:500]}"
            )
        if not response.content:
            return {}
        try:
            return response.json()
        except ValueError as exc:
            raise VectorError(f"Qdrant 返回非 JSON: {response.text[:300]}") from exc

    def test(self) -> dict[str, Any]:
        self._request("GET", "/collections")
        return {"ok": True, "source": "qdrant", "url": self.base_url}

    def list_collections(self) -> list[str]:
        data = self._request("GET", "/collections")
        collections = data.get("result", {}).get("collections", [])
        names = [str(item.get("name")) for item in collections if item.get("name")]
        if self.collection_filter:
            names = [name for name in names if name == self.collection_filter]
        return names

    def _collection_info(self, name: str) -> dict[str, Any]:
        data = self._request("GET", f"/collections/{quote(name, safe='')}")
        result = data.get("result") or {}
        if not result:
            raise VectorError(f"Qdrant collection 不存在: {name}")
        return result

    def _vector_config(self, info: dict[str, Any]) -> tuple[str, int | None, str]:
        params = (info.get("config") or {}).get("params") or {}
        vectors = params.get("vectors")
        if isinstance(vectors, dict):
            # Unnamed vector config: {"size": 768, "distance": "Cosine"}
            if "size" in vectors:
                return (
                    "vector",
                    int(vectors.get("size") or 0) or None,
                    str(vectors.get("distance") or "cosine").lower(),
                )
            # Named vectors: {"title": {"size": 768, "distance": "Cosine"}}
            if not vectors:
                return "vector", None, "cosine"
            vector_name, config = next(iter(vectors.items()))
            config = config if isinstance(config, dict) else {}
            return (
                str(vector_name),
                int(config.get("size") or 0) or None,
                str(config.get("distance") or "cosine").lower(),
            )
        return "vector", None, "cosine"

    def describe(self, name: str) -> VectorCollection:
        info = self._collection_info(name)
        vector_name, dimension, metric = self._vector_config(info)
        count = int(info.get("points_count") or info.get("vectors_count") or 0)
        sample = self.scan(name, max_records=5)
        fields: dict[str, str] = {}
        for record in sample:
            for key, value in record.items():
                if key in ("id", "document", "score", "distance"):
                    continue
                if key not in fields:
                    fields[key] = (
                        "BOOLEAN" if isinstance(value, bool)
                        else "INTEGER" if isinstance(value, int)
                        else "DOUBLE" if isinstance(value, float)
                        else "VARCHAR"
                    )
        return VectorCollection(
            name=name,
            location=f"{self.base_url}/dashboard#/collections/{name}",
            vector_field=vector_name,
            dimension=dimension,
            metric=metric,
            count=count,
            metadata_fields=fields,
            sample=sample,
            raw=info,
        )

    def _filter(self, filters: list[dict[str, Any]] | None) -> dict[str, Any] | None:
        clauses = []
        for item in self._validate_filters(filters):
            field_name = item["field"]
            op = item["op"]
            value = item["value"]
            if op == "eq":
                clauses.append({"key": field_name, "match": {"value": value}})
            elif op == "in":
                values = value if isinstance(value, list) else [value]
                clauses.append({"key": field_name, "match": {"any": values}})
            elif op == "gte":
                clauses.append({"key": field_name, "range": {"gte": value}})
            elif op == "lte":
                clauses.append({"key": field_name, "range": {"lte": value}})
            elif op == "contains":
                clauses.append({"key": field_name, "match": {"text": str(value)}})
        return {"must": clauses} if clauses else None

    def _records(self, points: list[dict[str, Any]], *, with_distance: bool = False) -> list[dict[str, Any]]:
        records = []
        for point in points or []:
            payload = point.get("payload") or {}
            record = {"id": str(point.get("id"))}
            record.update(_payload_fields(payload))
            if payload.get("document") is not None:
                record["document"] = str(payload.get("document"))
            elif payload.get("content") is not None:
                record["document"] = str(payload.get("content"))
            elif payload.get("text") is not None:
                record["document"] = str(payload.get("text"))
            if with_distance and point.get("score") is not None:
                score = float(point["score"])
                record["score"] = round(score, 6)
                # Qdrant cosine/dot scores are similarities (higher is better);
                # euclid/manhattan scores are distances (lower is better).
                metric = str((point.get("_metric") or "cosine")).lower()
                if metric in ("euclid", "manhattan"):
                    record["distance"] = round(score, 6)
                else:
                    record["distance"] = round(1.0 - score, 6)
            records.append(record)
        return records

    def scan(
        self,
        name: str,
        filters: list[dict[str, Any]] | None = None,
        max_records: int = 5000,
    ) -> list[dict[str, Any]]:
        payload: dict[str, Any] = {
            "limit": min(1000, max_records),
            "with_payload": True,
            "with_vector": False,
        }
        qfilter = self._filter(filters)
        if qfilter:
            payload["filter"] = qfilter
        records: list[dict[str, Any]] = []
        offset = None
        while len(records) < max_records:
            page_payload = dict(payload)
            page_payload["limit"] = min(1000, max_records - len(records))
            if offset is not None:
                page_payload["offset"] = offset
            data = self._request(
                "POST",
                f"/collections/{quote(name, safe='')}/points/scroll",
                page_payload,
            )
            result = data.get("result") or {}
            points = result.get("points") or []
            records.extend(self._records(points))
            offset = result.get("next_page_offset")
            if not offset or not points:
                break
        return records[:max_records]

    def count(self, name: str, filters: list[dict[str, Any]] | None = None) -> int:
        payload: dict[str, Any] = {"exact": True}
        qfilter = self._filter(filters)
        if qfilter:
            payload["filter"] = qfilter
        data = self._request(
            "POST",
            f"/collections/{quote(name, safe='')}/points/count",
            payload,
        )
        return int((data.get("result") or {}).get("count") or 0)

    def search(
        self,
        name: str,
        vector: list[float],
        top_k: int,
        filters: list[dict[str, Any]] | None = None,
    ) -> list[dict[str, Any]]:
        info = self._collection_info(name)
        vector_name, _, metric = self._vector_config(info)
        payload: dict[str, Any] = {
            "vector": vector,
            "limit": top_k,
            "with_payload": True,
            "with_vector": False,
        }
        if vector_name and vector_name != "vector":
            payload["using"] = vector_name
        qfilter = self._filter(filters)
        if qfilter:
            payload["filter"] = qfilter
        data = self._request(
            "POST",
            f"/collections/{quote(name, safe='')}/points/search",
            payload,
        )
        points = data.get("result") or []
        for point in points:
            point["_metric"] = metric
        return self._records(points, with_distance=True)
