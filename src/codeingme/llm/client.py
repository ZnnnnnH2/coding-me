from __future__ import annotations

from collections import OrderedDict
import copy
from dataclasses import dataclass, field
import json
import os
from typing import Any

import httpx


DEFAULT_BASE_URL = "https://9985678.xyz/v1"
DEFAULT_MODEL = "gpt-5.4"
DEFAULT_TIMEOUT = 90.0


@dataclass(slots=True)
class LLMConfig:
    api_key: str
    base_url: str = DEFAULT_BASE_URL
    model: str = DEFAULT_MODEL
    reasoning_effort: str | None = "medium"
    temperature: float = 0.2
    timeout: float = DEFAULT_TIMEOUT
    trust_env: bool = False
    cache_enabled: bool = True
    cache_size: int = 256

    @classmethod
    def from_env(cls) -> LLMConfig | None:
        api_key = os.getenv("CODEINGME_LLM_API_KEY") or os.getenv("OPENAI_API_KEY")
        if not api_key:
            return None
        return cls(
            api_key=api_key,
            base_url=os.getenv("CODEINGME_LLM_BASE_URL", DEFAULT_BASE_URL),
            model=os.getenv("CODEINGME_LLM_MODEL", DEFAULT_MODEL),
            reasoning_effort=os.getenv("CODEINGME_LLM_REASONING_EFFORT", "medium"),
            temperature=float(os.getenv("CODEINGME_LLM_TEMPERATURE", "0.2")),
            timeout=float(os.getenv("CODEINGME_LLM_TIMEOUT", str(DEFAULT_TIMEOUT))),
            trust_env=os.getenv("CODEINGME_LLM_TRUST_ENV", "0") == "1",
            cache_enabled=os.getenv("CODEINGME_LLM_CACHE_ENABLED", "1") != "0",
            cache_size=int(os.getenv("CODEINGME_LLM_CACHE_SIZE", "256")),
        )


@dataclass(slots=True)
class LLMCompletion:
    model: str
    content: str
    usage: dict[str, Any] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict)
    cached: bool = False


class RelayLLMClient:
    def __init__(self, config: LLMConfig, transport: httpx.BaseTransport | None = None) -> None:
        self.config = config
        self._client = httpx.Client(
            base_url=config.base_url.rstrip("/"),
            timeout=config.timeout,
            transport=transport,
            trust_env=config.trust_env,
            headers={
                "Authorization": f"Bearer {config.api_key}",
                "Content-Type": "application/json",
            },
        )
        self._cache_enabled = config.cache_enabled and config.cache_size > 0
        self._cache_size = max(config.cache_size, 0)
        self._completion_cache: OrderedDict[str, LLMCompletion] = OrderedDict()
        self._cache_hits = 0
        self._cache_misses = 0

    @classmethod
    def from_env(cls) -> RelayLLMClient | None:
        config = LLMConfig.from_env()
        return cls(config) if config is not None else None

    def close(self) -> None:
        self._client.close()

    def clear_cache(self) -> None:
        self._completion_cache.clear()
        self._cache_hits = 0
        self._cache_misses = 0

    def cache_info(self) -> dict[str, int]:
        return {
            "entries": len(self._completion_cache),
            "hits": self._cache_hits,
            "misses": self._cache_misses,
        }

    def list_models(self) -> list[str]:
        response = self._client.get("/models")
        response.raise_for_status()
        payload = response.json()
        return [item["id"] for item in payload.get("data", [])]

    def prompt(
        self,
        system_prompt: str,
        user_prompt: str,
        *,
        model: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> LLMCompletion:
        payload: dict[str, Any] = {
            "model": model or self.config.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": self.config.temperature if temperature is None else temperature,
            "stream": False,
        }
        if self.config.reasoning_effort:
            payload["reasoning_effort"] = self.config.reasoning_effort
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens

        cache_key = self._cache_key(payload)
        if cache_key is not None:
            cached_completion = self._completion_cache.get(cache_key)
            if cached_completion is not None:
                self._cache_hits += 1
                self._completion_cache.move_to_end(cache_key)
                return self._clone_completion(cached_completion, cached=True)
            self._cache_misses += 1

        response = self._client.post("/chat/completions", json=payload)
        response.raise_for_status()
        data = response.json()
        message = data["choices"][0]["message"]["content"]
        completion = LLMCompletion(
            model=data.get("model", payload["model"]),
            content=self._coerce_content(message),
            usage=data.get("usage", {}),
            raw=data,
        )
        if cache_key is not None:
            self._completion_cache[cache_key] = self._clone_completion(completion)
            self._completion_cache.move_to_end(cache_key)
            while len(self._completion_cache) > self._cache_size:
                self._completion_cache.popitem(last=False)
        return completion

    def _cache_key(self, payload: dict[str, Any]) -> str | None:
        if not self._cache_enabled:
            return None
        return json.dumps(payload, sort_keys=True, ensure_ascii=True)

    def _clone_completion(self, completion: LLMCompletion, *, cached: bool | None = None) -> LLMCompletion:
        return LLMCompletion(
            model=completion.model,
            content=completion.content,
            usage=copy.deepcopy(completion.usage),
            raw=copy.deepcopy(completion.raw),
            cached=completion.cached if cached is None else cached,
        )

    def _coerce_content(self, content: Any) -> str:
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts: list[str] = []
            for item in content:
                if isinstance(item, dict) and "text" in item:
                    parts.append(str(item["text"]))
                else:
                    parts.append(str(item))
            return "\n".join(parts)
        return str(content)
