from __future__ import annotations

import json

import httpx
import pytest

from codeingme.agents import AgentContext
from codeingme.agents.architect import ArchitectAgent
from codeingme.agents.backend import BackendAgent
from codeingme.agents.devops import DevOpsAgent
from codeingme.agents.qa import QAAgent
from codeingme.contracts import APISpec, DataSchema, RequirementSpec, TestSpec as ContractTestSpec
from codeingme.graph import GraphSlice
from codeingme.llm import LLMCompletion, LLMConfig, RelayLLMClient


def test_relay_llm_client_lists_models_and_completes() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Bearer test-key"
        if request.method == "GET" and request.url.path == "/v1/models":
            return httpx.Response(200, json={"data": [{"id": "gpt-5.4"}, {"id": "gpt-5.3-codex"}]})
        if request.method == "POST" and request.url.path == "/v1/responses":
            payload = json.loads(request.content.decode("utf-8"))
            assert payload["model"] == "gpt-5.4"
            assert payload["instructions"] == "You are a probe."
            assert payload["input"] == "Ping"
            assert payload["reasoning"] == {"effort": "medium"}
            assert payload["stream"] is True
            return httpx.Response(
                200,
                json={
                    "model": "gpt-5.4",
                    "output": [
                        {
                            "type": "message",
                            "role": "assistant",
                            "content": [{"type": "output_text", "text": "Pong"}],
                        }
                    ],
                    "usage": {"input_tokens": 12, "output_tokens": 3, "total_tokens": 15},
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
    assert completion.usage["output_tokens"] == 3
    assert completion.usage["completion_tokens"] == 3


def test_relay_llm_client_caches_identical_prompt_responses() -> None:
    calls = {"post": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST" and request.url.path == "/v1/responses":
            calls["post"] += 1
            payload = json.loads(request.content.decode("utf-8"))
            assert payload["instructions"] == "You are a probe."
            assert payload["max_output_tokens"] == 32
            assert payload["stream"] is True
            prompt = payload["input"]
            return httpx.Response(
                200,
                json={
                    "model": "gpt-5.4",
                    "output": [
                        {
                            "type": "message",
                            "role": "assistant",
                            "content": [{"type": "output_text", "text": f"reply:{prompt}:{calls['post']}"}],
                        }
                    ],
                    "usage": {"input_tokens": 10, "output_tokens": 4, "total_tokens": 14},
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


def test_relay_llm_client_accepts_plain_text_success_body() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST" and request.url.path == "/v1/responses":
            return httpx.Response(
                200,
                text="Pong",
                headers={"content-type": "text/plain; charset=utf-8"},
            )
        raise AssertionError(f"Unexpected request: {request.method} {request.url}")

    transport = httpx.MockTransport(handler)
    client = RelayLLMClient(LLMConfig(api_key="test-key", max_retries=1), transport=transport)

    try:
        completion = client.prompt("You are a probe.", "Ping")
    finally:
        client.close()

    assert completion.content == "Pong"
    assert completion.model == "gpt-5.4"


def test_relay_llm_client_falls_back_to_chat_completions_when_responses_body_is_empty() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST" and request.url.path == "/v1/responses":
            payload = json.loads(request.content.decode("utf-8"))
            if payload["stream"] is True:
                return httpx.Response(
                    200,
                    text=(
                        'event: response.completed\n'
                        'data: {"type":"response.completed","response":{"model":"gpt-5.4","status":"completed","output":[],"usage":{"input_tokens":12,"output_tokens":3,"total_tokens":15}}}\n\n'
                    ),
                    headers={"content-type": "text/event-stream"},
                )
            if payload["stream"] is False:
                return httpx.Response(
                    200,
                    json={
                        "model": "gpt-5.4",
                        "status": "completed",
                        "output": [],
                        "usage": {"input_tokens": 12, "output_tokens": 3, "total_tokens": 15},
                    },
                )
        if request.method == "POST" and request.url.path == "/v1/chat/completions":
            payload = json.loads(request.content.decode("utf-8"))
            assert payload["messages"][1]["content"] == "Ping"
            assert payload["reasoning_effort"] == "medium"
            assert payload["max_completion_tokens"] == 32
            assert "max_tokens" not in payload
            return httpx.Response(
                200,
                json={
                    "model": "gpt-5.4",
                    "choices": [
                        {
                            "index": 0,
                            "message": {"role": "assistant", "content": "Pong"},
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": {"prompt_tokens": 12, "completion_tokens": 3, "total_tokens": 15},
                },
            )
        raise AssertionError(f"Unexpected request: {request.method} {request.url}")

    transport = httpx.MockTransport(handler)
    client = RelayLLMClient(LLMConfig(api_key="test-key", max_retries=1), transport=transport)

    try:
        completion = client.prompt("You are a probe.", "Ping", max_tokens=32)
    finally:
        client.close()

    assert completion.content == "Pong"
    assert completion.usage["completion_tokens"] == 3
    assert completion.raw["_codeingme"]["endpoint"] == "/chat/completions"
    assert completion.raw["_codeingme"]["fallback_errors"] == [
        "/responses returned HTTP 200 JSON without usable text content. status='stream' usage={\"output_tokens\": 3, \"total_tokens\": 15}",
        "/responses returned HTTP 200 JSON without usable text content. status='completed' usage={\"output_tokens\": 3, \"total_tokens\": 15}",
    ]


def test_relay_llm_client_falls_back_to_streaming_responses_when_non_stream_body_is_empty() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST" and request.url.path == "/v1/responses":
            payload = json.loads(request.content.decode("utf-8"))
            if payload["stream"] is True:
                return httpx.Response(
                    200,
                    text=(
                        'event: response.output_text.delta\n'
                        'data: {"type":"response.output_text.delta","delta":"Po"}\n\n'
                        'event: response.output_text.delta\n'
                        'data: {"type":"response.output_text.delta","delta":"ng"}\n\n'
                        'event: response.completed\n'
                        'data: {"type":"response.completed","response":{"model":"gpt-5.4","status":"completed","output":[{"type":"message","role":"assistant","content":[{"type":"output_text","text":"Pong"}]}],"usage":{"input_tokens":12,"output_tokens":3,"total_tokens":15}}}\n\n'
                        "data: [DONE]\n\n"
                    ),
                    headers={"content-type": "text/event-stream"},
                )
            if payload["stream"] is False:
                return httpx.Response(
                    200,
                    json={
                        "model": "gpt-5.4",
                        "status": "completed",
                        "output": [],
                        "usage": {"input_tokens": 12, "output_tokens": 3, "total_tokens": 15},
                    },
                )
        if request.method == "POST" and request.url.path == "/v1/chat/completions":
            raise AssertionError("Responses streaming fallback should have succeeded before chat fallback")
        raise AssertionError(f"Unexpected request: {request.method} {request.url}")

    transport = httpx.MockTransport(handler)
    client = RelayLLMClient(LLMConfig(api_key="test-key", max_retries=1), transport=transport)

    try:
        completion = client.prompt("You are a probe.", "Ping")
    finally:
        client.close()

    assert completion.content == "Pong"
    assert completion.usage["completion_tokens"] == 3
    assert completion.raw["_codeingme"]["endpoint"] == "/responses"
    assert completion.raw["_codeingme"]["streamed"] is True
    assert "fallback_errors" not in completion.raw["_codeingme"]


def test_relay_llm_client_does_not_treat_missing_chat_content_as_string_none() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST" and request.url.path == "/v1/responses":
            payload = json.loads(request.content.decode("utf-8"))
            if payload["stream"] is True:
                return httpx.Response(
                    200,
                    text=(
                        'event: response.completed\n'
                        'data: {"type":"response.completed","response":{"model":"gpt-5.4","status":"completed","output":[],"usage":{"input_tokens":12,"output_tokens":3,"total_tokens":15}}}\n\n'
                    ),
                    headers={"content-type": "text/event-stream"},
                )
            if payload["stream"] is False:
                return httpx.Response(
                    200,
                    json={
                        "model": "gpt-5.4",
                        "status": "completed",
                        "output": [],
                        "usage": {"input_tokens": 12, "output_tokens": 3, "total_tokens": 15},
                    },
                )
        if request.method == "POST" and request.url.path == "/v1/chat/completions":
            payload = json.loads(request.content.decode("utf-8"))
            if payload["stream"] is False:
                return httpx.Response(
                    200,
                    json={
                        "model": "gpt-5.4",
                        "choices": [
                            {
                                "index": 0,
                                "message": {"role": "assistant"},
                                "finish_reason": "stop",
                            }
                        ],
                        "usage": {"prompt_tokens": 12, "completion_tokens": 3, "total_tokens": 15},
                    },
                )
            return httpx.Response(
                200,
                text='data: {"model":"gpt-5.4","choices":[{"delta":{}}],"usage":{"prompt_tokens":12,"completion_tokens":3,"total_tokens":15}}\n\n',
                headers={"content-type": "text/event-stream"},
            )
        raise AssertionError(f"Unexpected request: {request.method} {request.url}")

    transport = httpx.MockTransport(handler)
    client = RelayLLMClient(LLMConfig(api_key="test-key", max_retries=1), transport=transport)

    try:
        with pytest.raises(RuntimeError, match="without usable text content"):
            client.prompt("You are a probe.", "Ping")
    finally:
        client.close()


def test_llm_config_from_env_falls_back_to_openai_base_url(monkeypatch) -> None:
    monkeypatch.delenv("CODEINGME_LLM_API_KEY", raising=False)
    monkeypatch.delenv("CODEINGME_LLM_BASE_URL", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "openai-key")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://example-proxy.test/v1")

    config = LLMConfig.from_env()

    assert config is not None
    assert config.api_key == "openai-key"
    assert config.base_url == "https://example-proxy.test/v1"


def test_llm_config_from_env_prefers_codeingme_base_url(monkeypatch) -> None:
    monkeypatch.delenv("CODEINGME_LLM_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "openai-key")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://example-proxy.test/v1")
    monkeypatch.setenv("CODEINGME_LLM_BASE_URL", "https://project-proxy.test/v1")

    config = LLMConfig.from_env()

    assert config is not None
    assert config.base_url == "https://project-proxy.test/v1"


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
            title="Build a tasks backend module",
            summary="Build a tasks backend module",
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
        title="Build a warehouse dispatch tasks backend module",
        summary="Build a warehouse dispatch tasks backend module with explicit completion visibility",
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
        title="Build a warehouse dispatch tasks backend module",
        summary="Build a warehouse dispatch tasks backend module with listing and completion state",
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
                                    "from fastapi import FastAPI\n"
                                    "from pydantic import BaseModel\n\n"
                                    "app = FastAPI()\n"
                                    "class Task(BaseModel):\n"
                                    "    id: int\n"
                                    "    title: str\n"
                                    "    completed: bool\n\n"
                                    "class TaskService:\n"
                                    "    def __init__(self) -> None:\n"
                                    "        self._tasks = [Task(id=1, title='LLM task', completed=True)]\n\n"
                                    "    def list_tasks(self):\n"
                                    "        return list(self._tasks)\n\n"
                                    "task_service = TaskService()\n\n"
                                    "@app.get('/api/tasks')\n"
                                    "def list_tasks():\n"
                                    "    return {'tasks': [task.model_dump() for task in task_service.list_tasks()]}\n"
                                    "```"
                                ),
                            }
                        ],
                        "routes": ["GET /api/tasks"],
                        "imports": ["fastapi.FastAPI", "pydantic.BaseModel"],
                        "risks": ["In-memory demo data only"],
                    }
                ),
            )

    result = BackendAgent().run(_agent_context(FakeLLMClient()))

    assert result.artifacts["generation_mode"] == "llm"
    assert result.artifacts["llm_model"] == "fake-model"
    assert result.artifacts["llm_response_format"] == "json-files"
    assert result.artifacts["routes"] == ["GET /api/tasks"]
    assert result.artifacts["imports"] == ["fastapi.FastAPI", "pydantic.BaseModel"]
    assert result.file_plan is not None
    assert "LLM task" in result.file_plan.patches[1].content
    assert "```" not in result.file_plan.patches[1].content


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
                                    "from fastapi import FastAPI\n"
                                    "from pydantic import BaseModel\n\n"
                                    "app = FastAPI(title='Warehouse Dispatch Board')\n\n"
                                    "class Task(BaseModel):\n"
                                    "    id: int\n"
                                    "    title: str\n"
                                    "    completed: bool\n\n"
                                    "class PickingTaskService:\n"
                                    "    def __init__(self) -> None:\n"
                                    "        self._tasks = [\n"
                                    "            Task(id=101, title='Pick aisle C-14', completed=False),\n"
                                    "            Task(id=102, title='Stage pallet for dock 2', completed=True),\n"
                                    "        ]\n\n"
                                    "    def list_tasks(self):\n"
                                    "        return list(self._tasks)\n\n"
                                    "picking_service = PickingTaskService()\n\n"
                                    "@app.get('/api/tasks')\n"
                                    "def list_tasks():\n"
                                    "    return {'tasks': [task.model_dump() for task in picking_service.list_tasks()]}\n"
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
    assert result.artifacts["routes"] == ["GET /api/tasks"]
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
                                "path": "demo_app/warehouse_dispatch_tasks_api.py",
                                "language": "python",
                                "content": (
                                    "from fastapi import FastAPI\n"
                                    "from pydantic import BaseModel\n\n"
                                    "app = FastAPI(title='Warehouse Dispatch Board')\n\n"
                                    "class WarehouseDispatchTask(BaseModel):\n"
                                    "    id: int\n"
                                    "    title: str\n"
                                    "    completed: bool\n\n"
                                    "class WarehouseDispatchTaskService:\n"
                                    "    def __init__(self) -> None:\n"
                                    "        self._tasks = [\n"
                                    "            WarehouseDispatchTask(id=1, title='Pick dock 4 freight', completed=False),\n"
                                    "            WarehouseDispatchTask(id=2, title='Confirm outbound pallet', completed=True),\n"
                                    "        ]\n\n"
                                    "    def list_warehouse_dispatch_tasks(self):\n"
                                    "        return list(self._tasks)\n\n"
                                    "warehouse_dispatch_task_service = WarehouseDispatchTaskService()\n\n"
                                    "@app.get('/api/warehouse-dispatch-tasks')\n"
                                    "def list_items():\n"
                                    "    return {'warehouse_dispatch_tasks': [task.model_dump() for task in warehouse_dispatch_task_service.list_warehouse_dispatch_tasks()]}\n"
                                ),
                            }
                        ],
                        "routes": ["GET /api/warehouse-dispatch-tasks"],
                        "imports": ["fastapi.FastAPI", "pydantic.BaseModel"],
                        "risks": ["In-memory data only"],
                    }
                ),
            )

    result = BackendAgent().run(
        _agent_context(
            FakeLLMClient(),
            requirement=RequirementSpec(
                title="Build a warehouse dispatch tasks backend module",
                summary="Build a warehouse dispatch tasks backend module with listing and completion state",
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
    assert result.artifacts["routes"] == ["GET /api/warehouse-dispatch-tasks"]
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
                                    "def test_tasks_visibility_rules() -> None:\n"
                                    "    response = client.get(\"/api/tasks\")\n"
                                    "    payload = response.json()\n"
                                    "    tasks = payload[\"tasks\"]\n"
                                    "    assert response.status_code == 200\n"
                                    "    assert any(task[\"completed\"] is True for task in tasks)\n"
                                    "    assert any(task[\"completed\"] is False for task in tasks)\n"
                                ),
                            }
                        ],
                        "tests": ["test_tasks_contract", "test_tasks_visibility_rules"],
                        "risks": ["In-memory data assumptions"],
                    }
                ),
            )

    result = QAAgent().run(_agent_context(FakeLLMClient()), _tests())

    assert result.artifacts["generation_mode"] == "llm"
    assert result.artifacts["tests"] == ["test_tasks_contract", "test_tasks_visibility_rules"]
    assert result.artifacts["llm_response_format"] == "json-files"
    assert result.file_plan is not None
    assert "def test_tasks_visibility_rules()" in result.file_plan.patches[0].content


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
                                    "def test_dispatch_visibility_rules() -> None:\n"
                                    "    payload = client.get('/api/tasks').json()\n"
                                    "    tasks = payload['tasks']\n"
                                    "    assert any(task['completed'] is True for task in tasks)\n"
                                    "    assert any(task['completed'] is False for task in tasks)\n"
                                ),
                            }
                        ],
                        "tests": [],
                        "risks": ["Visibility assumptions depend on fixture data"],
                    }
                ),
            )

    result = QAAgent().run(_agent_context(FakeLLMClient()), _tests())

    assert result.artifacts["generation_mode"] == "llm"
    assert result.artifacts["tests"] == [
        "test_dispatch_contract_shape",
        "test_dispatch_visibility_rules",
    ]
    assert "test_dispatch_visibility_rules" in result.file_plan.patches[0].content


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
                                "path": "tests_generated/test_warehouse_dispatch_tasks_demo.py",
                                "language": "python",
                                "content": (
                                    "from fastapi.testclient import TestClient\n"
                                    "from demo_app.warehouse_dispatch_tasks_api import app\n\n"
                                    "client = TestClient(app)\n\n"
                                    "def test_dispatch_contract_shape() -> None:\n"
                                    "    response = client.get('/api/warehouse-dispatch-tasks')\n"
                                    "    payload = response.json()\n"
                                    "    assert response.status_code == 200\n"
                                    "    assert 'warehouse_dispatch_tasks' in payload\n"
                                    "    assert isinstance(payload['warehouse_dispatch_tasks'], list)\n\n"
                                    "def test_dispatch_visibility_rules() -> None:\n"
                                    "    payload = client.get('/api/warehouse-dispatch-tasks').json()\n"
                                    "    tasks = payload['warehouse_dispatch_tasks']\n"
                                    "    assert any(task['completed'] is True for task in tasks)\n"
                                    "    assert any(task['completed'] is False for task in tasks)\n"
                                ),
                            }
                        ],
                        "tests": [
                            "test_dispatch_contract_shape",
                            "test_dispatch_visibility_rules",
                        ],
                        "risks": ["Visibility assumptions depend on fixture data"],
                    }
                ),
            )

    result = QAAgent().run(
        _agent_context(
            FakeLLMClient(),
            requirement=RequirementSpec(
                title="Build a warehouse dispatch tasks backend module",
                summary="Build a warehouse dispatch tasks backend module with listing and completion state",
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
        _tests("warehouse_dispatch_tasks"),
    )

    assert result.artifacts["generation_mode"] == "llm"
    assert result.artifacts["tests"] == [
        "test_dispatch_contract_shape",
        "test_dispatch_visibility_rules",
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
                            "docker compose up app",
                            "docker compose run --rm test python -m pytest tests_generated",
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


def test_devops_agent_accepts_extra_optional_empty_file_and_uvicorn_variant() -> None:
    class FakeLLMClient:
        def prompt(self, system_prompt: str, user_prompt: str, **_: object) -> LLMCompletion:
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
                                    "FROM python:3.11-slim\n\n"
                                    "WORKDIR /workspace\n"
                                    "ENV PYTHONPATH=/workspace\n"
                                    "RUN python -m pip install --no-cache-dir fastapi httpx pytest uvicorn\n"
                                    "COPY demo_app ./demo_app\n"
                                    "COPY tests_generated ./tests_generated\n"
                                    "CMD [\"uvicorn\", \"demo_app.tasks_api:app\", \"--host\", \"0.0.0.0\", \"--port\", \"8000\"]\n"
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
                                    "    environment:\n"
                                    "      PYTHONPATH: /workspace\n"
                                    "    command: uvicorn demo_app.tasks_api:app --host 0.0.0.0 --port 8000\n"
                                    "  test:\n"
                                    "    build:\n"
                                    "      context: .\n"
                                    "      dockerfile: Dockerfile\n"
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
                            {
                                "path": "demo_app/__init__.py",
                                "language": "python",
                                "content": "",
                            },
                        ],
                        "services": ["app", "test"],
                        "commands": [
                            "docker compose up app",
                            "docker compose run --rm test python -m pytest tests_generated",
                        ],
                        "risks": ["Requires Docker daemon"],
                    }
                ),
            )

    result = DevOpsAgent().run(_agent_context(FakeLLMClient()))

    assert result.artifacts["generation_mode"] == "llm"
    assert result.artifacts["llm_fallback"] == "false"


