from __future__ import annotations

from dataclasses import dataclass
import re

from codeingme.agents.base import AgentContext


@dataclass(slots=True)
class GenerationPlan:
    schema_name: str
    singular_slug: str
    plural_slug: str
    response_key: str
    backend_module_path: str
    backend_package_init_path: str
    test_module_path: str


def build_generation_plan(context: AgentContext) -> GenerationPlan:
    schema_name = _primary_schema_name(context)
    singular_words = _split_identifier(schema_name)
    plural_words = _pluralize_words(singular_words)
    singular_slug = "_".join(singular_words) or "item"
    plural_slug = "_".join(plural_words) or "items"
    return GenerationPlan(
        schema_name=schema_name,
        singular_slug=singular_slug,
        plural_slug=plural_slug,
        response_key=plural_slug,
        backend_module_path=f"demo_app/{plural_slug}_api.py",
        backend_package_init_path="demo_app/__init__.py",
        test_module_path=f"tests_generated/test_{plural_slug}_demo.py",
    )


def _primary_schema_name(context: AgentContext) -> str:
    if context.schemas:
        return context.schemas[0].name
    return "Task"


def _split_identifier(value: str) -> list[str]:
    tokens = re.findall(r"[A-Z]?[a-z0-9]+", value)
    normalized = [token.lower() for token in tokens if token]
    return normalized or ["task"]


def _pluralize_words(words: list[str]) -> list[str]:
    if not words:
        return ["tasks"]
    plural = list(words)
    last = plural[-1]
    if last.endswith("ies") or last.endswith("s"):
        return plural
    if last.endswith("y") and len(last) > 1:
        plural[-1] = last[:-1] + "ies"
    else:
        plural[-1] = last + "s"
    return plural
