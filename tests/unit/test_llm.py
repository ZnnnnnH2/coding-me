from __future__ import annotations

import json

import httpx

from codeingme.agents import AgentContext
from codeingme.agents.architect import ArchitectAgent
from codeingme.agents.backend import BackendAgent
from codeingme.agents.devops import DevOpsAgent
from codeingme.agents.frontend import FrontendAgent
from codeingme.agents.qa import QAAgent
from codeingme.contracts import APISpec, DataSchema, RequirementSpec, TestSpec as ContractTestSpec
from codeingme.graph import GraphSlice
from codeingme.llm import LLMCompletion, LLMConfig, RelayLLMClient


def test_relay_llm_client_lists_models_and_completes() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Bearer test-key"
        if request.method == "GET" and request.url.path == "/v1/models":
            return httpx.Response(200, json={"data": [{"id": "gpt-5.4"}, {"id": "gpt-5.3-codex"}]})
        if request.method == "POST" and request.url.path == "/v1/chat/completions":
            payload = json.loads(request.content.decode("utf-8"))
            assert payload["model"] == "gpt-5.4"
            assert payload["reasoning_effort"] == "medium"
            assert payload["messages"][1]["content"] == "Ping"
            return httpx.Response(
                200,
                json={
                    "model": "gpt-5.4",
                    "choices": [{"message": {"content": "Pong"}}],
                    "usage": {"prompt_tokens": 12, "completion_tokens": 3},
                },
            )
        raise AssertionError(f"Unexpected request: {request.method} {request.url}")

    transport = httpx.MockTransport(handler)
    client = RelayLLMClient(LLMConfig(api_key="test-key"), transport=transport)

    try:
        assert client.list_models() == ["gpt-5.4", "gpt-5.3-codex"]
        completion = client.prompt("You are a probe.", "Ping")
    finally:
        client.close()

    assert completion.content == "Pong"
    assert completion.usage["completion_tokens"] == 3


