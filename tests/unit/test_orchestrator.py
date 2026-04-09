"""覆盖主编排器行为的单元测试。"""

from __future__ import annotations

from dataclasses import dataclass, field
import json
from types import SimpleNamespace

from codeingme.agents import AgentContext
from codeingme.agents.backend import BackendAgent
from codeingme.agents.qa import QAAgent
from codeingme.demo_app import DemoAppBlueprint
from codeingme.orchestrator import CodeingmeOrchestrator
from codeingme.runtime import ContainerTestConfig, FilePatch, FilePatchOperation, FilePatchPlan


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


def test_orchestrator_compacts_existing_implementation_writes(tmp_path) -> None:
    target = tmp_path / "demo_app" / "tasks_api.py"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        "from fastapi import FastAPI\n\napp = FastAPI(title='Old Demo')\n",
        encoding="utf-8",
    )
    orchestrator = CodeingmeOrchestrator(workspace_root=tmp_path)
    plan = FilePatchPlan(
        name="implementation",
        patches=[
            FilePatch(
                path="demo_app/tasks_api.py",
                content='from fastapi import FastAPI\n\napp = FastAPI(title="Tasks Demo")\n',
            )
        ],
    )

    compacted = orchestrator._compact_plan_for_apply(plan)

    assert compacted is not None
    assert len(compacted.patches) == 1
    assert compacted.patches[0].operation is FilePatchOperation.DIFF


def test_orchestrator_enables_containerized_verification_when_requested(tmp_path, monkeypatch) -> None:
    (tmp_path / "docker-compose.yml").write_text("services:\n  test:\n    image: python:3.11-slim\n", encoding="utf-8")
    orchestrator = CodeingmeOrchestrator(workspace_root=tmp_path)
    monkeypatch.setenv("CODEINGME_RUN_TESTS_IN_DOCKER", "1")

    config = orchestrator._test_execution_config()

    assert config == ContainerTestConfig(compose_file="docker-compose.yml", service="test")


@dataclass
class _RecordingBackendAgent(BackendAgent):
    contexts: list[set[str]] = field(default_factory=list)

    def run(self, context: AgentContext):
        self.contexts.append(context.graph_slice.node_ids())
        return super().run(context)


@dataclass
class _RecordingQAAgent(QAAgent):
    contexts: list[set[str]] = field(default_factory=list)

    def run(self, context: AgentContext, tests=None):
        self.contexts.append(context.graph_slice.node_ids())
        return super().run(context, tests)


def test_orchestrator_executes_cascade_batches_with_sliced_contexts(tmp_path) -> None:
    blueprint = DemoAppBlueprint()
    orchestrator = CodeingmeOrchestrator(workspace_root=tmp_path, llm_client=_FakeRunLLMClient())
    backend = _RecordingBackendAgent()
    qa = _RecordingQAAgent()
    orchestrator.backend = backend
    orchestrator.qa = qa

    result = orchestrator.run(blueprint.requirement_prompt())

    assert result.final_state == "done"
    assert len(backend.contexts) == 3
    assert len(qa.contexts) == 2
    assert {"schema:task", "api:get:/api/tasks"} in backend.contexts
    assert {
        "schema:task",
        "api:get:/api/tasks",
        "demo_app/tasks_api.py::class:TaskService",
        "demo_app/tasks_api.py::function:list_items",
    } in backend.contexts
    assert qa.contexts[-1] == {
        "schema:task",
        "api:get:/api/tasks",
        "tests_generated/test_tasks_demo.py::function:_get_json",
        "tests_generated/test_tasks_demo.py::function:test_task_contract",
        "tests_generated/test_tasks_demo.py::function:test_task_visibility_rules",
    }


def test_orchestrator_emits_progress_events(tmp_path) -> None:
    blueprint = DemoAppBlueprint()
    orchestrator = CodeingmeOrchestrator(workspace_root=tmp_path, llm_client=_FakeRunLLMClient())
    events = []

    result = orchestrator.run(blueprint.requirement_prompt(), event_callback=events.append)

    assert result.final_state == "done"
    assert any(event.stage == "state" and event.state == "contract_generation" for event in events)
    assert any(
        event.stage == "agent" and event.role == "backend" and event.status == "completed"
        for event in events
    )
    assert any(event.stage == "cascade_batch" and event.status == "completed" for event in events)
    assert any(
        event.stage == "tests" and event.status == "completed" and event.state == "verification"
        for event in events
    )
    assert events[-1].stage == "state"
    assert events[-1].state == "done"
