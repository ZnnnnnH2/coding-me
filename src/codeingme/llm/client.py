"""实现与 LLM 服务交互的客户端。"""

from __future__ import annotations

from collections import OrderedDict
import copy
from dataclasses import dataclass, field
import json
import os
import time
from typing import Any

import httpx


REQUIRED_LLM_ENV_VARS = (
    "CODEINGME_LLM_API_KEY",
    "CODEINGME_LLM_BASE_URL",
    "CODEINGME_LLM_MODEL",
)
DEFAULT_TIMEOUT = 90.0
DEFAULT_MAX_RETRIES = 2
DEFAULT_GENERATION_MAX_ATTEMPTS = 3


@dataclass(slots=True)
class LLMConfig:
    api_key: str
    base_url: str
    model: str
    reasoning_effort: str | None = None
    temperature: float = 0.2
    timeout: float = DEFAULT_TIMEOUT
    trust_env: bool = False
    cache_enabled: bool = True
    cache_size: int = 256
    max_retries: int = DEFAULT_MAX_RETRIES
    generation_max_attempts: int = DEFAULT_GENERATION_MAX_ATTEMPTS

    @classmethod
    def from_env(cls) -> LLMConfig | None:
        required_values = {
            name: _normalized_env_value(name)
            for name in REQUIRED_LLM_ENV_VARS
        }
        if not any(required_values.values()):
            return None
        missing = [name for name, value in required_values.items() if value is None]
        if missing:
            raise RuntimeError(cls.missing_required_env_message(missing))
        return cls(
            api_key=required_values["CODEINGME_LLM_API_KEY"],
            base_url=required_values["CODEINGME_LLM_BASE_URL"],
            model=required_values["CODEINGME_LLM_MODEL"],
            reasoning_effort=_normalized_env_value("CODEINGME_LLM_REASONING_EFFORT"),
            temperature=float(os.getenv("CODEINGME_LLM_TEMPERATURE", "0.2")),
            timeout=float(os.getenv("CODEINGME_LLM_TIMEOUT", str(DEFAULT_TIMEOUT))),
            trust_env=os.getenv("CODEINGME_LLM_TRUST_ENV", "0") == "1",
            cache_enabled=os.getenv("CODEINGME_LLM_CACHE_ENABLED", "1") != "0",
            cache_size=int(os.getenv("CODEINGME_LLM_CACHE_SIZE", "256")),
            max_retries=max(1, int(os.getenv("CODEINGME_LLM_MAX_RETRIES", str(DEFAULT_MAX_RETRIES)))),
            generation_max_attempts=max(
                1,
                int(
                    os.getenv(
                        "CODEINGME_LLM_GENERATION_MAX_ATTEMPTS",
                        str(DEFAULT_GENERATION_MAX_ATTEMPTS),
                    )
                ),
            ),
        )

    @classmethod
    def required_env_vars(cls) -> tuple[str, ...]:
        return REQUIRED_LLM_ENV_VARS

    @classmethod
    def missing_required_env_message(cls, missing: list[str] | None = None) -> str:
        missing_vars = missing or list(REQUIRED_LLM_ENV_VARS)
        return (
            "Missing LLM configuration. Set "
            + ", ".join(missing_vars)
            + " in .env before running LLM-backed commands."
        )


@dataclass(slots=True)
class LLMCompletion:
    model: str
    content: str
    usage: dict[str, Any] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict)
    cached: bool = False


