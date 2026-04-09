"""覆盖演示流程的端到端测试。"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from codeingme.agents.backend import BackendAgent
from codeingme.agents.base import AgentContext, AgentResult
from codeingme.demo_app import DemoAppBlueprint
from codeingme.orchestrator import CodeingmeOrchestrator
from codeingme.runtime import FilePatch, FilePatchOperation, FilePatchPlan, PatchApplier


class _FakeRunLLMClient:
    config = SimpleNamespace(model="fake-run-llm", generation_max_attempts=3)

    def prompt(self, system_prompt: str, user_prompt: str, **_: object):
        requirement = user_prompt.lower()
        schema_name = "WarehouseDispatchTask" if "warehouse dispatch" in requirement else "Task"
        route = "/api/warehouse-dispatch-tasks" if "warehouse dispatch" in requirement else "/api/tasks"
        response_key = "warehouse_dispatch_tasks" if "warehouse dispatch" in requirement else "tasks"
        summary = "List warehouse dispatch tasks" if "warehouse dispatch" in requirement else "List tasks"
        module_name = "warehouse_dispatch_tasks_api" if "warehouse dispatch" in requirement else "tasks_api"

        if "architect agent inside" in system_prompt:
            return _completion(
                {
                    "summary": summary,
                    "design_note": f"Use {schema_name} as the contract behind {route}.",
                    "schemas": [{"name": schema_name, "fields": {"id": "int", "title": "str", "completed": "bool"}}],
                    "apis": [{"route": route, "method": "GET", "summary": summary, "response_schema": schema_name}],
                    "risks": ["In-memory only"],
                }
            )

        if "backend agent in a state-machine-driven app generator" in system_prompt:
            return _completion(
                {
                    "summary": f"Generated {schema_name} backend",
                    "files": [
                        {
                            "path": f"demo_app/{module_name}.py",
                            "language": "python",
                            "content": _backend_source(schema_name=schema_name, route=route, response_key=response_key),
                        }
                    ],
                    "routes": [f"GET {route}"],
                    "imports": ["fastapi.FastAPI", "pydantic.BaseModel"],
                    "risks": ["In-memory only"],
                }
            )

        if "QA agent in a state-machine-driven app generator" in system_prompt:
            return _completion(
                {
                    "summary": f"Generated {schema_name} tests",
                    "files": [
                        {
                            "path": f"tests_generated/test_{response_key}_demo.py",
                            "language": "python",
                            "content": _qa_source(module_name=module_name, route=route, response_key=response_key),
                        }
                    ],
                    "tests": [
                        f"test_{_singular(response_key)}_contract",
                        f"test_{_singular(response_key)}_visibility_rules",
                    ],
                    "risks": ["Fixture assumptions"],
                }
            )

        if "DevOps agent in a state-machine-driven app generator" in system_prompt:
            return _completion(
                {
                    "summary": "Generated runtime stack",
                    "files": [
                        {
                            "path": "Dockerfile",
                            "language": "dockerfile",
                            "content": _dockerfile(module_name),
                        },
                        {
                            "path": "docker-compose.yml",
                            "language": "yaml",
                            "content": _compose_file(module_name),
                        },
                        {
                            "path": ".dockerignore",
                            "language": "text",
                            "content": "__pycache__/\n.pytest_cache/\n.venv/\ngraph.json\n",
                        },
                    ],
                    "services": ["app", "test"],
                    "commands": [
                        "docker compose up app",
                        "docker compose run --rm test python -m pytest tests_generated",
                    ],
                    "risks": ["Requires Docker daemon"],
                }
            )

        raise AssertionError(f"Unexpected system prompt: {system_prompt}")


def _completion(payload: dict[str, object]):
    from codeingme.llm import LLMCompletion

    return LLMCompletion(model="fake-run-llm", content=json.dumps(payload))


def _backend_source(*, schema_name: str, route: str, response_key: str) -> str:
    service_name = f"{schema_name}Service"
    service_var = _snake_case(service_name)
    return (
        "from fastapi import FastAPI\n"
        "from pydantic import BaseModel\n\n"
        f'app = FastAPI(title="{schema_name} API")\n\n'
        f"class {schema_name}(BaseModel):\n"
        "    id: int\n"
        "    title: str\n"
        "    completed: bool\n\n"
        f"class {service_name}:\n"
        "    def __init__(self) -> None:\n"
        "        self._items = [\n"
        f'            {schema_name}(id=1, title="Review queue", completed=False),\n'
        f'            {schema_name}(id=2, title="Confirm completion", completed=True),\n'
        "        ]\n\n"
        f"    def list_{response_key}(self) -> list[{schema_name}]:\n"
        "        return list(self._items)\n\n"
        f"{service_var} = {service_name}()\n\n"
        f'@app.get("{route}")\n'
        "async def list_items() -> dict[str, list[dict[str, object]]]:\n"
        f'    return {{"{response_key}": [item.model_dump() for item in {service_var}.list_{response_key}()]}}\n'
    )


def _qa_source(*, module_name: str, route: str, response_key: str) -> str:
    singular = _singular(response_key)
    return (
        "from __future__ import annotations\n\n"
        "import asyncio\n"
        "import json\n"
        "import sys\n"
        "from pathlib import Path\n"
        "from typing import Any\n\n"
        "WORKSPACE_ROOT = Path(__file__).resolve().parents[1]\n"
        "if str(WORKSPACE_ROOT) not in sys.path:\n"
        "    sys.path.insert(0, str(WORKSPACE_ROOT))\n\n"
        f"from demo_app.{module_name} import app\n\n"
        "async def _request(path: str) -> tuple[int, str]:\n"
        "    body_parts: list[bytes] = []\n"
        "    status_code = 500\n"
        "    request_sent = False\n\n"
        "    async def receive() -> dict[str, object]:\n"
        "        nonlocal request_sent\n"
        "        if request_sent:\n"
        "            return {'type': 'http.disconnect'}\n"
        "        request_sent = True\n"
        "        return {'type': 'http.request', 'body': b'', 'more_body': False}\n\n"
        "    async def send(message: dict[str, object]) -> None:\n"
        "        nonlocal status_code\n"
        "        if message['type'] == 'http.response.start':\n"
        "            status_code = int(message['status'])\n"
        "        elif message['type'] == 'http.response.body':\n"
        "            body_parts.append(bytes(message.get('body', b'')))\n\n"
        "    await app(\n"
        "        {\n"
        "            'type': 'http',\n"
        "            'asgi': {'version': '3.0'},\n"
        "            'http_version': '1.1',\n"
        "            'method': 'GET',\n"
        "            'scheme': 'http',\n"
        "            'path': path,\n"
        "            'raw_path': path.encode('utf-8'),\n"
        "            'query_string': b'',\n"
        "            'headers': [],\n"
        "            'client': ('testclient', 50000),\n"
        "            'server': ('testserver', 80),\n"
        "            'root_path': '',\n"
        "        },\n"
        "        receive,\n"
        "        send,\n"
        "    )\n"
        "    return status_code, b''.join(body_parts).decode('utf-8')\n\n"
        "def _get_json(path: str) -> tuple[int, dict[str, Any]]:\n"
        "    status_code, body = asyncio.run(_request(path))\n"
        "    return status_code, json.loads(body)\n\n"
        f"def test_{singular}_contract() -> None:\n"
        f'    status_code, payload = _get_json("{route}")\n'
        "    assert status_code == 200\n"
        f'    assert "{response_key}" in payload\n'
        f'    assert isinstance(payload["{response_key}"], list)\n'
        f'    first_item = payload["{response_key}"][0]\n'
        "    assert isinstance(first_item['id'], int)\n"
        "    assert isinstance(first_item['title'], str)\n"
        "    assert isinstance(first_item['completed'], bool)\n\n"
        f"def test_{singular}_visibility_rules() -> None:\n"
        f'    status_code, payload = _get_json("{route}")\n'
        f'    items = payload["{response_key}"]\n'
        "    assert status_code == 200\n"
        "    assert any(item['completed'] is True for item in items)\n"
        "    assert any(item['completed'] is False for item in items)\n"
    )


def _dockerfile(module_name: str) -> str:
    return (
        "FROM python:3.11-slim\n\n"
        "WORKDIR /workspace\n\n"
        "ENV PYTHONDONTWRITEBYTECODE=1\n"
        "ENV PYTHONUNBUFFERED=1\n"
        "ENV PYTHONPATH=/workspace\n\n"
        "RUN python -m pip install --no-cache-dir --upgrade pip \\\n"
        "    && python -m pip install --no-cache-dir fastapi httpx pytest uvicorn\n\n"
        "COPY demo_app ./demo_app\n"
        "COPY tests_generated ./tests_generated\n\n"
        'CMD ["python", "-m", "uvicorn", "demo_app.'
        + module_name
        + ':app", "--host", "0.0.0.0", "--port", "8000"]\n'
    )


def _compose_file(module_name: str) -> str:
    return (
        "services:\n"
        "  app:\n"
        "    build:\n"
        "      context: .\n"
        "      dockerfile: Dockerfile\n"
        "    working_dir: /workspace\n"
        "    environment:\n"
        "      PYTHONPATH: /workspace\n"
        f"    command: python -m uvicorn demo_app.{module_name}:app --host 0.0.0.0 --port 8000\n"
        "    ports:\n"
        '      - "${APP_PORT:-8000}:8000"\n'
        "  test:\n"
        "    build:\n"
        "      context: .\n"
        "      dockerfile: Dockerfile\n"
        "    working_dir: /workspace\n"
        "    environment:\n"
        "      PYTHONPATH: /workspace\n"
        "    command: python -m pytest tests_generated\n"
    )


def _snake_case(value: str) -> str:
    import re

    parts = re.findall(r"[A-Z]?[a-z0-9]+", value)
    return "_".join(part.lower() for part in parts) or "service"


def _singular(value: str) -> str:
    if value.endswith("ies") and len(value) > 3:
        return value[:-3] + "y"
    if value.endswith("s") and len(value) > 1:
        return value[:-1]
    return value


class BrokenBackendAgent(BackendAgent):
    def run(self, context: AgentContext) -> AgentResult:
        result = super().run(context)
        result.summary = "Generated an intentionally incomplete FastAPI backend"
        result.file_plan = FilePatchPlan(
            name="broken_backend",
            patches=[
                FilePatch(path="demo_app/__init__.py", content="from .tasks_api import app\n"),
                FilePatch(
                    path="demo_app/tasks_api.py",
                    content='from fastapi import FastAPI\n\napp = FastAPI(title="Broken Tasks Demo")\n',
                ),
            ],
        )
        return result


class RecordingPatchApplier(PatchApplier):
    def __init__(self, root_dir: Path) -> None:
        super().__init__(root_dir)
        self.applied_plans: list[tuple[str, list[tuple[str, FilePatchOperation]]]] = []

    def apply(self, plan: FilePatchPlan) -> list[object]:
        self.applied_plans.append(
            (plan.name, [(patch.path, patch.operation) for patch in plan.patches])
        )
        return super().apply(plan)


def test_demo_flow_reaches_done_state(tmp_path) -> None:
    blueprint = DemoAppBlueprint()
    result = CodeingmeOrchestrator(workspace_root=tmp_path, llm_client=_FakeRunLLMClient()).run(blueprint.requirement_prompt())

    assert result.final_state == "done"
    assert "schema:task" in result.graph_nodes
    assert "api:get:/api/tasks" in result.blast_radius
    assert "schema:task" in result.cascade_order
    assert result.cascade_batches[0] == ["schema:task"]
    assert result.cascade_tasks[0].node_id == "schema:task"
    assert result.cascade_tasks[0].role == "backend"
    assert "demo_app/tasks_api.py::class:TaskService" in result.graph_nodes
    assert "demo_app/tasks_api.py::function:list_items" in result.graph_nodes
    assert "demo_app/tasks_api.py::function:list_items" in result.graph_sync_added
    assert "api:get:/api/tasks" in result.context_slice_nodes
    assert "ModuleNotFoundError" in result.red_test_output
    assert "2 passed" in result.verification_output
    assert Path(result.graph_path).exists()
    assert (tmp_path / "Dockerfile").exists()
    assert (tmp_path / "demo_app" / "tasks_api.py").exists()
    assert (tmp_path / "docker-compose.yml").exists()


def test_demo_flow_rolls_back_failed_implementation(tmp_path) -> None:
    blueprint = DemoAppBlueprint()
    orchestrator = CodeingmeOrchestrator(workspace_root=tmp_path, llm_client=_FakeRunLLMClient())
    orchestrator.backend = BrokenBackendAgent()

    with pytest.raises(RuntimeError, match="Verification failed"):
        orchestrator.run(blueprint.requirement_prompt())

    assert not (tmp_path / "demo_app" / "tasks_api.py").exists()
    assert not (tmp_path / "demo_app" / "__init__.py").exists()
    assert not (tmp_path / "Dockerfile").exists()
    assert not (tmp_path / "docker-compose.yml").exists()
    assert (tmp_path / "tests_generated" / "test_tasks_demo.py").exists()
    assert "service:task_service" not in (tmp_path / "graph.json").read_text(encoding="utf-8")


def test_demo_flow_compacts_existing_backend_write_into_diff(tmp_path) -> None:
    blueprint = DemoAppBlueprint()
    existing_backend = tmp_path / "demo_app" / "tasks_api.py"
    existing_backend.parent.mkdir(parents=True, exist_ok=True)
    existing_backend.write_text(
        'from fastapi import FastAPI\n\napp = FastAPI(title="Placeholder Tasks Demo")\n',
        encoding="utf-8",
    )

    orchestrator = CodeingmeOrchestrator(workspace_root=tmp_path)
    orchestrator.llm_client = _FakeRunLLMClient()
    orchestrator._reset_workspace = lambda: None
    recording_applier = RecordingPatchApplier(tmp_path)
    orchestrator.patch_applier = recording_applier

    result = orchestrator.run(blueprint.requirement_prompt())

    implementation_plan = next(plan for plan in recording_applier.applied_plans if plan[0] == "implementation")

    assert result.final_state == "done"
    assert any(path == "demo_app/tasks_api.py" for path, _operation in implementation_plan[1])


def test_demo_flow_supports_requirement_specific_bootstrap_specs(tmp_path) -> None:
    requirement = "Build a warehouse dispatch tasks backend module with listing and completion state"

    result = CodeingmeOrchestrator(workspace_root=tmp_path, llm_client=_FakeRunLLMClient()).run(requirement)

    backend_source = (
        tmp_path / "demo_app" / "warehouse_dispatch_tasks_api.py"
    ).read_text(encoding="utf-8")
    qa_source = (
        tmp_path / "tests_generated" / "test_warehouse_dispatch_tasks_demo.py"
    ).read_text(encoding="utf-8")

    assert result.final_state == "done"
    assert "schema:warehousedispatchtask" in result.graph_nodes
    assert "api:get:/api/warehouse-dispatch-tasks" in result.graph_nodes
    assert result.cascade_tasks[0].node_id == "schema:warehousedispatchtask"
    assert "api:get:/api/warehouse-dispatch-tasks" in result.context_slice_nodes
    assert '@app.get("/api/warehouse-dispatch-tasks")' in backend_source
    assert '_get_json("/api/warehouse-dispatch-tasks")' in qa_source
    assert 'payload["warehouse_dispatch_tasks"]' in qa_source
