from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
import json
import re
from typing import Any

from codeingme.contracts import APISpec, DataSchema, RequirementSpec, TestSpec
from codeingme.graph import GraphSlice
from codeingme.llm import LLMCompletion, RelayLLMClient
from codeingme.runtime.patches import FilePatchPlan


@dataclass(slots=True)
class AgentContext:
    requirement: RequirementSpec
    graph_slice: GraphSlice
    apis: list[APISpec] = field(default_factory=list)
    schemas: list[DataSchema] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)
    llm_client: RelayLLMClient | None = None


@dataclass(slots=True)
class AgentResult:
    role: str
    summary: str
    artifacts: dict[str, object] = field(default_factory=dict)
    tests: list[TestSpec] = field(default_factory=list)
    emitted_nodes: list[str] = field(default_factory=list)
    file_plan: FilePatchPlan | None = None


@dataclass(slots=True)
class GeneratedFileArtifact:
    path: str
    content: str
    language: str | None = None


@dataclass(slots=True)
class StructuredGenerationBundle:
    summary: str | None = None
    files: list[GeneratedFileArtifact] = field(default_factory=list)
    collections: dict[str, list[str]] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict)


class BaseAgent:
    role = "base"

    def run(self, context: AgentContext) -> AgentResult:
        raise NotImplementedError

    def _llm_completion(
        self,
        context: AgentContext,
        *,
        system_prompt: str,
        user_prompt: str,
        max_tokens: int,
    ) -> tuple[LLMCompletion | None, dict[str, str]]:
        if context.llm_client is None:
            return None, {}
        try:
            completion = context.llm_client.prompt(
                system_prompt,
                user_prompt,
                max_tokens=max_tokens,
            )
        except Exception as exc:
            return None, {"llm_error": str(exc), "llm_fallback": "true"}
        return completion, {
            "llm_model": completion.model,
            "llm_fallback": "false",
            "llm_cached": "true" if completion.cached else "false",
        }

    def _llm_structured_files(
        self,
        context: AgentContext,
        *,
        system_prompt: str,
        user_prompt: str,
        max_tokens: int,
        required_files: dict[str, str],
        collection_fields: list[str],
        validator: Callable[[StructuredGenerationBundle], bool] | None = None,
    ) -> tuple[StructuredGenerationBundle | None, dict[str, str]]:
        completion, metadata = self._llm_completion(
            context,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            max_tokens=max_tokens,
        )
        if completion is None:
            return None, metadata

        payload = self._extract_json_object(completion.content)
        if payload is None:
            metadata["llm_error"] = "Model did not return a valid JSON object"
            metadata["llm_fallback"] = "true"
            return None, metadata

        files_data = payload.get("files")
        if not isinstance(files_data, list) or not files_data:
            metadata["llm_error"] = "Model JSON response is missing a non-empty files list"
            metadata["llm_fallback"] = "true"
            return None, metadata

        files: list[GeneratedFileArtifact] = []
        for item in files_data:
            if not isinstance(item, dict):
                metadata["llm_error"] = "Model JSON files list must contain objects"
                metadata["llm_fallback"] = "true"
                return None, metadata

            path = item.get("path")
            raw_content = item.get("content")
            language = item.get("language")
            if not isinstance(path, str) or not path.strip():
                metadata["llm_error"] = "Generated file entry is missing a valid path"
                metadata["llm_fallback"] = "true"
                return None, metadata
            if not isinstance(raw_content, str) or not raw_content.strip():
                metadata["llm_error"] = "Generated file entry is missing non-empty content"
                metadata["llm_fallback"] = "true"
                return None, metadata

            normalized_path = path.strip()
            expected_language = required_files.get(normalized_path)
            normalized_language = language.strip() if isinstance(language, str) and language.strip() else None
            content = self._extract_code_block(raw_content, language=normalized_language or expected_language)
            if not content.strip():
                metadata["llm_error"] = "Generated file content was empty after extraction"
                metadata["llm_fallback"] = "true"
                return None, metadata
            files.append(
                GeneratedFileArtifact(
                    path=normalized_path,
                    content=content,
                    language=normalized_language or expected_language,
                )
            )

        missing = sorted(path for path in required_files if self._file_from_list(files, path) is None)
        if missing:
            metadata["llm_error"] = f"Model JSON response is missing required files: {', '.join(missing)}"
            metadata["llm_fallback"] = "true"
            return None, metadata

        collections: dict[str, list[str]] = {}
        for field_name in collection_fields:
            raw_items = payload.get(field_name, [])
            if raw_items is None:
                raw_items = []
            if not isinstance(raw_items, list) or any(not isinstance(item, str) for item in raw_items):
                metadata["llm_error"] = f"Model JSON field {field_name} must be a list of strings"
                metadata["llm_fallback"] = "true"
                return None, metadata
            collections[field_name] = [item.strip() for item in raw_items if item.strip()]

        bundle = StructuredGenerationBundle(
            summary=payload.get("summary") if isinstance(payload.get("summary"), str) else None,
            files=files,
            collections=collections,
            raw=payload,
        )
        if validator is not None and not validator(bundle):
            metadata["llm_error"] = "Generated structured response failed validation"
            metadata["llm_fallback"] = "true"
            return None, metadata

        if bundle.summary and bundle.summary.strip():
            metadata["llm_summary"] = bundle.summary.strip()
        metadata["llm_response_format"] = "json-files"
        return bundle, metadata

    @staticmethod
    def _file_from_list(files: list[GeneratedFileArtifact], path: str) -> GeneratedFileArtifact | None:
        for file in files:
            if file.path == path:
                return file
        return None

    def _file_content(self, bundle: StructuredGenerationBundle, path: str) -> str | None:
        file = self._file_from_list(bundle.files, path)
        return file.content if file is not None else None

    @staticmethod
    def _join_items(items: list[str]) -> str:
        return ", ".join(items)

    @staticmethod
    def _is_python_file(path: str, content: str) -> bool:
        return path.endswith(".py") and bool(content.strip())

    @staticmethod
    def _is_html_file(path: str, content: str) -> bool:
        return path.endswith(".html") and bool(content.strip())

    @staticmethod
    def _has_items(items: list[str], required: list[str]) -> bool:
        return all(item in items for item in required)

    @staticmethod
    def _extract_code_block(content: str, language: str | None = None) -> str:
        matches = list(
            re.finditer(r"```(?P<lang>[^\n`]*)\n(?P<body>.*?)```", content, re.DOTALL)
        )
        if not matches:
            return content.strip()
        normalized_language = language.lower() if language is not None else None
        for match in matches:
            block_language = match.group("lang").strip().lower()
            if normalized_language is None or block_language == normalized_language:
                return match.group("body").strip()
        return matches[0].group("body").strip()

    @classmethod
    def _extract_json_object(cls, content: str) -> dict[str, Any] | None:
        candidates = [content.strip()]
        json_block = cls._extract_code_block(content, language="json")
        if json_block and json_block not in candidates:
            candidates.append(json_block)

        stripped = content.strip()
        start = stripped.find("{")
        end = stripped.rfind("}")
        if start != -1 and end != -1 and end > start:
            inline_object = stripped[start : end + 1]
            if inline_object not in candidates:
                candidates.append(inline_object)

        for candidate in candidates:
            try:
                payload = json.loads(candidate)
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict):
                return payload
        return None