def test_backend_agent_falls_back_on_invalid_json_response() -> None:
    class FakeLLMClient:
        def prompt(self, system_prompt: str, user_prompt: str, **_: object) -> LLMCompletion:
            return LLMCompletion(model="fake-model", content="not json")

    result = BackendAgent().run(_agent_context(FakeLLMClient()))

    assert result.artifacts["generation_mode"] == "template"
    assert result.artifacts["llm_fallback"] == "true"
    assert "valid JSON object" in result.artifacts["llm_error"]


def test_backend_agent_retries_after_invalid_json_response() -> None:
    calls = {"count": 0}

    class FakeLLMClient:
        def prompt(self, system_prompt: str, user_prompt: str, **_: object) -> LLMCompletion:
            calls["count"] += 1
            if calls["count"] == 1:
                return LLMCompletion(model="fake-model", content="not json")
            return LLMCompletion(
                model="fake-model",
                content=json.dumps(
                    {
                        "summary": "Recovered backend",
                        "files": [
                            {
                                "path": "demo_app/tasks_api.py",
                                "language": "python",
                                "content": (
                                    "from fastapi import FastAPI\n"
                                    "from pydantic import BaseModel\n\n"
                                    "app = FastAPI(title='Tasks Board')\n\n"
                                    "class Task(BaseModel):\n"
                                    "    id: int\n"
                                    "    title: str\n"
                                    "    completed: bool\n\n"
                                    "class TaskService:\n"
                                    "    def list_tasks(self) -> list[Task]:\n"
                                    "        return [\n"
                                    "            Task(id=1, title='Review tasks', completed=False),\n"
                                    "            Task(id=2, title='Confirm completion', completed=True),\n"
                                    "        ]\n\n"
                                    "task_service = TaskService()\n\n"
                                    "@app.get('/api/tasks')\n"
                                    "async def list_items() -> dict[str, list[dict[str, object]]]:\n"
                                    "    return {'tasks': [item.model_dump() for item in task_service.list_tasks()]}\n"
                                ),
                            }
                        ],
                        "routes": ["GET /api/tasks"],
                        "imports": ["fastapi.FastAPI", "pydantic.BaseModel"],
                        "risks": ["In-memory only"],
                    }
                ),
            )

    result = BackendAgent().run(_agent_context(FakeLLMClient()))

    assert calls["count"] == 2
    assert result.artifacts["generation_mode"] == "llm"
    assert result.artifacts["llm_fallback"] == "false"
    assert result.artifacts["llm_attempts"] == "2"
    assert len(result.artifacts["llm_attempt_records"]) == 2
    assert result.artifacts["llm_attempt_records"][0]["success"] is False
    assert result.artifacts["llm_attempt_records"][1]["kind"] == "retry"


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
                        "routes": ["GET /api/tasks"],
                        "imports": ["fastapi.FastAPI"],
                        "risks": [],
                    }
                ),
            )

    result = BackendAgent().run(_agent_context(FakeLLMClient()))

    assert result.artifacts["generation_mode"] == "template"
    assert result.artifacts["llm_fallback"] == "true"
    assert "missing required files" in result.artifacts["llm_error"]


