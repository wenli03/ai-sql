from __future__ import annotations

import re
from typing import Any
from urllib.parse import quote

import httpx

from ..models import ConnectionConfig
from .base import VectorAdapter, VectorCollection, VectorError

_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _base_url(conn: ConnectionConfig) -> str:
    url = (conn.host or "").strip() or "http://127.0.0.1:8081"
    if not url.startswith(("http://", "https://")):
        url = "http://" + url
    return url.rstrip("/")


def _gql_string(value: Any) -> str:
    return '"' + str(value).replace("\\", "\\\\").replace('"', '\\"') + '"'


class WeaviateAdapter(VectorAdapter):
    source_type = "weaviate"

    def __init__(self, conn: ConnectionConfig) -> None:
        self.conn = conn
        self.base_url = _base_url(conn)
        self.api_key = conn.password or conn.options.get("api_key") or ""
        self.collection_filter = conn.options.get("collection") or ""

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
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
            raise VectorError(f"无法连接 Weaviate: {exc}") from exc
        if response.status_code >= 400:
            raise VectorError(
                f"Weaviate API HTTP {response.status_code}: {response.text[:500]}"
            )
        if not response.content:
            return {}
        try:
            return response.json()
        except ValueError as exc:
            raise VectorError(f"Weaviate 返回非 JSON: {response.text[:300]}") from exc

    def test(self) -> dict[str, Any]:
        data = self._request("GET", "/v1/meta")
        return {
            "ok": True,
            "source": "weaviate",
            "url": self.base_url,
            "version": data.get("version"),
        }

    def _schema(self) -> dict[str, Any]:
        return self._request("GET", "/v1/schema") or {}

    def _classes(self) -> list[dict[str, Any]]:
        return list(self._schema().get("classes") or [])

    def list_collections(self) -> list[str]:
        names = [str(item.get("class")) for item in self._classes() if item.get("class")]
        if self.collection_filter:
            names = [name for name in names if name == self.collection_filter]
        return names

    def _class(self, name: str) -> dict[str, Any]:
        for item in self._classes():
            if item.get("class") == name:
                return item
        raise VectorError(f"Weaviate class 不存在: {name}")

    def _count(self, name: str, where: dict[str, Any] | None = None) -> int:
        if not _IDENT.match(name):
            raise VectorError(f"非法 Weaviate class 名称: {name}")
        where_text = ""
        if where:
            where_text = f"(where: {self._gql_where(where)})"
        query = f"{{ Aggregate {{ {name}{where_text} {{ meta {{ count }} }} }} }}"
        data = self._request("POST", "/v1/graphql", {"query": query})
        if data.get("errors"):
            raise VectorError(f"Weaviate GraphQL 错误: {data['errors']}")
        items = ((data.get("data") or {}).get("Aggregate") or {}).get(name) or []
        if not items:
            return 0
        return int(((items[0] or {}).get("meta") or {}).get("count") or 0)

    def describe(self, name: str) -> VectorCollection:
        item = self._class(name)
        properties = item.get("properties") or []
        count = self._count(name)
        sample = self.scan(name, max_records=5)
        dimension = None
        try:
            data = self._request("GET", f"/v1/objects?class={quote(name)}&limit=1&include=vector")
            objects = data.get("objects") or []
            if objects and objects[0].get("vector"):
                dimension = len(objects[0]["vector"])
        except Exception:
            pass
        metric = str((item.get("vectorIndexConfig") or {}).get("distance") or "cosine").lower()
        fields = {}
        for prop in properties:
            prop_name = str(prop.get("name") or "")
            data_types = prop.get("dataType") or []
            data_type = str(data_types[0]) if data_types else "text"
            fields[prop_name] = (
                "INTEGER" if data_type == "int"
                else "DOUBLE" if data_type == "number"
                else "BOOLEAN" if data_type == "boolean"
                else "TIMESTAMP" if data_type == "date"
                else "VARCHAR"
            )
        return VectorCollection(
            name=name,
            location=f"{self.base_url}/v1/objects?class={name}",
            vector_field="vector",
            dimension=dimension,
            metric=metric,
            count=count,
            metadata_fields=fields,
            sample=sample,
            raw=item,
        )

    def _where(self, filters: list[dict[str, Any]] | None) -> dict[str, Any] | None:
        operands = []
        for item in self._validate_filters(filters):
            field_name = item["field"]
            op = item["op"]
            value = item["value"]
            if not _IDENT.match(field_name):
                raise VectorError(f"非法 Weaviate 属性名: {field_name}")
            if op == "eq":
                if isinstance(value, bool):
                    operands.append({"path": [field_name], "operator": "Equal", "valueBoolean": value})
                elif isinstance(value, (int, float)):
                    operands.append({"path": [field_name], "operator": "Equal", "valueNumber": value})
                else:
                    operands.append({"path": [field_name], "operator": "Equal", "valueText": str(value)})
            elif op == "in":
                values = value if isinstance(value, list) else [value]
                if values and all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in values):
                    operands.append({"path": [field_name], "operator": "ContainsAny", "valueNumberArray": values})
                else:
                    operands.append({"path": [field_name], "operator": "ContainsAny", "valueTextArray": [str(v) for v in values]})
            elif op == "gte":
                operands.append({"path": [field_name], "operator": "GreaterThanEqual", "valueNumber": value})
            elif op == "lte":
                operands.append({"path": [field_name], "operator": "LessThanEqual", "valueNumber": value})
            elif op == "contains":
                operands.append({"path": [field_name], "operator": "Like", "valueText": f"*{value}*"})
        if not operands:
            return None
        if len(operands) == 1:
            return operands[0]
        return {"operator": "And", "operands": operands}

    def _gql_where(self, where: dict[str, Any]) -> str:
        if "operands" in where:
            inner = ", ".join(self._gql_where(item) for item in where["operands"])
            return f"{{operator: {where['operator']}, operands: [{inner}]}}"
        path_text = ", ".join(_gql_string(p) for p in where["path"])
        parts = [f"path: [{path_text}]", f"operator: {where['operator']}"]
        for key, value in where.items():
            if key in ("path", "operator"):
                continue
            if isinstance(value, str):
                parts.append(f"{key}: {_gql_string(value)}")
            elif isinstance(value, bool):
                parts.append(f"{key}: {'true' if value else 'false'}")
            elif isinstance(value, (int, float)):
                parts.append(f"{key}: {value}")
            elif isinstance(value, list):
                values = ", ".join(_gql_string(v) for v in value)
                parts.append(f"{key}: [{values}]")
        return "{" + ", ".join(parts) + "}"

    def scan(
        self,
        name: str,
        filters: list[dict[str, Any]] | None = None,
        max_records: int = 5000,
    ) -> list[dict[str, Any]]:
        where = self._where(filters)
        if where:
            # Object listing supports a where filter via the same GraphQL-compatible
            # syntax in newer Weaviate versions; fall back to client-side filtering.
            pass
        records: list[dict[str, Any]] = []
        offset = 0
        while len(records) < max_records:
            limit = min(100, max_records - len(records))
            data = self._request(
                "GET",
                f"/v1/objects?class={quote(name)}&limit={limit}&offset={offset}",
            )
            objects = data.get("objects") or []
            if not objects:
                break
            for obj in objects:
                properties = obj.get("properties") or {}
                record = {"id": str(obj.get("id"))}
                record.update({k: v for k, v in properties.items() if isinstance(v, (str, int, float, bool)) or v is None})
                for key in ("document", "content", "text"):
                    if properties.get(key) is not None:
                        record["document"] = str(properties.get(key))
                        break
                records.append(record)
            offset += len(objects)
            if len(objects) < limit:
                break
        if where:
            records = [record for record in records if self._match_record(record, filters)]
        return records[:max_records]

    def _match_record(self, record: dict[str, Any], filters: list[dict[str, Any]] | None) -> bool:
        for item in self._validate_filters(filters):
            field_name = item["field"]
            op = item["op"]
            value = item["value"]
            actual = record.get(field_name)
            if op == "eq" and actual != value:
                return False
            if op == "in":
                values = value if isinstance(value, list) else [value]
                if actual not in values:
                    return False
            if op == "gte" and actual is not None and actual < value:
                return False
            if op == "lte" and actual is not None and actual > value:
                return False
            if op == "contains" and (actual is None or str(value).lower() not in str(actual).lower()):
                return False
        return True

    def count(self, name: str, filters: list[dict[str, Any]] | None = None) -> int:
        if filters:
            return len(self.scan(name, filters, max_records=100000))
        return self._count(name)

    def search(
        self,
        name: str,
        vector: list[float],
        top_k: int,
        filters: list[dict[str, Any]] | None = None,
    ) -> list[dict[str, Any]]:
        if not _IDENT.match(name):
            raise VectorError(f"非法 Weaviate class 名称: {name}")
        collection = self._class(name)
        properties = [
            str(prop.get("name"))
            for prop in (collection.get("properties") or [])
            if _IDENT.match(str(prop.get("name") or ""))
        ]
        fields = " ".join(properties)
        where = self._where(filters)
        where_text = f", where: {self._gql_where(where)}" if where else ""
        vector_text = ", ".join(str(float(v)) for v in vector)
        query = (
            "{ Get { "
            f"{name}(nearVector: {{vector: [{vector_text}]}}, limit: {int(top_k)}{where_text}) "
            f"{{ {fields} _additional {{ id distance }} }}"
            " } }"
        )
        data = self._request("POST", "/v1/graphql", {"query": query})
        if data.get("errors"):
            raise VectorError(f"Weaviate GraphQL 错误: {data['errors']}")
        items = ((data.get("data") or {}).get("Get") or {}).get(name) or []
        records = []
        for item in items:
            record = {}
            additional = item.get("_additional") or {}
            for key, value in item.items():
                if key == "_additional":
                    continue
                record[key] = value
            if not record.get("id"):
                record["id"] = str(additional.get("id") or "")
            distance = additional.get("distance")
            if distance is not None:
                record["distance"] = round(float(distance), 6)
                record["score"] = round(1.0 - float(distance), 6)
            for key in ("document", "content", "text"):
                if record.get(key) is not None:
                    record["document"] = str(record.get(key))
                    break
            records.append(record)
        return records
