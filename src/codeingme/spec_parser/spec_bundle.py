"""负责读取并汇总规格包内容。"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import re


@dataclass(slots=True)
class SpecificationBundle:
    spec_dir: Path
    service_name: str
    summary: str
    openapi_path: Path | None = None
    schema_path: Path | None = None
    rules_path: Path | None = None
    user_story_path: Path | None = None
    endpoints: list[str] = field(default_factory=list)
    tables: list[str] = field(default_factory=list)
    rules: list[str] = field(default_factory=list)

    def requirement_prompt(self) -> str:
        descriptor = self.service_name.replace("_", " ")
        detail_parts: list[str] = []
        if self.summary:
            detail_parts.append(self.summary)
        if self.endpoints:
            detail_parts.append(f"APIs: {', '.join(self.endpoints)}")
        if self.tables:
            detail_parts.append(f"Tables: {', '.join(self.tables)}")
        if self.rules:
            detail_parts.append(f"Rules: {', '.join(self.rules[:3])}")
        details = "; ".join(detail_parts)
        if details:
            return (
                f"Build a {descriptor} backend module with explicit contract coverage. "
                f"{details}"
            )
        return f"Build a {descriptor} backend module with explicit contract coverage"

    def to_dict(self) -> dict[str, object]:
        return {
            "spec_dir": str(self.spec_dir),
            "service_name": self.service_name,
            "summary": self.summary,
            "openapi_path": str(self.openapi_path) if self.openapi_path is not None else None,
            "schema_path": str(self.schema_path) if self.schema_path is not None else None,
            "rules_path": str(self.rules_path) if self.rules_path is not None else None,
            "user_story_path": str(self.user_story_path) if self.user_story_path is not None else None,
            "endpoints": self.endpoints,
            "tables": self.tables,
            "rules": self.rules,
        }


def load_spec_bundle(spec_dir: str | Path) -> SpecificationBundle:
    spec_path = Path(spec_dir).expanduser().resolve()
    if not spec_path.exists() or not spec_path.is_dir():
        raise FileNotFoundError(f"Specification directory not found: {spec_dir}")

    openapi_path = _first_existing(spec_path, ["openapi.yaml", "openapi.yml", "openapi.json"])
    schema_path = _first_existing(spec_path, ["schema.sql"])
    rules_path = _first_existing(spec_path, ["business_rules.yaml", "business_rules.yml", "rules.yaml"])
    user_story_path = _first_existing(spec_path, ["user_story.md", "README.md"])

    if openapi_path is None and schema_path is None and rules_path is None and user_story_path is None:
        raise FileNotFoundError(
            f"No supported specification files found in {spec_path}. "
            "Expected one of openapi.yaml, schema.sql, business_rules.yaml, or user_story.md."
        )

    openapi_text = _read_text(openapi_path)
    schema_text = _read_text(schema_path)
    rules_text = _read_text(rules_path)
    story_text = _read_text(user_story_path)

    service_name = _normalize_service_name(_extract_title(openapi_text) or spec_path.name)
    summary = _extract_summary(openapi_text, story_text)
    endpoints = _extract_endpoints(openapi_text)
    tables = _extract_tables(schema_text)
    rules = _extract_rules(rules_text)

    return SpecificationBundle(
        spec_dir=spec_path,
        service_name=service_name,
        summary=summary,
        openapi_path=openapi_path,
        schema_path=schema_path,
        rules_path=rules_path,
        user_story_path=user_story_path,
        endpoints=endpoints,
        tables=tables,
        rules=rules,
    )


def _first_existing(root: Path, names: list[str]) -> Path | None:
    for name in names:
        candidate = root / name
        if candidate.exists():
            return candidate
    return None


def _read_text(path: Path | None) -> str:
    if path is None:
        return ""
    return path.read_text(encoding="utf-8")


def _extract_title(openapi_text: str) -> str | None:
    match = re.search(r"(?mi)^\s*title:\s*(.+?)\s*$", openapi_text)
    return match.group(1).strip() if match else None


def _extract_summary(openapi_text: str, story_text: str) -> str:
    description_match = re.search(r"(?mi)^\s*description:\s*(.+?)\s*$", openapi_text)
    if description_match:
        return description_match.group(1).strip()
    for line in story_text.splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            return stripped
    return ""


def _extract_endpoints(openapi_text: str) -> list[str]:
    return [
        match.group(1).strip()
        for match in re.finditer(r"(?m)^\s{2}(/[^:]+):\s*$", openapi_text)
    ]


def _extract_tables(schema_text: str) -> list[str]:
    return [
        _strip_quotes(match.group(1))
        for match in re.finditer(
            r"(?im)\bcreate\s+table\s+(?:if\s+not\s+exists\s+)?([\"`]?[\w]+[\"`]?)",
            schema_text,
        )
    ]


def _extract_rules(rules_text: str) -> list[str]:
    rules: list[str] = []
    for line in rules_text.splitlines():
        stripped = line.strip()
        if stripped.startswith("- "):
            rules.append(stripped[2:].strip())
    return rules


def _normalize_service_name(value: str) -> str:
    collapsed = re.sub(r"[^a-zA-Z0-9]+", "_", value).strip("_").lower()
    collapsed = re.sub(r"_api$", "", collapsed)
    return collapsed or "service"


def _strip_quotes(value: str) -> str:
    return value.strip('`"')