def test_qa_agent_falls_back_to_template_on_llm_error() -> None:
    class BrokenLLMClient:
        def prompt(self, system_prompt: str, user_prompt: str, **_: object) -> LLMCompletion:
            raise RuntimeError("relay unavailable")

    result = QAAgent().run(_agent_context(BrokenLLMClient()), _tests())

    assert result.artifacts["generation_mode"] == "template"
    assert result.artifacts["llm_fallback"] == "true"
    assert "relay unavailable" in result.artifacts["llm_error"]
    assert result.file_plan is not None
    assert "def test_task_contract()" in result.file_plan.patches[0].content
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
            title="Build a tasks backend module",
            summary="Build a tasks backend module",
            acceptance_criteria=["List tasks"],
        ),
        graph_slice=GraphSlice(),
        apis=apis or [APISpec(route="/api/tasks", method="GET", summary="List tasks", response_schema="Task")],
        schemas=schemas or [DataSchema(name="Task", fields={"id": "int", "title": "str", "completed": "bool"})],
        llm_client=llm_client,
    )


def _tests(app_name: str = "tasks") -> list[ContractTestSpec]:
    return [
        ContractTestSpec(
            name=f"{app_name}_contract_test",
            description="contract",
            expected_state="green",
            path=f"tests_generated/test_{app_name}_demo.py",
        )
    ]
