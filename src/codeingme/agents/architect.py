"""实现架构代理，用于生成初始合同。"""

from __future__ import annotations

from dataclasses import dataclass, field
import re

from codeingme.agents.base import AgentContext, AgentResult, BaseAgent
from codeingme.contracts import APISpec, DataSchema


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

        payload, llm_artifacts = self._llm_json_object(
            context,
            system_prompt=(
                "You are the architect agent inside a state-machine-driven backend module generator. "
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
            validator=self._validate_contract_payload,
        )
        if payload is None:
            raise RuntimeError(
                self._llm_generation_failure_message(
                    output_kind="contract response",
                    metadata=llm_artifacts,
                )
            )

        plan = self._plan_from_payload(payload)
        if plan is None:
            raise RuntimeError(
                self._llm_generation_failure_message(
                    output_kind="contract response",
                    metadata={
                        **llm_artifacts,
                        "llm_error": "Generated contract response failed validation",
                    },
                )
            )

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

    def _validate_contract_payload(self, payload: dict[str, object]) -> str | None:
        return None if self._plan_from_payload(payload) is not None else "Generated contract response failed validation"

    def _plan_from_payload(self, payload: dict[str, object]) -> _BootstrapPlan | None:
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

        summary = str(primary_api_data.get("summary", "")).strip() or f"GET {route}"
        design_note = str(payload.get("design_note", "")).strip() or str(payload.get("summary", "")).strip()
        if not design_note:
            design_note = f"Use {schema_name} as the primary contract for {route}."

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
        required_fields = {
            "id": "int",
            "title": "str",
            "completed": "bool",
        }
        for key, value in required_fields.items():
            if normalized.get(key) != value:
                return None
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

    def _field_summary(self, schema: DataSchema) -> str:
        return ", ".join(f"{key}:{value}" for key, value in schema.fields.items())
