from __future__ import annotations

import ast

from codeingme.agents.base import AgentContext, AgentResult, BaseAgent, StructuredGenerationBundle
from codeingme.contracts import TestSpec
from codeingme.runtime import FilePatch, FilePatchPlan


class QAAgent(BaseAgent):
    role = "qa"

    def run(self, context: AgentContext, tests: list[TestSpec] | None = None) -> AgentResult:
        resolved_tests = tests or []
        test_path = resolved_tests[0].path if resolved_tests else "tests_generated/test_tasks_demo.py"
        api_route = self._api_route(context)
        test_source = self._default_test_source(context)
        artifacts: dict[str, object] = {"test_file": test_path, "generation_mode": "template"}
        bundle, llm_artifacts = self._llm_structured_files(
            context,
            system_prompt=(
                "You are the QA agent in a state-machine-driven app generator. "
                "Return a JSON object with keys summary, files, tests, and risks. "
                "The files field must be a list of generated files."
            ),
            user_prompt=(
                f"Requirement: {context.requirement.summary}\n"
                f"APIs: {self._api_summary(context)}\n"
                f"Schemas: {self._schema_summary(context)}\n"
                "Response format:\n"
                "- Return JSON only.\n"
                f'- Include a files array with one object for path "{test_path}".\n'
                '- Each file object must use keys path, language, and content.\n'
                '- The content value may be a plain Python string or a ```python fenced block.\n'
                '- Include tests as a list of test function names.\n'
                '- Include risks as a list of short risk notes.\n'
                "Constraints:\n"
                "- Import app from demo_app.tasks_api.\n"
                "- Exercise the ASGI app in-process without real network I/O.\n"
                f"- Add one contract test for GET {api_route}.\n"
                "- Add one end-to-end test for GET / that checks rendered task content.\n"
                "- Use meaningful pytest test names and return them in the tests list.\n"
                f"- Treat GET {api_route} as returning an object with a top-level tasks list, not a bare array.\n"
                "- Do not hardcode example task titles; derive UI assertions from the API response.\n"
                "- Keep the tests valid for any non-empty tasks list that uses id, title, and completed fields.\n"
                "- Do not include any prose outside the JSON object."
            ),
            max_tokens=1000,
            required_files={test_path: "python"},
            collection_fields=["tests", "risks"],
            validator=lambda bundle: self._is_valid_test_bundle(bundle, test_path, api_route=api_route),
        )
        artifacts.update(llm_artifacts)
        if bundle is not None:
            llm_source = self._file_content(bundle, test_path)
            if llm_source is not None:
                test_source = llm_source
            artifacts["tests"] = bundle.collections["tests"] or self._infer_test_names(llm_source or "")
            artifacts["risks"] = bundle.collections["risks"]
            artifacts["generation_mode"] = "llm"

        return AgentResult(
            role=self.role,
            summary="Defined acceptance checks for generated contracts",
            artifacts=artifacts,
            tests=resolved_tests,
            file_plan=FilePatchPlan(
                name="qa_red_tests",
                patches=[FilePatch(path=test_path, content=test_source)],
            ),
        )

    def _default_test_source(self, context: AgentContext) -> str:
        api_route = self._api_route(context)
        return f"""from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any

WORKSPACE_ROOT = Path(__file__).resolve().parents[1]
if str(WORKSPACE_ROOT) not in sys.path:
    sys.path.insert(0, str(WORKSPACE_ROOT))

from demo_app.tasks_api import app


async def _request(path: str) -> tuple[int, str]:
    body_parts: list[bytes] = []
    status_code = 500
    request_sent = False

    async def receive() -> dict[str, object]:
        nonlocal request_sent
        if request_sent:
            return {{"type": "http.disconnect"}}
        request_sent = True
        return {{"type": "http.request", "body": b"", "more_body": False}}

    async def send(message: dict[str, object]) -> None:
        nonlocal status_code
        if message["type"] == "http.response.start":
            status_code = int(message["status"])
        elif message["type"] == "http.response.body":
            body_parts.append(bytes(message.get("body", b"")))

    await app(
        {{
            "type": "http",
            "asgi": {{"version": "3.0"}},
            "http_version": "1.1",
            "method": "GET",
            "scheme": "http",
            "path": path,
            "raw_path": path.encode("utf-8"),
            "query_string": b"",
            "headers": [],
            "client": ("testclient", 50000),
            "server": ("testserver", 80),
            "root_path": "",
        }},
        receive,
        send,
    )
    return status_code, b"".join(body_parts).decode("utf-8")


def _get_json(path: str) -> tuple[int, dict[str, Any]]:
    status_code, body = asyncio.run(_request(path))
    return status_code, json.loads(body)


def _get_text(path: str) -> tuple[int, str]:
    return asyncio.run(_request(path))


def test_tasks_contract() -> None:
    status_code, payload = _get_json("{api_route}")

    assert status_code == 200
    assert "tasks" in payload
    assert isinstance(payload["tasks"], list)
    assert payload["tasks"]
    first_task = payload["tasks"][0]
    assert isinstance(first_task["id"], int)
    assert isinstance(first_task["title"], str)
    assert first_task["title"]
    assert isinstance(first_task["completed"], bool)


def test_tasks_e2e() -> None:
    api_status, payload = _get_json("{api_route}")
    tasks = payload["tasks"]
    ui_status, html = _get_text("/")

    assert api_status == 200
    assert ui_status == 200
    for task in tasks:
        assert task["title"] in html
"""

    def _schema_summary(self, context: AgentContext) -> str:
        if not context.schemas:
            return "none"
        return "; ".join(
            f"{schema.name}({', '.join(f'{key}:{value}' for key, value in schema.fields.items())})"
            for schema in context.schemas
        )

    def _api_summary(self, context: AgentContext) -> str:
        if not context.apis:
            return "none"
        return "; ".join(f"{api.method} {api.route}" for api in context.apis)

    def _is_valid_test_bundle(self, bundle: StructuredGenerationBundle, test_path: str, *, api_route: str) -> bool:
        content = self._file_content(bundle, test_path)
        if content is None or not self._is_python_file(test_path, content):
            return False
        try:
            module = ast.parse(content)
        except SyntaxError:
            return False
        test_names = self._infer_test_names(content)
        if len(test_names) < 2:
            return False
        if not self._imports_app(module):
            return False
        if not (self._imports_test_client(module) or self._has_app_request_helper(module, content)):
            return False
        if not (self._creates_test_client(module) or self._has_app_request_helper(module, content)):
            return False
        test_functions = [node for node in module.body if isinstance(node, ast.FunctionDef) and node.name.startswith("test_")]
        has_contract = any(self._is_contract_test(node, content, api_route=api_route) for node in test_functions)
        has_e2e = any(self._is_e2e_test(node, content, api_route=api_route) for node in test_functions)
        if not (has_contract and has_e2e):
            return False
        listed_tests = bundle.collections.get("tests", [])
        return not listed_tests or set(listed_tests).issubset(set(test_names))

    def _imports_test_client(self, module: ast.Module) -> bool:
        for node in module.body:
            if not isinstance(node, ast.ImportFrom):
                continue
            if node.module != "fastapi.testclient":
                continue
            if any(alias.name == "TestClient" for alias in node.names):
                return True
        return False

    def _imports_app(self, module: ast.Module) -> bool:
        for node in module.body:
            if not isinstance(node, ast.ImportFrom):
                continue
            if node.module != "demo_app.tasks_api":
                continue
            if any(alias.name == "app" for alias in node.names):
                return True
        return False

    def _creates_test_client(self, module: ast.Module) -> bool:
        for node in module.body:
            if not isinstance(node, (ast.Assign, ast.AnnAssign)):
                continue
            value = node.value
            if not isinstance(value, ast.Call):
                continue
            if not isinstance(value.func, ast.Name) or value.func.id != "TestClient":
                continue
            if not value.args:
                continue
            if isinstance(value.args[0], ast.Name) and value.args[0].id == "app":
                return True
        return False

    def _has_app_request_helper(self, module: ast.Module, content: str) -> bool:
        has_request_helper = any(
            isinstance(node, ast.AsyncFunctionDef) and node.name == "_request"
            for node in module.body
        )
        return has_request_helper and "await app(" in content and "http.response.start" in content

    def _is_contract_test(self, node: ast.FunctionDef, content: str, *, api_route: str) -> bool:
        source = ast.get_source_segment(content, node) or ""
        return (
            self._references_path(node, source, api_route)
            and "status_code" in source
            and self._extracts_tasks_from_payload(source)
            and "isinstance" in source
        )

    def _is_e2e_test(self, node: ast.FunctionDef, content: str, *, api_route: str) -> bool:
        source = ast.get_source_segment(content, node) or ""
        return (
            self._references_path(node, source, api_route)
            and self._references_path(node, source, "/")
            and ("response.text" in source or "html" in source)
            and "title" in source
            and self._extracts_tasks_from_payload(source)
        )

    def _infer_test_names(self, content: str) -> list[str]:
        try:
            module = ast.parse(content)
        except SyntaxError:
            return []
        return [
            node.name
            for node in module.body
            if isinstance(node, ast.FunctionDef) and node.name.startswith("test_")
        ]

    def _has_client_get_call(self, node: ast.FunctionDef, path: str) -> bool:
        for candidate in ast.walk(node):
            if not isinstance(candidate, ast.Call):
                continue
            if not isinstance(candidate.func, ast.Attribute):
                continue
            if candidate.func.attr != "get" or not candidate.args:
                continue
            if not isinstance(candidate.args[0], ast.Constant) or candidate.args[0].value != path:
                continue
            return True
        return False

    def _references_path(self, node: ast.FunctionDef, source: str, path: str) -> bool:
        if self._has_client_get_call(node, path):
            return True
        return f'"{path}"' in source or f"'{path}'" in source

    def _extracts_tasks_from_payload(self, source: str) -> bool:
        return (
            '"tasks" in payload' in source
            or "'tasks' in payload" in source
            or 'response.json()["tasks"]' in source
            or "response.json()['tasks']" in source
            or '.json()["tasks"]' in source
            or ".json()['tasks']" in source
            or 'payload["tasks"]' in source
            or "payload['tasks']" in source
        )

    def _api_route(self, context: AgentContext) -> str:
        if context.apis:
            return context.apis[0].route
        return "/api/tasks"
