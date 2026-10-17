from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

import httpx

from .. import config
from ..core.trace import SPAN_LLM_HTTP, Trace


class LLMError(RuntimeError):
    pass


@dataclass
class ProviderConfig:
    name: str
    base_url: str
    api_key: str
    model: str


@dataclass
class LLMResult:
    content: str
    provider: str
    model: str
    usage: dict[str, Any]


def provider_name(base_url: str, fallback: str = "OpenAI-compatible") -> str:
    host = urlparse(base_url).netloc.lower()
    if "deepseek" in host:
        return "DeepSeek"
    if "openrouter" in host:
        return "OpenRouter"
    if "openai" in host:
        return "OpenAI"
    if "bigmodel" in host:
        return "BigModel"
    if "siliconflow" in host:
        return "SiliconFlow"
    if "ollama" in host or "localhost" in host or "127.0.0.1" in host:
        return "Ollama"
    return host or fallback


def _endpoints(base_url: str) -> list[str]:
    base = base_url.rstrip("/")
    if base.endswith("/chat/completions"):
        return [base]
    if base.endswith("/v1"):
        return [base + "/chat/completions"]
    return [base + "/chat/completions", base + "/v1/chat/completions"]


def extract_json(text: str) -> dict[str, Any]:
    raw = text.strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```[a-zA-Z0-9_-]*\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass
    start = raw.find("{")
    end = raw.rfind("}")
    if start >= 0 and end > start:
        candidate = raw[start:end + 1]
        try:
            return json.loads(candidate)
        except json.JSONDecodeError as exc:
            raise LLMError(f"模型返回的 JSON 无法解析: {exc}") from exc
    raise LLMError("模型没有返回 JSON 对象")


def _is_loopback(base_url: str) -> bool:
    host = urlparse(base_url).netloc.lower()
    return "localhost" in host or "127.0.0.1" in host


def build_providers() -> list[ProviderConfig]:
    providers: list[ProviderConfig] = []
    primary_key = config.LLM_API_KEY
    if config.LLM_MODEL and (primary_key or _is_loopback(config.LLM_BASE_URL)):
        providers.append(
            ProviderConfig(
                name=provider_name(config.LLM_BASE_URL),
                base_url=config.LLM_BASE_URL,
                api_key=primary_key,
                model=config.LLM_MODEL,
            )
        )

    fallback_base = os.getenv("LLM_FALLBACK_BASE_URL", "")
    fallback_model = os.getenv("LLM_FALLBACK_MODEL", "")
    if fallback_base and fallback_model:
        providers.append(
            ProviderConfig(
                name=provider_name(fallback_base, "Fallback"),
                base_url=fallback_base.rstrip("/"),
                api_key=os.getenv("LLM_FALLBACK_API_KEY", ""),
                model=fallback_model,
            )
        )

    # Local development fallback: a working OpenAI-compatible key already present
    # in the environment (BigModel / Zhipu). This keeps the demo usable when the
    # primary key is expired or scoped to another service.
    bm_key = os.getenv("MIDSCENE_MODEL_API_KEY", "")
    bm_base = os.getenv("MIDSCENE_MODEL_BASE_URL", "")
    if bm_key and bm_base:
        providers.append(
            ProviderConfig(
                name="BigModel",
                base_url=bm_base.rstrip("/"),
                api_key=bm_key,
                model=os.getenv("WREN_INSIGHT_FALLBACK_MODEL", "glm-4-flash"),
            )
        )

    ollama_model = os.getenv("OLLAMA_MODEL", "qwen2.5:3b")
    if ollama_model:
        providers.append(
            ProviderConfig(
                name="Ollama",
                base_url="http://127.0.0.1:11434/v1",
                api_key="",
                model=ollama_model,
            )
        )

    deduped: list[ProviderConfig] = []
    seen: set[tuple[str, str]] = set()
    for provider in providers:
        key = (provider.base_url.rstrip("/"), provider.model)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(provider)
    return deduped


class LLMClient:
    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        model: str | None = None,
        timeout: float | None = None,
    ) -> None:
        self.timeout = timeout or config.LLM_TIMEOUT_SECONDS
        if base_url or api_key is not None or model:
            self.providers = [
                ProviderConfig(
                    name=provider_name(base_url or config.LLM_BASE_URL),
                    base_url=(base_url or config.LLM_BASE_URL).rstrip("/"),
                    api_key=api_key if api_key is not None else config.LLM_API_KEY,
                    model=model or config.LLM_MODEL,
                )
            ]
        else:
            self.providers = build_providers()
        self._preferred = 0
        self.provider = self.providers[0].name if self.providers else "未配置"
        self.model = self.providers[0].model if self.providers else ""
        self.last_error = ""

    @property
    def configured(self) -> bool:
        return bool(self.providers) and any(
            provider.api_key or _is_loopback(provider.base_url)
            for provider in self.providers
        )

    def _ordered_providers(self) -> list[ProviderConfig]:
        if not self.providers:
            return []
        preferred = self.providers[self._preferred]
        return [preferred] + [p for i, p in enumerate(self.providers) if i != self._preferred]

    def _chat_provider(
        self,
        provider: ProviderConfig,
        messages: list[dict[str, str]],
        *,
        temperature: float,
        max_tokens: int,
        json_mode: bool,
        trace: Trace | None = None,
    ) -> LLMResult:
        payload: dict[str, Any] = {
            "model": provider.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": False,
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}

        headers = {"Content-Type": "application/json"}
        if provider.api_key:
            headers["Authorization"] = f"Bearer {provider.api_key}"

        last_error: Exception | None = None
        for endpoint in _endpoints(provider.base_url):
            for attempt_json_mode in ([True, False] if json_mode else [False]):
                body = dict(payload)
                if not attempt_json_mode:
                    body.pop("response_format", None)
                def _post() -> httpx.Response:
                    with httpx.Client(timeout=self.timeout) as client:
                        return client.post(endpoint, headers=headers, json=body)

                try:
                    if trace is not None:
                        with trace.span(
                            SPAN_LLM_HTTP, endpoint=endpoint, json_mode=attempt_json_mode
                        ):
                            response = _post()
                    else:
                        response = _post()
                except httpx.HTTPError as exc:
                    last_error = LLMError(f"无法连接 {provider.name} 服务: {exc}")
                    break
                if response.status_code == 404:
                    last_error = LLMError(f"{provider.name} 接口不存在: {endpoint}")
                    break
                if response.status_code in (401, 403):
                    last_error = LLMError(
                        f"{provider.name} 鉴权失败（HTTP {response.status_code}）"
                    )
                    break
                if response.status_code >= 400:
                    detail = response.text[:400]
                    last_error = LLMError(
                        f"{provider.name} 调用失败（HTTP {response.status_code}）: {detail}"
                    )
                    if attempt_json_mode and response.status_code == 400:
                        continue
                    break
                data = response.json()
                choices = data.get("choices") or []
                if not choices:
                    last_error = LLMError(
                        f"{provider.name} 返回空结果: {str(data)[:300]}"
                    )
                    break
                content = choices[0].get("message", {}).get("content") or ""
                return LLMResult(
                    content=content,
                    provider=provider.name,
                    model=provider.model,
                    usage=data.get("usage") or {},
                )
        raise last_error or LLMError(f"{provider.name} 调用失败")

    def chat(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float | None = None,
        max_tokens: int = 1800,
        json_mode: bool = True,
        trace: Trace | None = None,
    ) -> LLMResult:
        if not self.configured:
            raise LLMError(
                "未配置大模型。请设置 LLM_BASE_URL、LLM_API_KEY、LLM_MODEL，"
                "或启动本地 Ollama（LLM_BASE_URL=http://127.0.0.1:11434/v1）。"
            )
        errors: list[str] = []
        providers = self._ordered_providers()
        for index, provider in enumerate(providers):
            try:
                result = self._chat_provider(
                    provider,
                    messages,
                    temperature=config.LLM_TEMPERATURE if temperature is None else temperature,
                    max_tokens=max_tokens,
                    json_mode=json_mode,
                    trace=trace,
                )
            except LLMError as exc:
                errors.append(f"{provider.name}: {exc}")
                self.last_error = " | ".join(errors[-3:])
                continue
            # Remember the working provider by identity.
            for i, candidate in enumerate(self.providers):
                if candidate is provider:
                    self._preferred = i
                    break
            self.provider = provider.name
            self.model = provider.model
            return result
        raise LLMError("所有模型服务均不可用：" + " | ".join(errors[-3:]))