class LLMProviderError(RuntimeError):
    pass


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
        payload = self._responses_payload(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            model=model or self.config.model,
            temperature=self.config.temperature if temperature is None else temperature,
            max_tokens=max_tokens,
            stream=True,
        )

        cache_key = self._cache_key(payload)
        if cache_key is not None:
            cached_completion = self._completion_cache.get(cache_key)
            if cached_completion is not None:
                self._cache_hits += 1
                self._completion_cache.move_to_end(cache_key)
                return self._clone_completion(cached_completion, cached=True)
            self._cache_misses += 1

        completion = self._request_completion(
            responses_payload=payload,
            chat_payload=self._chat_completion_payload(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                model=model or self.config.model,
                temperature=self.config.temperature if temperature is None else temperature,
                max_tokens=max_tokens,
            ),
        )
        if cache_key is not None:
            self._completion_cache[cache_key] = self._clone_completion(completion)
            self._completion_cache.move_to_end(cache_key)
            while len(self._completion_cache) > self._cache_size:
                self._completion_cache.popitem(last=False)
        return completion

    def _request_completion(
        self,
        *,
        responses_payload: dict[str, Any],
        chat_payload: dict[str, Any],
    ) -> LLMCompletion:
        errors: list[str] = []
        last_error: str = "Unknown provider error"
        for attempt in range(self.config.max_retries):
            completion, attempt_errors = self._request_completion_once(
                responses_payload=responses_payload,
                chat_payload=chat_payload,
            )
            if completion is not None:
                if errors or attempt_errors:
                    self._attach_completion_diagnostics(
                        completion,
                        fallback_errors=[*errors, *attempt_errors],
                    )
                return completion
            if attempt_errors:
                errors.extend(f"attempt {attempt + 1}: {error}" for error in attempt_errors)
                last_error = attempt_errors[-1]
            if attempt + 1 < self.config.max_retries:
                time.sleep(min(0.25 * (attempt + 1), 0.5))
        raise LLMProviderError(
            "LLM provider did not return usable text content. " + (last_error if len(errors) == 1 else " | ".join(errors))
        )

    def _request_completion_once(
        self,
        *,
        responses_payload: dict[str, Any],
        chat_payload: dict[str, Any],
    ) -> tuple[LLMCompletion | None, list[str]]:
        errors: list[str] = []
        streaming_response_completion, streaming_response_error = self._request_streaming_responses_completion(
            responses_payload
        )
        if streaming_response_completion is not None:
            return streaming_response_completion, []
        if streaming_response_error:
            errors.append(streaming_response_error)

        non_stream_responses_payload = dict(responses_payload)
        non_stream_responses_payload["stream"] = False
        response_completion, response_error = self._request_responses_completion(non_stream_responses_payload)
        if response_completion is not None:
            return response_completion, errors
        if response_error:
            errors.append(response_error)

        chat_completion, chat_error = self._request_chat_completion(chat_payload)
        if chat_completion is not None:
            return chat_completion, errors
        if chat_error:
            errors.append(chat_error)

        streaming_chat_payload = dict(chat_payload)
        streaming_chat_payload["stream"] = True
        streaming_chat_completion, streaming_chat_error = self._request_streaming_chat_completion(
            streaming_chat_payload
        )
        if streaming_chat_completion is not None:
            return streaming_chat_completion, errors
        if streaming_chat_error:
            errors.append(streaming_chat_error)
        return None, errors

    def _request_responses_completion(self, payload: dict[str, Any]) -> tuple[LLMCompletion | None, str | None]:
        try:
            response = self._client.post("/responses", json=payload)
            response.raise_for_status()
            data = self._decode_payload(response, endpoint="/responses", fallback_model=payload["model"])
        except (httpx.HTTPError, LLMProviderError) as exc:
            return None, str(exc)

        content = self._extract_response_text(data).strip()
        if not content:
            return None, self._missing_text_error(
                endpoint="/responses",
                payload=data,
            )
        return (
            self._attach_completion_diagnostics(
                LLMCompletion(
                    model=data.get("model", payload["model"]),
                    content=content,
                    usage=self._normalize_usage(data.get("usage", {})),
                    raw=data,
                ),
                endpoint="/responses",
            ),
            None,
        )

    def _request_streaming_responses_completion(self, payload: dict[str, Any]) -> tuple[LLMCompletion | None, str | None]:
        try:
            with self._client.stream("POST", "/responses", json=payload) as response:
                response.raise_for_status()
                if not self._is_event_stream(response):
                    data = self._decode_payload(response, endpoint="/responses", fallback_model=payload["model"])
                    content = self._extract_response_text(data).strip()
                    if not content:
                        return None, self._missing_text_error(
                            endpoint="/responses",
                            payload=data,
                        )
                    return (
                        self._attach_completion_diagnostics(
                            LLMCompletion(
                                model=data.get("model", payload["model"]),
                                content=content,
                                usage=self._normalize_usage(data.get("usage", {})),
                                raw=data,
                            ),
                            endpoint="/responses",
                        ),
                        None,
                    )
                content_parts: list[str] = []
                raw_chunks: list[dict[str, Any]] = []
                model = payload["model"]
                usage: dict[str, Any] = {}

                for event in self._iter_sse_events(response):
                    event_name = self._sse_event_name(event)
                    data = self._sse_event_data(event)
                    if not data:
                        continue
                    if data == "[DONE]":
                        continue
                    try:
                        chunk = json.loads(data)
                    except json.JSONDecodeError as exc:
                        raise LLMProviderError(
                            f"/responses stream returned invalid JSON chunk: {data[:240]!r}"
                        ) from exc
                    if not isinstance(chunk, dict):
                        raise LLMProviderError(
                            f"/responses stream returned a non-object chunk: {type(chunk).__name__}"
                        )
                    raw_chunks.append({"event": event_name, "data": chunk})
                    model = self._stream_model(chunk, model)
                    usage = self._stream_usage(chunk, usage)

                    delta, final_text = self._extract_responses_stream_text(event_name, chunk)
                    if delta:
                        content_parts.append(delta)
                        continue
                    if final_text and not content_parts:
                        content_parts.append(final_text)
        except (httpx.HTTPError, LLMProviderError) as exc:
            return None, str(exc)

        content = "".join(content_parts).strip()
        if not content:
            return None, self._missing_text_error(
                endpoint="/responses",
                payload={"status": "stream", "usage": usage},
            )
        return (
            self._attach_completion_diagnostics(
                LLMCompletion(
                    model=model,
                    content=content,
                    usage=usage,
                    raw={"chunks": raw_chunks},
                ),
                endpoint="/responses",
                streamed=True,
            ),
            None,
        )

    def _request_chat_completion(self, payload: dict[str, Any]) -> tuple[LLMCompletion | None, str | None]:
        try:
            response = self._client.post("/chat/completions", json=payload)
            response.raise_for_status()
            data = self._decode_payload(response, endpoint="/chat/completions", fallback_model=payload["model"])
        except (httpx.HTTPError, LLMProviderError) as exc:
            return None, str(exc)

        content = self._extract_chat_completion_text(data).strip() or self._extract_response_text(data).strip()
        if not content:
            return None, self._missing_text_error(
                endpoint="/chat/completions",
                payload=data,
            )
        return (
            self._attach_completion_diagnostics(
                LLMCompletion(
                    model=data.get("model", payload["model"]),
                    content=content,
                    usage=self._normalize_usage(data.get("usage", {})),
                    raw=data,
                ),
                endpoint="/chat/completions",
            ),
            None,
        )

    def _request_streaming_chat_completion(self, payload: dict[str, Any]) -> tuple[LLMCompletion | None, str | None]:
        try:
            with self._client.stream("POST", "/chat/completions", json=payload) as response:
                response.raise_for_status()
                if not self._is_event_stream(response):
                    data = self._decode_payload(response, endpoint="/chat/completions", fallback_model=payload["model"])
                    content = self._extract_chat_completion_text(data).strip() or self._extract_response_text(data).strip()
                    if not content:
                        return None, self._missing_text_error(
                            endpoint="/chat/completions",
                            payload=data,
                        )
                    return (
                        self._attach_completion_diagnostics(
                            LLMCompletion(
                                model=data.get("model", payload["model"]),
                                content=content,
                                usage=self._normalize_usage(data.get("usage", {})),
                                raw=data,
                            ),
                            endpoint="/chat/completions",
                        ),
                        None,
                    )
                content_parts: list[str] = []
                raw_chunks: list[dict[str, Any]] = []
                model = payload["model"]
                usage: dict[str, Any] = {}

                for event in self._iter_sse_events(response):
                    data = self._sse_event_data(event)
                    if not data:
                        continue
                    if data == "[DONE]":
                        continue
                    try:
                        chunk = json.loads(data)
                    except json.JSONDecodeError as exc:
                        raise LLMProviderError(
                            f"/chat/completions stream returned invalid JSON chunk: {data[:240]!r}"
                        ) from exc
                    if not isinstance(chunk, dict):
                        raise LLMProviderError(
                            f"/chat/completions stream returned a non-object chunk: {type(chunk).__name__}"
                        )
                    raw_chunks.append(chunk)
                    model = chunk.get("model", model)
                    normalized_usage = self._normalize_usage(chunk.get("usage", {}))
                    if normalized_usage:
                        usage = normalized_usage
                    delta = self._extract_chat_completion_delta(chunk)
                    if not delta:
                        delta = self._extract_chat_completion_text(chunk)
                    if delta:
                        content_parts.append(delta)
        except (httpx.HTTPError, LLMProviderError) as exc:
            return None, str(exc)

        content = "".join(content_parts).strip()
        if not content:
            return None, self._missing_text_error(
                endpoint="/chat/completions",
                payload={"status": "stream", "usage": usage},
            )
        return (
            self._attach_completion_diagnostics(
                LLMCompletion(
                    model=model,
                    content=content,
                    usage=usage,
                    raw={"chunks": raw_chunks},
                ),
                endpoint="/chat/completions",
                streamed=True,
            ),
            None,
        )

    def _chat_completion_payload(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        model: str,
        temperature: float,
        max_tokens: int | None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": temperature,
            "stream": False,
        }
        if self.config.reasoning_effort:
            payload["reasoning_effort"] = self.config.reasoning_effort
        if max_tokens is not None:
            payload["max_completion_tokens"] = max_tokens
        return payload

    def _responses_payload(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        model: str,
        temperature: float,
        max_tokens: int | None,
        stream: bool,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": model,
            "instructions": system_prompt,
            "input": user_prompt,
            "temperature": temperature,
            "stream": stream,
        }
        if self.config.reasoning_effort:
            payload["reasoning"] = {"effort": self.config.reasoning_effort}
        if max_tokens is not None:
            payload["max_output_tokens"] = max_tokens
        return payload

    def _attach_completion_diagnostics(
        self,
        completion: LLMCompletion,
        *,
        endpoint: str | None = None,
        streamed: bool | None = None,
        fallback_errors: list[str] | None = None,
    ) -> LLMCompletion:
        raw = completion.raw if isinstance(completion.raw, dict) else {}
        diagnostics = raw.setdefault("_codeingme", {})
        if endpoint is not None:
            diagnostics["endpoint"] = endpoint
        if streamed is not None:
            diagnostics["streamed"] = streamed
        if fallback_errors:
            diagnostics["fallback_errors"] = list(fallback_errors)
        completion.raw = raw
        return completion

    def _iter_sse_events(self, response: httpx.Response) -> list[list[str]]:
        events: list[list[str]] = []
        current_event: list[str] = []
        for raw_line in response.iter_lines():
            line = raw_line.strip()
            if not line:
                if current_event:
                    events.append(current_event)
                    current_event = []
                continue
            current_event.append(line)
        if current_event:
            events.append(current_event)
        return events

    def _sse_event_data(self, event: list[str]) -> str:
        data_lines = [line[5:].strip() for line in event if line.startswith("data:")]
        return "\n".join(data_lines).strip()

    def _is_event_stream(self, response: httpx.Response) -> bool:
        return "text/event-stream" in response.headers.get("content-type", "").lower()

    def _sse_event_name(self, event: list[str]) -> str | None:
        for line in event:
            if line.startswith("event:"):
                return line[6:].strip()
        return None

    def _stream_model(self, payload: dict[str, Any], current_model: str) -> str:
        model = payload.get("model")
        if isinstance(model, str) and model:
            return model
        response_payload = payload.get("response")
        if isinstance(response_payload, dict):
            response_model = response_payload.get("model")
            if isinstance(response_model, str) and response_model:
                return response_model
        return current_model

    def _stream_usage(self, payload: dict[str, Any], current_usage: dict[str, Any]) -> dict[str, Any]:
        normalized_usage = self._normalize_usage(payload.get("usage", {}))
        if normalized_usage:
            return normalized_usage
        response_payload = payload.get("response")
        if isinstance(response_payload, dict):
            normalized_response_usage = self._normalize_usage(response_payload.get("usage", {}))
            if normalized_response_usage:
                return normalized_response_usage
        return current_usage

    def _extract_responses_stream_text(self, event_name: str | None, payload: dict[str, Any]) -> tuple[str, str]:
        payload_type = payload.get("type")
        effective_type = event_name or payload_type if isinstance(payload_type, str) else event_name
        if isinstance(effective_type, str) and effective_type.endswith("output_text.delta"):
            delta = payload.get("delta")
            if isinstance(delta, str) and delta:
                return delta, ""
        if isinstance(effective_type, str) and effective_type.endswith("output_text.done"):
            text = payload.get("text")
            if isinstance(text, str) and text:
                return "", text
        response_payload = payload.get("response")
        if isinstance(response_payload, dict):
            return "", self._extract_response_text(response_payload).strip()
        return "", self._extract_response_text(payload).strip()

    def _decode_payload(
        self,
        response: httpx.Response,
        *,
        endpoint: str,
        fallback_model: str,
    ) -> dict[str, Any]:
        body = response.text
        try:
            payload = response.json()
        except json.JSONDecodeError as exc:
            stripped = body.strip()
            content_type = response.headers.get("content-type", "")
            if stripped and "json" not in content_type.lower() and not stripped.startswith("<"):
                return {"model": fallback_model, "output_text": stripped, "usage": {}}
            raise LLMProviderError(
                self._response_error_message(
                    endpoint=endpoint,
                    response=response,
                    detail=f"Invalid JSON body: {exc}",
                    body=body,
                )
            ) from exc
        if not isinstance(payload, dict):
            raise LLMProviderError(
                self._response_error_message(
                    endpoint=endpoint,
                    response=response,
                    detail=f"Expected a JSON object, received {type(payload).__name__}",
                    body=body,
                )
            )
        return payload

    def _extract_chat_completion_text(self, payload: dict[str, Any]) -> str:
        choices = payload.get("choices")
        if isinstance(choices, list) and choices:
            first = choices[0]
            if isinstance(first, dict):
                message = first.get("message")
                if isinstance(message, dict):
                    content = message.get("content")
                    if isinstance(content, list):
                        parts: list[str] = []
                        for item in content:
                            if isinstance(item, dict):
                                text = item.get("text")
                                if isinstance(text, str) and text.strip():
                                    parts.append(text.strip())
                        if parts:
                            return "\n".join(parts)
                    coerced = self._coerce_content(content).strip()
                    if coerced:
                        return coerced
                text = first.get("text")
                if isinstance(text, str) and text.strip():
                    return text.strip()
        return ""

    def _extract_chat_completion_delta(self, payload: dict[str, Any]) -> str:
        choices = payload.get("choices")
        if isinstance(choices, list) and choices:
            first = choices[0]
            if isinstance(first, dict):
                delta = first.get("delta")
                if isinstance(delta, dict):
                    return self._coerce_content(delta.get("content"))
        return ""

    def _missing_text_error(self, *, endpoint: str, payload: dict[str, Any]) -> str:
        usage = payload.get("usage")
        usage_fragment = ""
        if isinstance(usage, dict):
            output_tokens = usage.get("output_tokens") or usage.get("completion_tokens")
            total_tokens = usage.get("total_tokens")
            if output_tokens is not None or total_tokens is not None:
                usage_fragment = (
                    " usage="
                    + json.dumps(
                        {"output_tokens": output_tokens, "total_tokens": total_tokens},
                        ensure_ascii=True,
                    )
                )
        return (
            f"{endpoint} returned HTTP 200 JSON without usable text content."
            f" status={payload.get('status')!r}{usage_fragment}"
        )

    def _response_error_message(
        self,
        *,
        endpoint: str,
        response: httpx.Response,
        detail: str,
        body: str,
    ) -> str:
        preview = body.strip().replace("\n", "\\n")
        if len(preview) > 240:
            preview = preview[:240] + "..."
        return (
            f"{endpoint} returned an unusable response: {detail}. "
            f"status={response.status_code} content_type={response.headers.get('content-type', '')!r} "
            f"body_preview={preview!r}"
        )

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
        if content is None:
            return ""
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts: list[str] = []
            for item in content:
                if item is None:
                    continue
                if isinstance(item, dict) and "text" in item:
                    text = item["text"]
                    if text is not None:
                        parts.append(str(text))
                else:
                    parts.append(str(item))
            return "\n".join(parts)
        return str(content)

    def _extract_response_text(self, payload: dict[str, Any]) -> str:
        output_text = payload.get("output_text")
        if isinstance(output_text, str) and output_text:
            return output_text

        output = payload.get("output")
        if isinstance(output, list):
            parts: list[str] = []
            for item in output:
                if not isinstance(item, dict):
                    continue
                content = item.get("content")
                if isinstance(content, list):
                    text = self._coerce_content(content).strip()
                    if text:
                        parts.append(text)
            if parts:
                return "\n".join(parts)

        choices = payload.get("choices")
        if isinstance(choices, list) and choices:
            first = choices[0]
            if isinstance(first, dict):
                message = first.get("message")
                if isinstance(message, dict):
                    return self._coerce_content(message.get("content"))

        return ""

    def _normalize_usage(self, usage: Any) -> dict[str, Any]:
        if not isinstance(usage, dict):
            return {}

        normalized = copy.deepcopy(usage)
        if "input_tokens" in normalized and "prompt_tokens" not in normalized:
            normalized["prompt_tokens"] = normalized["input_tokens"]
        if "output_tokens" in normalized and "completion_tokens" not in normalized:
            normalized["completion_tokens"] = normalized["output_tokens"]
        if "total_tokens" not in normalized:
            prompt_tokens = normalized.get("prompt_tokens")
            completion_tokens = normalized.get("completion_tokens")
            if isinstance(prompt_tokens, int) and isinstance(completion_tokens, int):
                normalized["total_tokens"] = prompt_tokens + completion_tokens
        return normalized


def _normalized_env_value(name: str) -> str | None:
    value = os.getenv(name)
    if value is None:
        return None
    normalized = value.strip()
    return normalized or None
