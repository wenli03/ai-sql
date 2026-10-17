from __future__ import annotations

from typing import Any

import httpx

from .. import config


class EmbeddingError(RuntimeError):
    pass


class EmbeddingClient:
    """OpenAI-compatible text embedding client (Ollama by default)."""

    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        model: str | None = None,
        timeout: float = 60.0,
    ) -> None:
        self.base_url = (base_url or config.EMBEDDING_BASE_URL).rstrip("/")
        self.api_key = api_key if api_key is not None else config.EMBEDDING_API_KEY
        self.model = model or config.EMBEDDING_MODEL
        self.timeout = timeout
        self._dimension: int | None = None

    @property
    def configured(self) -> bool:
        return bool(self.model and self.base_url)

    def _endpoints(self) -> list[str]:
        base = self.base_url
        if base.endswith("/embeddings"):
            return [base]
        if base.endswith("/v1"):
            return [base + "/embeddings"]
        return [base + "/v1/embeddings", base + "/embeddings"]

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        if not self.configured:
            raise EmbeddingError(
                "未配置向量化模型。请设置 EMBEDDING_BASE_URL / EMBEDDING_MODEL"
                "（默认 Ollama: http://127.0.0.1:11434/v1 + nomic-embed-text）。"
            )
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        payload = {"model": self.model, "input": texts}
        last_error: Exception | None = None
        for endpoint in self._endpoints():
            try:
                with httpx.Client(timeout=self.timeout) as client:
                    response = client.post(endpoint, headers=headers, json=payload)
                if response.status_code >= 400:
                    last_error = EmbeddingError(
                        f"向量化服务返回 HTTP {response.status_code}: {response.text[:300]}"
                    )
                    continue
                data = response.json()
                items = data.get("data") or []
                vectors = [item.get("embedding") for item in items]
                if not vectors or any(not isinstance(v, list) for v in vectors):
                    last_error = EmbeddingError(f"向量化服务返回格式异常: {str(data)[:300]}")
                    continue
                self._dimension = len(vectors[0])
                return vectors
            except httpx.HTTPError as exc:
                last_error = EmbeddingError(f"无法连接向量化服务: {exc}")
                continue
        raise last_error or EmbeddingError("向量化失败")

    def embed_one(self, text: str) -> list[float]:
        return self.embed([text])[0]

    @property
    def dimension(self) -> int | None:
        return self._dimension
