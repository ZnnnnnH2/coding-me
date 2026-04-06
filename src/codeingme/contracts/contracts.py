from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class RequirementSpec:
    title: str
    summary: str
    acceptance_criteria: list[str]


@dataclass(slots=True)
class DataSchema:
    name: str
    fields: dict[str, str]


@dataclass(slots=True)
class APISpec:
    route: str
    method: str
    summary: str
    request_schema: str | None = None
    response_schema: str | None = None


@dataclass(slots=True)
class TestSpec:
    name: str
    description: str
    expected_state: str
    path: str | None = None
