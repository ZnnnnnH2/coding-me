from __future__ import annotations

from dataclasses import dataclass, field
import re

from codeingme.agents.base import AgentContext, AgentResult, BaseAgent
from codeingme.contracts import APISpec, DataSchema


_DEFAULT_FIELDS = {
    "id": "int",
    "title": "str",
    "completed": "bool",
}
_GENERIC_WORDS = {
    "a",
    "an",
    "and",
    "app",
    "application",
    "board",
    "build",
    "completion",
    "create",
    "dashboard",
    "for",
    "list",
    "listing",
    "of",
    "page",
    "show",
    "showing",
    "state",
    "status",
    "system",
    "the",
    "to",
    "used",
    "validate",
    "web",
    "with",
}
_PLURAL_NOUNS = {
    "items",
    "jobs",
    "orders",
    "requests",
    "shipments",
    "tasks",
    "tickets",
}
_SINGULAR_NOUNS = {noun[:-1] for noun in _PLURAL_NOUNS}


@dataclass(slots=True)
class _BootstrapPlan:
    schemas: list[DataSchema]
    apis: list[APISpec]
    design_note: str
    artifacts: dict[str, object] = field(default_factory=dict)


class ArchitectAgent(BaseAgent):
    role = "architect"

    def __init__(self) -> None:
        self._bootstrap_cache: dict[str, _BootstrapPlan] = {}

    def run(self, context: AgentContext) -> AgentResult:
        plan = self._bootstrap_plan(context)
        primary_schema = plan.schemas[0]
        primary_api = plan.apis[0]
        artifacts: dict[str, object] = {
            "openapi": f"{primary_api.method} {primary_api.route}",
            "schema": f"{primary_schema.name}: {self._field_summary(primary_schema)}",
            "design_note": plan.design_note,
        }
        artifacts.update(plan.artifacts)
        if artifacts.get("generation_mode") == "llm":
            artifacts["llm_design_note"] = plan.design_note

        return AgentResult(
            role=self.role,
            summary="Created initial architecture contract",
            artifacts=artifacts,
            emitted_nodes=[primary_schema.name, f"{primary_api.method} {primary_api.route}"],
        )

    def bootstrap_specs(self, context: AgentContext) -> tuple[list[DataSchema], list[APISpec]]:
        plan = self._bootstrap_plan(context)
        return plan.schemas, plan.apis

    def _bootstrap_plan(self, context: AgentContext) -> _BootstrapPlan:
        cache_key = self._bootstrap_cache_key(context)
        cached = self._bootstrap_cache.get(cache_key)
        if cached is not None:
            return cached

        fallback = self._heuristic_bootstrap_plan(context)
        if context.llm_client is None:
            self._bootstrap_cache[cache_key] = fallback
            return fallback

        completion, llm_artifacts = self._llm_completion(
            context,
            system_prompt=(
                "You are the architect agent inside a state-machine-driven web app generator. "
                "Return a JSON object with keys summary, design_note, schemas, apis, and risks."
            ),
            user_prompt=(
                f"Requirement: {context.requirement.summary}\n"
                f"Acceptance criteria: {', '.join(context.requirement.acceptance_criteria)}\n"
                "Response format:\n"
                "- Return JSON only.\n"
                "- Include schemas as a non-empty list with one primary object using keys name and fields.\n"
                "- The schema fields must include id:int, title:str, and completed:bool.\n"
                "- Include apis as a non-empty list with one primary object using keys route, method, summary, and response_schema.\n"
                "- The primary API method must be GET.\n"
                "- The primary API route must be concise, requirement-specific, and live under /api/.\n"
                "- The response_schema value must match the primary schema name.\n"
                "- design_note should briefly explain how the contract maps to the requirement.\n"
                "- Do not include any prose outside the JSON object."
            ),
            max_tokens=700,
        )
        if completion is None:
            fallback.artifacts.update(llm_artifacts)
            self._bootstrap_cache[cache_key] = fallback
            return fallback

        payload = self._extract_json_object(completion.content)
        if payload is None:
            fallback.artifacts.update(llm_artifacts)
            fallback.artifacts["llm_error"] = "Model did not return a valid JSON object"
            fallback.artifacts["llm_fallback"] = "true"
            self._bootstrap_cache[cache_key] = fallback
            return fallback

        plan = self._plan_from_payload(payload, fallback)
        if plan is None:
            fallback.artifacts.update(llm_artifacts)
            fallback.artifacts["llm_error"] = "Generated contract response failed validation"
            fallback.artifacts["llm_fallback"] = "true"
            self._bootstrap_cache[cache_key] = fallback
            return fallback

        plan.artifacts.update(llm_artifacts)
        plan.artifacts["generation_mode"] = "llm"
        plan.artifacts["llm_response_format"] = "json-contracts"
        self._bootstrap_cache[cache_key] = plan
        return plan

    def _bootstrap_cache_key(self, context: AgentContext) -> str:
        criteria = "|".join(context.requirement.acceptance_criteria)
        if context.llm_client is None:
            llm_marker = "no-llm"
        else:
            model = getattr(getattr(context.llm_client, "config", None), "model", context.llm_client.__class__.__name__)
            llm_marker = f"llm:{model}:{id(context.llm_client)}"
        return f"{llm_marker}|{context.requirement.title}|{context.requirement.summary}|{criteria}"

    def _plan_from_payload(self, payload: dict[str, object], fallback: _BootstrapPlan) -> _BootstrapPlan | None:
        schemas_data = payload.get("schemas")
        apis_data = payload.get("apis")
        if not isinstance(schemas_data, list) or not schemas_data:
            return None
        if not isinstance(apis_data, list) or not apis_data:
            return None

        primary_schema_data = schemas_data[0]
        primary_api_data = apis_data[0]
        if not isinstance(primary_schema_data, dict) or not isinstance(primary_api_data, dict):
            return None

        schema_name = self._normalize_schema_name(primary_schema_data.get("name"))
        if schema_name is None:
            return None

        raw_fields = primary_schema_data.get("fields")
        if not isinstance(raw_fields, dict):
            return None
        fields = self._normalize_fields(raw_fields)
        if fields is None:
            return None

        route = self._normalize_route(primary_api_data.get("route"))
        if route is None:
            return None
        method = str(primary_api_data.get("method", "GET")).strip().upper()
        if method != "GET":
            return None

        response_schema_name = self._normalize_schema_name(primary_api_data.get("response_schema")) or schema_name
        if response_schema_name != schema_name:
            response_schema_name = schema_name

        summary = str(primary_api_data.get("summary", "")).strip() or fallback.apis[0].summary
        design_note = str(payload.get("design_note", "")).strip() or str(payload.get("summary", "")).strip()
        if not design_note:
            design_note = fallback.design_note

        return _BootstrapPlan(
            schemas=[DataSchema(name=schema_name, fields=fields)],
            apis=[
                APISpec(
                    route=route,
                    method=method,
                    summary=summary,
                    response_schema=response_schema_name,
                )
            ],
            design_note=design_note,
            artifacts={"generation_mode": "llm"},
        )

    def _heuristic_bootstrap_plan(self, context: AgentContext) -> _BootstrapPlan:
        entity_words, singular_noun, plural_noun = self._primary_entity(context.requirement.summary)
        schema_name = self._pascal_case([*entity_words, singular_noun])
        route = "/api/" + self._kebab_case([*entity_words, plural_noun])
        label = " ".join([*entity_words, plural_noun]).strip()
        summary = f"List {label}" if label else f"List {plural_noun}"
        design_note = (
            f"Use {schema_name} as the primary contract and expose GET {route} "
            "so downstream agents stay aligned on a single list-oriented resource."
        )
        return _BootstrapPlan(
            schemas=[DataSchema(name=schema_name, fields=dict(_DEFAULT_FIELDS))],
            apis=[
                APISpec(
                    route=route,
                    method="GET",
                    summary=summary,
                    response_schema=schema_name,
                )
            ],
            design_note=design_note,
            artifacts={"generation_mode": "heuristic"},
        )

    def _primary_entity(self, requirement_text: str) -> tuple[list[str], str, str]:
        tokens = [token for token in re.findall(r"[A-Za-z]+", requirement_text.lower()) if token]
        filtered = [token for token in tokens if token not in _GENERIC_WORDS]
        if not filtered:
            return [], "task", "tasks"

        noun_index = next(
            (
                index
                for index, token in enumerate(filtered)
                if token in _PLURAL_NOUNS or token in _SINGULAR_NOUNS
            ),
            len(filtered) - 1,
        )
        noun = filtered[noun_index]
        singular_noun = self._singularize(noun)
        plural_noun = self._pluralize(noun)
        prefixes = [token for token in filtered[:noun_index] if token not in _SINGULAR_NOUNS and token not in _PLURAL_NOUNS]
        entity_words = prefixes[-2:]
        return entity_words, singular_noun, plural_noun

    def _normalize_schema_name(self, value: object) -> str | None:
        if not isinstance(value, str):
            return None
        tokens = re.findall(r"[A-Za-z0-9]+", value)
        if not tokens:
            return None
        return "".join(token[:1].upper() + token[1:] for token in tokens)

    def _normalize_fields(self, fields: dict[object, object]) -> dict[str, str] | None:
        normalized: dict[str, str] = {}
        for key, value in fields.items():
            if not isinstance(key, str) or not isinstance(value, str):
                return None
            field_name = key.strip().lower()
            field_type = value.strip().lower()
            if not field_name or not field_type:
                return None
            normalized[field_name] = field_type
        for key, value in _DEFAULT_FIELDS.items():
            normalized[key] = value
        return normalized

    def _normalize_route(self, value: object) -> str | None:
        if not isinstance(value, str):
            return None
        route = value.strip().lower().replace("_", "-")
        route = re.sub(r"[^a-z0-9/-]+", "-", route)
        route = re.sub(r"-{2,}", "-", route)
        if not route.startswith("/"):
            route = f"/{route}"
        if not route.startswith("/api/"):
            return None
        return route.rstrip("/") or "/api"

    def _singularize(self, noun: str) -> str:
        if noun.endswith("ies") and len(noun) > 3:
            return noun[:-3] + "y"
        if noun.endswith("s") and len(noun) > 1:
            return noun[:-1]
        return noun

    def _pluralize(self, noun: str) -> str:
        if noun.endswith("y") and len(noun) > 1:
            return noun[:-1] + "ies"
        if noun.endswith("s"):
            return noun
        return noun + "s"

    def _pascal_case(self, parts: list[str]) -> str:
        cleaned = [part for part in parts if part]
        if not cleaned:
            return "Task"
        return "".join(part[:1].upper() + part[1:] for part in cleaned)

    def _kebab_case(self, parts: list[str]) -> str:
        cleaned = [part for part in parts if part]
        if not cleaned:
            return "tasks"
        return "-".join(cleaned)

    def _field_summary(self, schema: DataSchema) -> str:
        return ", ".join(f"{key}:{value}" for key, value in schema.fields.items())