def test_relay_llm_client_caches_identical_prompt_responses() -> None:
    calls = {"post": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST" and request.url.path == "/v1/chat/completions":
            calls["post"] += 1
            payload = json.loads(request.content.decode("utf-8"))
            prompt = payload["messages"][1]["content"]
            return httpx.Response(
                200,
                json={
                    "model": "gpt-5.4",
                    "choices": [{"message": {"content": f"reply:{prompt}:{calls['post']}"}}],
                    "usage": {"prompt_tokens": 10, "completion_tokens": 4},
                },
            )
        raise AssertionError(f"Unexpected request: {request.method} {request.url}")

    transport = httpx.MockTransport(handler)
    client = RelayLLMClient(LLMConfig(api_key="test-key", cache_size=8), transport=transport)

    try:
        first = client.prompt("You are a probe.", "Ping", max_tokens=32)
        second = client.prompt("You are a probe.", "Ping", max_tokens=32)
        third = client.prompt("You are a probe.", "Ping again", max_tokens=32)
    finally:
        client.close()

    assert calls["post"] == 2
    assert first.content == "reply:Ping:1"
    assert first.cached is False
    assert second.content == "reply:Ping:1"
    assert second.cached is True
    assert third.content == "reply:Ping again:2"
    assert client.cache_info() == {"entries": 2, "hits": 1, "misses": 2}


def test_architect_agent_uses_llm_note_when_available() -> None:
    class FakeLLMClient:
        def prompt(self, system_prompt: str, user_prompt: str, **_: object) -> LLMCompletion:
            assert "schemas, apis" in system_prompt
            assert "Requirement:" in user_prompt
            return LLMCompletion(
                model="fake-model",
                content=json.dumps(
                    {
                        "summary": "Task list contract",
                        "design_note": "Keep the contract centered on a task list route.",
                        "schemas": [
                            {
                                "name": "Task",
                                "fields": {"id": "int", "title": "str", "completed": "bool"},
                            }
                        ],
                        "apis": [
                            {
                                "route": "/api/tasks",
                                "method": "GET",
                                "summary": "List tasks",
                                "response_schema": "Task",
                            }
                        ],
                        "risks": ["In-memory storage only"],
                    }
                ),
            )

    context = AgentContext(
        requirement=RequirementSpec(
            title="Build a tasks web app",
            summary="Build a tasks web app",
            acceptance_criteria=["List tasks"],
        ),
        graph_slice=GraphSlice(),
        llm_client=FakeLLMClient(),
    )

    result = ArchitectAgent().run(context)

    assert result.artifacts["llm_model"] == "fake-model"
    assert result.artifacts["llm_response_format"] == "json-contracts"
    assert "task list route" in result.artifacts["llm_design_note"]


def test_architect_agent_bootstrap_specs_use_requirement_specific_llm_contracts() -> None:
    class FakeLLMClient:
        def __init__(self) -> None:
            self.calls = 0

        def prompt(self, system_prompt: str, user_prompt: str, **_: object) -> LLMCompletion:
            self.calls += 1
            assert "requirement-specific" in user_prompt
            return LLMCompletion(
                model="fake-model",
                content=json.dumps(
                    {
                        "summary": "Warehouse dispatch contract",
                        "design_note": "Drive the flow through a warehouse dispatch queue contract.",
                        "schemas": [
                            {
                                "name": "WarehouseDispatchTask",
                                "fields": {"id": "int", "title": "str", "completed": "bool"},
                            }
                        ],
                        "apis": [
                            {
                                "route": "/api/warehouse-dispatch-tasks",
                                "method": "GET",
                                "summary": "List warehouse dispatch tasks",
                                "response_schema": "WarehouseDispatchTask",
                            }
                        ],
                        "risks": ["No persistence"],
                    }
                ),
            )

    client = FakeLLMClient()
    requirement = RequirementSpec(
        title="Build a warehouse dispatch tasks web app",
        summary="Build a warehouse dispatch tasks web app with a paper-and-ink dashboard for operators",
        acceptance_criteria=["List warehouse dispatch tasks"],
    )
    context = AgentContext(requirement=requirement, graph_slice=GraphSlice(), llm_client=client)
    agent = ArchitectAgent()

    result = agent.run(context)
    schemas, apis = agent.bootstrap_specs(context)

    assert client.calls == 1
    assert result.artifacts["generation_mode"] == "llm"
    assert result.artifacts["openapi"] == "GET /api/warehouse-dispatch-tasks"
    assert result.artifacts["schema"] == "WarehouseDispatchTask: id:int, title:str, completed:bool"
    assert schemas == [
        DataSchema(name="WarehouseDispatchTask", fields={"id": "int", "title": "str", "completed": "bool"})
    ]
    assert apis == [
        APISpec(
            route="/api/warehouse-dispatch-tasks",
            method="GET",
            summary="List warehouse dispatch tasks",
            response_schema="WarehouseDispatchTask",
        )
    ]


def test_architect_agent_bootstrap_specs_fall_back_to_requirement_heuristics() -> None:
    requirement = RequirementSpec(
        title="Build a warehouse dispatch tasks web app",
        summary="Build a warehouse dispatch tasks web app with listing and completion state",
        acceptance_criteria=["List warehouse dispatch tasks"],
    )
    context = AgentContext(requirement=requirement, graph_slice=GraphSlice())
    agent = ArchitectAgent()

    result = agent.run(context)
    schemas, apis = agent.bootstrap_specs(context)

    assert result.artifacts["generation_mode"] == "heuristic"
    assert schemas == [
        DataSchema(
            name="WarehouseDispatchTask",
            fields={"id": "int", "title": "str", "completed": "bool"},
        )
    ]
    assert apis == [
        APISpec(
            route="/api/warehouse-dispatch-tasks",
            method="GET",
            summary="List warehouse dispatch tasks",
            response_schema="WarehouseDispatchTask",
        )
    ]


def test_backend_agent_uses_llm_generated_bundle() -> None:
    class FakeLLMClient:
        def prompt(self, system_prompt: str, user_prompt: str, **_: object) -> LLMCompletion:
            assert "files, routes, imports, and risks" in system_prompt
            assert "demo_app/tasks_api.py" in user_prompt
            return LLMCompletion(
                model="fake-model",
                content=json.dumps(
                    {
                        "summary": "Generated task backend",
                        "files": [
                            {
                                "path": "demo_app/tasks_api.py",
                                "language": "python",
                                "content": (
                                    "```python\n"
                                    "from pathlib import Path\n\n"
                                    "from fastapi import FastAPI\n"
                                    "from fastapi.responses import HTMLResponse\n\n"
                                    "app = FastAPI()\n"
                                    "class TaskService:\n"
                                    "    def __init__(self) -> None:\n"
                                    "        self._tasks = [{\"id\": 1, \"title\": \"LLM task\", \"completed\": True}]\n\n"
                                    "    def list_tasks(self):\n"
                                    "        return list(self._tasks)\n\n"
                                    "task_service = TaskService()\n\n"
                                    "def _render_task_list() -> str:\n"
                                    "    template = '<ul>{{TASK_ITEMS}}</ul>'\n"
                                    "    return template.replace('{{TASK_ITEMS}}', '<li>LLM task</li>')\n\n"
                                    "@app.get('/api/tasks')\n"
                                    "def list_tasks():\n"
                                    "    return {'tasks': task_service.list_tasks()}\n\n"
                                    "@app.get('/', response_class=HTMLResponse)\n"
                                    "def task_list():\n"
                                    "    return HTMLResponse(_render_task_list())\n"
                                    "```"
                                ),
                            }
                        ],
                        "routes": ["GET /api/tasks", "GET /"],
                        "imports": ["fastapi.FastAPI", "fastapi.responses.HTMLResponse", "pathlib.Path"],
                        "risks": ["Template coupling"],
                    }
                ),
            )

    result = BackendAgent().run(_agent_context(FakeLLMClient()))

    assert result.artifacts["generation_mode"] == "llm"
    assert result.artifacts["llm_model"] == "fake-model"
    assert result.artifacts["llm_response_format"] == "json-files"
    assert result.artifacts["routes"] == ["GET /api/tasks", "GET /"]
    assert result.artifacts["imports"] == ["fastapi.FastAPI", "fastapi.responses.HTMLResponse", "pathlib.Path"]
    assert result.file_plan is not None
    assert "LLM task" in result.file_plan.patches[1].content
    assert "```" not in result.file_plan.patches[1].content


def test_frontend_agent_uses_llm_generated_bundle() -> None:
    class FakeLLMClient:
        def prompt(self, system_prompt: str, user_prompt: str, **_: object) -> LLMCompletion:
            assert "files, components, and risks" in system_prompt
            return LLMCompletion(
                model="fake-model",
                content=json.dumps(
                    {
                        "summary": "Generated task list page",
                        "files": [
                            {
                                "path": "demo_app/static/task_list.html",
                                "language": "html",
                                "content": (
                                    "<!DOCTYPE html><html><head><style>body{font-family:Georgia,serif;}</style></head>"
                                    "<body><main><h1>Task Board</h1>"
                                    "<label><input id=\"task-search\" type=\"search\" /></label>"
                                    "<div><button data-filter=\"all\">All</button></div>"
                                    "<aside id=\"task-spotlight\"></aside>"
                                    "<ul id=\"task-list\">{{TASK_ITEMS}}</ul>"
                                    "<script>document.body.dataset.ready='1';</script>"
                                    "</main></body></html>"
                                ),
                            }
                        ],
                        "components": ["TaskListPage", "TaskCommandBar", "TaskSpotlight"],
                        "risks": ["Inline script needs careful maintenance"],
                    }
                ),
            )

    result = FrontendAgent().run(_agent_context(FakeLLMClient()))

    assert result.artifacts["generation_mode"] == "llm"
    assert result.artifacts["components"] == ["TaskListPage", "TaskCommandBar", "TaskSpotlight"]
    assert result.file_plan is not None
    assert "{{TASK_ITEMS}}" in result.file_plan.patches[0].content


def test_backend_agent_accepts_requirement_specific_llm_bundle() -> None:
    class FakeLLMClient:
        def prompt(self, system_prompt: str, user_prompt: str, **_: object) -> LLMCompletion:
            assert "reflect the requirement domain" in user_prompt
            return LLMCompletion(
                model="fake-model",
                content=json.dumps(
                    {
                        "summary": "Generated warehouse dispatch backend",
                        "files": [
                            {
                                "path": "demo_app/tasks_api.py",
                                "language": "python",
                                "content": (
                                    "from pathlib import Path\n\n"
                                    "from fastapi import FastAPI\n"
                                    "from fastapi.responses import HTMLResponse\n\n"
                                    "app = FastAPI(title='Warehouse Dispatch Board')\n\n"
                                    "class PickingTaskService:\n"
                                    "    def __init__(self) -> None:\n"
                                    "        self._tasks = [\n"
                                    "            {'id': 101, 'title': 'Pick aisle C-14', 'completed': False},\n"
                                    "            {'id': 102, 'title': 'Stage pallet for dock 2', 'completed': True},\n"
                                    "        ]\n\n"
                                    "    def list_tasks(self):\n"
                                    "        return list(self._tasks)\n\n"
                                    "picking_service = PickingTaskService()\n\n"
                                    "def render_dashboard() -> str:\n"
                                    "    template = Path(__file__).with_name('static').joinpath('task_list.html').read_text(encoding='utf-8')\n"
                                    "    items = '\\n'.join(f\"<li>{task['title']}</li>\" for task in picking_service.list_tasks())\n"
                                    "    return template.replace('{{TASK_ITEMS}}', items)\n\n"
                                    "@app.get('/api/tasks')\n"
                                    "def list_tasks():\n"
                                    "    return {'tasks': picking_service.list_tasks()}\n\n"
                                    "@app.get('/', response_class=HTMLResponse)\n"
                                    "def dispatch_dashboard():\n"
                                    "    return HTMLResponse(render_dashboard())\n"
                                ),
                            }
                        ],
                        "routes": [],
                        "imports": [],
                        "risks": ["In-memory data only"],
                    }
                ),
            )

    result = BackendAgent().run(_agent_context(FakeLLMClient()))

    assert result.artifacts["generation_mode"] == "llm"
    assert result.artifacts["routes"] == ["GET /", "GET /api/tasks"]
    assert "PickingTaskService" in result.file_plan.patches[1].content
    assert "Warehouse Dispatch Board" in result.file_plan.patches[1].content


def test_backend_agent_accepts_context_specific_api_route() -> None:
    class FakeLLMClient:
        def prompt(self, system_prompt: str, user_prompt: str, **_: object) -> LLMCompletion:
            assert "/api/warehouse-dispatch-tasks" in user_prompt
            return LLMCompletion(
                model="fake-model",
                content=json.dumps(
                    {
                        "summary": "Generated warehouse dispatch backend",
                        "files": [
                            {
                                "path": "demo_app/tasks_api.py",
                                "language": "python",
                                "content": (
                                    "from pathlib import Path\n\n"
                                    "from fastapi import FastAPI\n"
                                    "from fastapi.responses import HTMLResponse\n\n"
                                    "app = FastAPI(title='Warehouse Dispatch Board')\n\n"
                                    "class WarehouseDispatchTaskService:\n"
                                    "    def __init__(self) -> None:\n"
                                    "        self._tasks = [\n"
                                    "            {'id': 1, 'title': 'Pick dock 4 freight', 'completed': False},\n"
                                    "            {'id': 2, 'title': 'Confirm outbound pallet', 'completed': True},\n"
                                    "        ]\n\n"
                                    "    def list_tasks(self):\n"
                                    "        return list(self._tasks)\n\n"
                                    "warehouse_dispatch_task_service = WarehouseDispatchTaskService()\n\n"
                                    "def render_board() -> str:\n"
                                    "    template = Path(__file__).with_name('static').joinpath('task_list.html').read_text(encoding='utf-8')\n"
                                    "    items = '\\n'.join(f\"<li>{task['title']}</li>\" for task in warehouse_dispatch_task_service.list_tasks())\n"
                                    "    return template.replace('{{TASK_ITEMS}}', items)\n\n"
                                    "@app.get('/api/warehouse-dispatch-tasks')\n"
                                    "def list_tasks():\n"
                                    "    return {'tasks': warehouse_dispatch_task_service.list_tasks()}\n\n"
                                    "@app.get('/', response_class=HTMLResponse)\n"
                                    "def task_list():\n"
                                    "    return HTMLResponse(render_board())\n"
                                ),
                            }
                        ],
                        "routes": ["GET /api/warehouse-dispatch-tasks", "GET /"],
                        "imports": ["fastapi.FastAPI", "fastapi.responses.HTMLResponse", "pathlib.Path"],
                        "risks": ["In-memory data only"],
                    }
                ),
            )

    result = BackendAgent().run(
        _agent_context(
            FakeLLMClient(),
            requirement=RequirementSpec(
                title="Build a warehouse dispatch tasks web app",
                summary="Build a warehouse dispatch tasks web app with listing and completion state",
                acceptance_criteria=["List warehouse dispatch tasks"],
            ),
            apis=[
                APISpec(
                    route="/api/warehouse-dispatch-tasks",
                    method="GET",
                    summary="List warehouse dispatch tasks",
                    response_schema="WarehouseDispatchTask",
                )
            ],
            schemas=[
                DataSchema(
                    name="WarehouseDispatchTask",
                    fields={"id": "int", "title": "str", "completed": "bool"},
                )
            ],
        )
    )

    assert result.artifacts["generation_mode"] == "llm"
    assert result.artifacts["routes"] == ["GET /api/warehouse-dispatch-tasks", "GET /"]
    assert "WarehouseDispatchTaskService" in result.file_plan.patches[1].content
    assert "@app.get('/api/warehouse-dispatch-tasks')" in result.file_plan.patches[1].content


def test_qa_agent_uses_structured_llm_bundle_when_valid() -> None:
    class FakeLLMClient:
        def prompt(self, system_prompt: str, user_prompt: str, **_: object) -> LLMCompletion:
            assert "files, tests, and risks" in system_prompt
            assert "tests_generated/test_tasks_demo.py" in user_prompt
            return LLMCompletion(
                model="fake-model",
                content=json.dumps(
                    {
                        "summary": "Generated tests",
                        "files": [
                            {
                                "path": "tests_generated/test_tasks_demo.py",
                                "language": "python",
                                "content": (
                                    "from fastapi.testclient import TestClient\n"
                                    "from demo_app.tasks_api import app\n\n"
                                    "client = TestClient(app)\n\n"
                                    "def test_tasks_contract() -> None:\n"
                                    "    response = client.get(\"/api/tasks\")\n"
                                    "    payload = response.json()\n"
                                    "    assert \"tasks\" in payload\n"
                                    "    assert isinstance(payload[\"tasks\"], list)\n"
                                    "    assert response.status_code == 200\n\n"
                                    "def test_tasks_e2e() -> None:\n"
                                    "    tasks = client.get(\"/api/tasks\").json()[\"tasks\"]\n"
                                    "    response = client.get(\"/\")\n"
                                    "    assert response.status_code == 200\n"
                                    "    for task in tasks:\n"
                                    "        assert task[\"title\"] in response.text\n"
                                ),
                            }
                        ],
                        "tests": ["test_tasks_contract", "test_tasks_e2e"],
                        "risks": ["Shallow assertions"],
                    }
                ),
            )

    result = QAAgent().run(_agent_context(FakeLLMClient()), _tests())

    assert result.artifacts["generation_mode"] == "llm"
    assert result.artifacts["tests"] == ["test_tasks_contract", "test_tasks_e2e"]
    assert result.artifacts["llm_response_format"] == "json-files"
    assert result.file_plan is not None
    assert "def test_tasks_e2e()" in result.file_plan.patches[0].content


def test_frontend_agent_infers_components_for_requirement_specific_page() -> None:
    class FakeLLMClient:
        def prompt(self, system_prompt: str, user_prompt: str, **_: object) -> LLMCompletion:
            assert "Translate the requirement into the page title" in user_prompt
            return LLMCompletion(
                model="fake-model",
                content=json.dumps(
                    {
                        "summary": "Generated dispatch dashboard",
                        "files": [
                            {
                                "path": "demo_app/static/task_list.html",
                                "language": "html",
                                "content": (
                                    "<!DOCTYPE html><html><head><title>Warehouse Dispatch Board</title>"
                                    "<style>body{font-family:'IBM Plex Serif',serif;}main{padding:2rem;}</style></head>"
                                    "<body><main><p>Paper-and-ink dispatch dashboard.</p><h1>Picking Queue</h1>"
                                    "<label><input id=\"task-search\" type=\"search\" /></label>"
                                    "<div><button data-filter=\"all\">All</button><button data-filter=\"done\">Done</button></div>"
                                    "<aside id=\"task-spotlight\"></aside>"
                                    "<ul id=\"task-list\">{{TASK_ITEMS}}</ul>"
                                    "<script>document.body.dataset.mode='dispatch';</script>"
                                    "</main></body></html>"
                                ),
                            }
                        ],
                        "components": [],
                        "risks": ["No live updates"],
                    }
                ),
            )

    result = FrontendAgent().run(_agent_context(FakeLLMClient()))

    assert result.artifacts["generation_mode"] == "llm"
    assert result.artifacts["components"] == ["TaskListPage", "TaskCommandBar", "TaskSpotlight"]
    assert "Warehouse Dispatch Board" in result.file_plan.patches[0].content


def test_qa_agent_accepts_requirement_specific_test_names_and_infers_metadata() -> None:
    class FakeLLMClient:
        def prompt(self, system_prompt: str, user_prompt: str, **_: object) -> LLMCompletion:
            assert "Use meaningful pytest test names" in user_prompt
            return LLMCompletion(
                model="fake-model",
                content=json.dumps(
                    {
                        "summary": "Generated dispatch checks",
                        "files": [
                            {
                                "path": "tests_generated/test_tasks_demo.py",
                                "language": "python",
                                "content": (
                                    "from fastapi.testclient import TestClient\n"
                                    "from demo_app.tasks_api import app\n\n"
                                    "client = TestClient(app)\n\n"
                                    "def test_dispatch_contract_shape() -> None:\n"
                                    "    response = client.get('/api/tasks')\n"
                                    "    payload = response.json()\n"
                                    "    assert response.status_code == 200\n"
                                    "    assert 'tasks' in payload\n"
                                    "    assert isinstance(payload['tasks'], list)\n\n"
                                    "def test_dispatch_dashboard_renders_api_titles() -> None:\n"
                                    "    payload = client.get('/api/tasks').json()\n"
                                    "    tasks = payload['tasks']\n"
                                    "    response = client.get('/')\n"
                                    "    assert response.status_code == 200\n"
                                    "    for task in tasks:\n"
                                    "        assert task['title'] in response.text\n"
                                ),
                            }
                        ],
                        "tests": [],
                        "risks": ["HTML assertions stay shallow"],
                    }
                ),
            )

    result = QAAgent().run(_agent_context(FakeLLMClient()), _tests())

    assert result.artifacts["generation_mode"] == "llm"
    assert result.artifacts["tests"] == [
        "test_dispatch_contract_shape",
        "test_dispatch_dashboard_renders_api_titles",
    ]
    assert "test_dispatch_dashboard_renders_api_titles" in result.file_plan.patches[0].content


def test_qa_agent_accepts_context_specific_api_route() -> None:
    class FakeLLMClient:
        def prompt(self, system_prompt: str, user_prompt: str, **_: object) -> LLMCompletion:
            assert "/api/warehouse-dispatch-tasks" in user_prompt
            return LLMCompletion(
                model="fake-model",
                content=json.dumps(
                    {
                        "summary": "Generated warehouse dispatch tests",
                        "files": [
                            {
                                "path": "tests_generated/test_tasks_demo.py",
                                "language": "python",
                                "content": (
                                    "from fastapi.testclient import TestClient\n"
                                    "from demo_app.tasks_api import app\n\n"
                                    "client = TestClient(app)\n\n"
                                    "def test_dispatch_contract_shape() -> None:\n"
                                    "    response = client.get('/api/warehouse-dispatch-tasks')\n"
                                    "    payload = response.json()\n"
                                    "    assert response.status_code == 200\n"
                                    "    assert 'tasks' in payload\n"
                                    "    assert isinstance(payload['tasks'], list)\n\n"
                                    "def test_dispatch_dashboard_renders_api_titles() -> None:\n"
                                    "    payload = client.get('/api/warehouse-dispatch-tasks').json()\n"
                                    "    tasks = payload['tasks']\n"
                                    "    response = client.get('/')\n"
                                    "    assert response.status_code == 200\n"
                                    "    for task in tasks:\n"
                                    "        assert task['title'] in response.text\n"
                                ),
                            }
                        ],
                        "tests": [
                            "test_dispatch_contract_shape",
                            "test_dispatch_dashboard_renders_api_titles",
                        ],
                        "risks": ["HTML assertions stay shallow"],
                    }
                ),
            )

    result = QAAgent().run(
        _agent_context(
            FakeLLMClient(),
            requirement=RequirementSpec(
                title="Build a warehouse dispatch tasks web app",
                summary="Build a warehouse dispatch tasks web app with listing and completion state",
                acceptance_criteria=["List warehouse dispatch tasks"],
            ),
            apis=[
                APISpec(
                    route="/api/warehouse-dispatch-tasks",
                    method="GET",
                    summary="List warehouse dispatch tasks",
                    response_schema="WarehouseDispatchTask",
                )
            ],
            schemas=[
                DataSchema(
                    name="WarehouseDispatchTask",
                    fields={"id": "int", "title": "str", "completed": "bool"},
                )
            ],
        ),
        _tests(),
    )

    assert result.artifacts["generation_mode"] == "llm"
    assert result.artifacts["tests"] == [
        "test_dispatch_contract_shape",
        "test_dispatch_dashboard_renders_api_titles",
    ]
    assert "client.get('/api/warehouse-dispatch-tasks')" in result.file_plan.patches[0].content


def test_devops_agent_uses_structured_llm_bundle_when_valid() -> None:
    class FakeLLMClient:
        def prompt(self, system_prompt: str, user_prompt: str, **_: object) -> LLMCompletion:
            assert "services, commands, and risks" in system_prompt
            assert "docker-compose.yml" in user_prompt
            return LLMCompletion(
                model="fake-model",
                content=json.dumps(
                    {
                        "summary": "Generated container stack",
                        "files": [
                            {
                                "path": "Dockerfile",
                                "language": "dockerfile",
                                "content": (
                                    "```dockerfile\n"
                                    "FROM python:3.11-slim\n\n"
                                    "WORKDIR /workspace\n"
                                    "ENV PYTHONPATH=/workspace\n"
                                    "RUN python -m pip install --no-cache-dir fastapi httpx pytest uvicorn\n"
                                    "COPY demo_app ./demo_app\n"
                                    "COPY tests_generated ./tests_generated\n"
                                    "CMD [\"python\", \"-m\", \"uvicorn\", \"demo_app.tasks_api:app\", \"--host\", \"0.0.0.0\", \"--port\", \"8000\"]\n"
                                    "```"
                                ),
                            },
                            {
                                "path": "docker-compose.yml",
                                "language": "yaml",
                                "content": (
                                    "services:\n"
                                    "  app:\n"
                                    "    build:\n"
                                    "      context: .\n"
                                    "      dockerfile: Dockerfile\n"
                                    "    working_dir: /workspace\n"
                                    "    environment:\n"
                                    "      PYTHONPATH: /workspace\n"
                                    "    command: python -m uvicorn demo_app.tasks_api:app --host 0.0.0.0 --port 8000\n"
                                    "  test:\n"
                                    "    build:\n"
                                    "      context: .\n"
                                    "      dockerfile: Dockerfile\n"
                                    "    working_dir: /workspace\n"
                                    "    environment:\n"
                                    "      PYTHONPATH: /workspace\n"
                                    "    command: python -m pytest tests_generated\n"
                                ),
                            },
                            {
                                "path": ".dockerignore",
                                "language": "text",
                                "content": "__pycache__/\n.pytest_cache/\n.venv/\n",
                            },
                        ],
                        "services": ["app", "test"],
                        "commands": [
                            "python -m uvicorn demo_app.tasks_api:app --host 0.0.0.0 --port 8000",
                            "python -m pytest tests_generated",
                        ],
                        "risks": ["Requires Docker daemon"],
                    }
                ),
            )

    result = DevOpsAgent().run(_agent_context(FakeLLMClient()))

    assert result.artifacts["generation_mode"] == "llm"
    assert result.artifacts["services"] == ["app", "test"]
    assert result.file_plan is not None
    assert result.file_plan.patches[0].path == "Dockerfile"
    assert "demo_app.tasks_api:app" in result.file_plan.patches[0].content


def test_backend_agent_falls_back_on_invalid_json_response() -> None:
    class FakeLLMClient:
        def prompt(self, system_prompt: str, user_prompt: str, **_: object) -> LLMCompletion:
            return LLMCompletion(model="fake-model", content="not json")

    result = BackendAgent().run(_agent_context(FakeLLMClient()))

    assert result.artifacts["generation_mode"] == "template"
    assert result.artifacts["llm_fallback"] == "true"
    assert "valid JSON object" in result.artifacts["llm_error"]


def test_backend_agent_falls_back_when_required_file_is_missing() -> None:
    class FakeLLMClient:
        def prompt(self, system_prompt: str, user_prompt: str, **_: object) -> LLMCompletion:
            return LLMCompletion(
                model="fake-model",
                content=json.dumps(
                    {
                        "summary": "Broken backend",
                        "files": [
                            {
                                "path": "wrong/path.py",
                                "language": "python",
                                "content": "print('oops')",
                            }
                        ],
                        "routes": ["GET /api/tasks", "GET /"],
                        "imports": ["fastapi.FastAPI"],
                        "risks": [],
                    }
                ),
            )

    result = BackendAgent().run(_agent_context(FakeLLMClient()))

    assert result.artifacts["generation_mode"] == "template"
    assert result.artifacts["llm_fallback"] == "true"
    assert "missing required files" in result.artifacts["llm_error"]


def test_frontend_agent_falls_back_on_invalid_generated_bundle() -> None:
    class FakeLLMClient:
        def prompt(self, system_prompt: str, user_prompt: str, **_: object) -> LLMCompletion:
            return LLMCompletion(
                model="fake-model",
                content=json.dumps(
                    {
                        "summary": "Broken page",
                        "files": [
                            {
                                "path": "demo_app/static/task_list.html",
                                "language": "html",
                                "content": "<html><body><div>No list placeholder</div></body></html>",
                            }
                        ],
                        "components": [],
                        "risks": [],
                    }
                ),
            )

    result = FrontendAgent().run(_agent_context(FakeLLMClient()))

    assert result.artifacts["generation_mode"] == "template"
    assert result.artifacts["llm_fallback"] == "true"
    assert "failed validation" in result.artifacts["llm_error"]


def test_qa_agent_falls_back_to_template_on_llm_error() -> None:
    class BrokenLLMClient:
        def prompt(self, system_prompt: str, user_prompt: str, **_: object) -> LLMCompletion:
            raise RuntimeError("relay unavailable")

    result = QAAgent().run(_agent_context(BrokenLLMClient()), _tests())

    assert result.artifacts["generation_mode"] == "template"
    assert result.artifacts["llm_fallback"] == "true"
    assert "relay unavailable" in result.artifacts["llm_error"]
    assert result.file_plan is not None
    assert "def test_tasks_contract()" in result.file_plan.patches[0].content
    assert "from demo_app.tasks_api import app" in result.file_plan.patches[0].content


def test_devops_agent_falls_back_on_invalid_generated_bundle() -> None:
    class FakeLLMClient:
        def prompt(self, system_prompt: str, user_prompt: str, **_: object) -> LLMCompletion:
            return LLMCompletion(
                model="fake-model",
                content=json.dumps(
                    {
                        "summary": "Broken container stack",
                        "files": [
                            {
                                "path": "Dockerfile",
                                "language": "dockerfile",
                                "content": "FROM python:3.11-slim\n",
                            },
                            {
                                "path": "docker-compose.yml",
                                "language": "yaml",
                                "content": "services:\n  app:\n    image: python:3.11-slim\n",
                            },
                            {
                                "path": ".dockerignore",
                                "language": "text",
                                "content": "__pycache__/\n",
                            },
                        ],
                        "services": ["app"],
                        "commands": ["python -m pytest tests_generated"],
                        "risks": [],
                    }
                ),
            )

    result = DevOpsAgent().run(_agent_context(FakeLLMClient()))

    assert result.artifacts["generation_mode"] == "template"
    assert result.artifacts["llm_fallback"] == "true"
    assert "failed validation" in result.artifacts["llm_error"]


def _agent_context(
    llm_client: object,
    *,
    requirement: RequirementSpec | None = None,
    apis: list[APISpec] | None = None,
    schemas: list[DataSchema] | None = None,
) -> AgentContext:
    return AgentContext(
        requirement=requirement
        or RequirementSpec(
            title="Build a tasks web app",
            summary="Build a tasks web app",
            acceptance_criteria=["List tasks"],
        ),
        graph_slice=GraphSlice(),
        apis=apis or [APISpec(route="/api/tasks", method="GET", summary="List tasks", response_schema="Task")],
        schemas=schemas or [DataSchema(name="Task", fields={"id": "int", "title": "str", "completed": "bool"})],
        llm_client=llm_client,
    )


def _tests() -> list[ContractTestSpec]:
    return [
        ContractTestSpec(
            name="tasks_contract_test",
            description="contract",
            expected_state="green",
            path="tests_generated/test_tasks_demo.py",
        )
    ]
