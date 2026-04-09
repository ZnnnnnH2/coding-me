"""覆盖 Studio 演示接口与运行管理的单元测试。"""

from __future__ import annotations

import sys
from pathlib import Path

from fastapi.testclient import TestClient

import codeingme.studio as studio
from codeingme.orchestrator import OrchestrationEvent, OrchestrationResult
from codeingme.studio import StudioRunManager, create_app


def _spec_payload() -> dict[str, object]:
    return {
        "files": {
            "openapi.yaml": "openapi: 3.1.0\ninfo:\n  title: Dispatch Tasks API\n  description: Demo bundle\npaths:\n  /api/tasks:\n    get:\n      summary: List tasks\n",
            "schema.sql": "create table tasks (id integer primary key, title text, completed boolean);\n",
            "business_rules.yaml": "- Completed tasks stay visible\n",
            "user_story.md": "# Story\nDispatch coordinators need a backend module.\n",
        }
    }


class _FakeOrchestrator:
    def __init__(self, workspace_root: Path) -> None:
        self.workspace_root = workspace_root

    def run(self, requirement_text: str, event_callback=None) -> OrchestrationResult:
        self.workspace_root.mkdir(parents=True, exist_ok=True)
        app_dir = self.workspace_root / "demo_app"
        app_dir.mkdir(parents=True, exist_ok=True)
        (app_dir / "tasks_api.py").write_text(
            "from fastapi import FastAPI\n"
            "from pydantic import BaseModel\n\n"
            "app = FastAPI(title='Studio Demo')\n\n"
            "class Task(BaseModel):\n"
            "    id: int\n"
            "    title: str\n"
            "    completed: bool\n\n"
            "_ITEMS = [\n"
            "    Task(id=1, title='Dispatch board review', completed=True),\n"
            "    Task(id=2, title='Open lane confirmation', completed=False),\n"
            "]\n\n"
            "@app.get('/api/tasks')\n"
            "async def list_tasks() -> dict:\n"
            "    return {'tasks': [item.model_dump() for item in _ITEMS]}\n",
            encoding="utf-8",
        )
        tests_dir = self.workspace_root / "tests_generated"
        tests_dir.mkdir(parents=True, exist_ok=True)
        (tests_dir / "test_tasks_demo.py").write_text(
            "from fastapi.testclient import TestClient\n"
            "from demo_app.tasks_api import app\n\n"
            "client = TestClient(app)\n\n"
            "def test_task_contract() -> None:\n"
            "    response = client.get('/api/tasks')\n"
            "    payload = response.json()\n"
            "    assert response.status_code == 200\n"
            "    assert 'tasks' in payload\n"
            "    assert isinstance(payload['tasks'], list)\n",
            encoding="utf-8",
        )
        (self.workspace_root / "Dockerfile").write_text(
            "FROM python:3.11-slim\nWORKDIR /workspace\n",
            encoding="utf-8",
        )
        (self.workspace_root / "docker-compose.yml").write_text(
            "services:\n  app:\n    image: python:3.11-slim\n",
            encoding="utf-8",
        )
        (self.workspace_root / ".dockerignore").write_text(
            "__pycache__/\n.pytest_cache/\n",
            encoding="utf-8",
        )
        if event_callback is not None:
            event_callback(
                OrchestrationEvent(
                    stage="state",
                    status="active",
                    state="contract_generation",
                    message="Architect agent is drafting contracts.",
                )
            )
            event_callback(
                OrchestrationEvent(
                    stage="agent",
                    status="started",
                    state="contract_generation",
                    role="architect",
                    message="Generating initial schemas and API contracts.",
                    details={"context_node_ids": ["requirement:root"]},
                )
            )
            event_callback(
                OrchestrationEvent(
                    stage="agent",
                    status="completed",
                    state="contract_generation",
                    role="architect",
                    message="Created initial architecture contract",
                    details={"patch_count": 0, "file_paths": [], "generation_mode": "llm", "patch_diffs": []},
                )
            )
            event_callback(
                OrchestrationEvent(
                    stage="agent",
                    status="started",
                    state="test_red",
                    role="qa",
                    message="Generating red tests and harnesses for the target backend module.",
                    details={"context_node_ids": ["schema:task", "api:get:/api/tasks"]},
                )
            )
            event_callback(
                OrchestrationEvent(
                    stage="agent",
                    status="completed",
                    state="test_red",
                    role="qa",
                    message="Defined backend contract and rule checks",
                    details={
                        "patch_count": 1,
                        "file_paths": ["tests_generated/test_tasks_demo.py"],
                        "generation_mode": "llm",
                        "patch_diffs": [
                            {
                                "path": "tests_generated/test_tasks_demo.py",
                                "operation": "write",
                                "diff": "--- /dev/null\n+++ b/tests_generated/test_tasks_demo.py\n+def test_task_contract(): ...",
                            }
                        ],
                    },
                )
            )
            event_callback(
                OrchestrationEvent(
                    stage="agent",
                    status="started",
                    state="implementation_loop",
                    role="backend",
                    message="Backend agent is generating the FastAPI service and API contract surface.",
                    details={"context_node_ids": ["schema:task", "api:get:/api/tasks"]},
                )
            )
            event_callback(
                OrchestrationEvent(
                    stage="agent",
                    status="completed",
                    state="implementation_loop",
                    role="backend",
                    message="Generated a FastAPI backend module with API contract coverage",
                    details={
                        "patch_count": 2,
                        "file_paths": ["demo_app/tasks_api.py", "demo_app/__init__.py"],
                        "generation_mode": "llm",
                        "patch_diffs": [
                            {
                                "path": "demo_app/tasks_api.py",
                                "operation": "write",
                                "diff": "--- /dev/null\n+++ b/demo_app/tasks_api.py\n+@app.get('/api/tasks')",
                            }
                        ],
                    },
                )
            )
            event_callback(
                OrchestrationEvent(
                    stage="agent",
                    status="started",
                    state="implementation_loop",
                    role="devops",
                    message="DevOps agent is preparing container and runtime wiring for the generated module.",
                    details={"context_node_ids": ["schema:task", "api:get:/api/tasks"]},
                )
            )
            event_callback(
                OrchestrationEvent(
                    stage="agent",
                    status="completed",
                    state="implementation_loop",
                    role="devops",
                    message="Prepared containerized runtime and verification artifacts",
                    details={
                        "patch_count": 3,
                        "file_paths": ["Dockerfile", "docker-compose.yml", ".dockerignore"],
                        "generation_mode": "template",
                        "patch_diffs": [
                            {
                                "path": "Dockerfile",
                                "operation": "write",
                                "diff": "--- /dev/null\n+++ b/Dockerfile\n+FROM python:3.11-slim",
                            }
                        ],
                    },
                )
            )
            event_callback(
                OrchestrationEvent(
                    stage="state",
                    status="active",
                    state="done",
                    message="Run completed successfully.",
                )
            )
        return OrchestrationResult(
            requirement=requirement_text,
            final_state="done",
            states=[
                "intake",
                "contract_generation",
                "test_red",
                "implementation_loop",
                "graph_sync",
                "cascade_update",
                "verification",
                "done",
            ],
            graph_nodes=["requirement:root", "schema:task", "api:get:/api/tasks"],
            blast_radius=["schema:task", "api:get:/api/tasks"],
            cascade_order=["schema:task", "api:get:/api/tasks"],
            cascade_batches=[["schema:task"], ["api:get:/api/tasks"]],
            cascade_tasks=[],
            context_slice_nodes=["schema:task", "api:get:/api/tasks"],
            graph_sync_added=["demo_app/tasks_api.py::function:list_tasks"],
            workspace_root=str(self.workspace_root),
            graph_path=str(self.workspace_root / "graph.json"),
            red_test_output="red test log",
            verification_output="verification log",
            artifacts={
                "architect": {"openapi": "GET /api/tasks", "schema": "Task: id:int, title:str, completed:bool", "generation_mode": "llm"},
                "qa": {
                    "test_file": "tests_generated/test_tasks_demo.py",
                    "generation_mode": "llm",
                    "test_expectations": {"tasks_contract_test": "green"},
                    "llm_attempt_records": [{"attempt": 1, "kind": "initial", "success": True, "model": "fake-model", "response_format": "json-files"}],
                },
                "backend": {
                    "route": "GET /api/tasks",
                    "service": "demo_app/tasks_api.py::class:TaskService",
                    "generation_mode": "llm",
                    "llm_model": "fake-model",
                    "llm_attempt_records": [
                        {"attempt": 1, "kind": "initial", "success": False, "model": "fake-model", "error": "timeout"},
                        {"attempt": 2, "kind": "retry", "success": True, "model": "fake-model", "response_format": "json-files"},
                    ],
                },
                "devops": {"docker": "docker compose up app", "ci": "docker compose run --rm test python -m pytest tests_generated", "generation_mode": "template"},
            },
        )


class _FlakyResumeOrchestrator:
    def __init__(self, workspace_root: Path, state: dict[str, int]) -> None:
        self.workspace_root = workspace_root
        self.state = state

    def run(self, requirement_text: str, event_callback=None) -> OrchestrationResult:
        self.state["run_calls"] = self.state.get("run_calls", 0) + 1
        self.workspace_root.mkdir(parents=True, exist_ok=True)
        if event_callback is not None:
            event_callback(
                OrchestrationEvent(
                    stage="state",
                    status="active",
                    state="contract_generation",
                    message="Architect agent is drafting contracts from the uploaded specification bundle.",
                )
            )
            event_callback(
                OrchestrationEvent(
                    stage="contracts",
                    status="completed",
                    state="contract_generation",
                    message="Contract nodes were synced into the graph store.",
                    details={
                        "schemas": ["Task"],
                        "apis": ["GET /api/tasks"],
                        "schemas_data": [{"name": "Task", "fields": {"id": "int", "title": "str", "completed": "bool"}}],
                        "apis_data": [{"route": "/api/tasks", "method": "GET", "summary": "List tasks", "request_schema": None, "response_schema": "Task"}],
                    },
                )
            )
            event_callback(
                OrchestrationEvent(
                    stage="state",
                    status="active",
                    state="test_red",
                    message="QA agent is drafting failing acceptance tests before implementation begins.",
                )
            )
            event_callback(
                OrchestrationEvent(
                    stage="agent",
                    status="completed",
                    state="test_red",
                    role="qa",
                    message="Defined backend contract and rule checks",
                    details={
                        "artifact": {
                            "test_file": "tests_generated/test_tasks_demo.py",
                            "generation_mode": "llm",
                        },
                        "tests": [
                            {
                                "name": "tasks_contract_test",
                                "description": "contract",
                                "expected_state": "green",
                                "path": "tests_generated/test_tasks_demo.py",
                            },
                            {
                                "name": "tasks_rule_test",
                                "description": "rule",
                                "expected_state": "green",
                                "path": "tests_generated/test_tasks_demo.py",
                            },
                        ],
                        "patch_count": 1,
                        "file_paths": ["tests_generated/test_tasks_demo.py"],
                        "generation_mode": "llm",
                        "patch_diffs": [],
                    },
                )
            )
            event_callback(
                OrchestrationEvent(
                    stage="state",
                    status="active",
                    state="implementation_loop",
                    message="Implementation agents are now generating backend and devops assets.",
                )
            )
        raise RuntimeError("backend stage failed before artifacts were accepted")

    def resume(
        self,
        requirement_text: str,
        *,
        resume_from,
        schemas_data,
        apis_data,
        generated_tests_data,
        prior_artifacts,
        red_test_output="",
        event_callback=None,
    ) -> OrchestrationResult:
        self.state["resume_calls"] = self.state.get("resume_calls", 0) + 1
        app_dir = self.workspace_root / "demo_app"
        app_dir.mkdir(parents=True, exist_ok=True)
        (app_dir / "tasks_api.py").write_text(
            "from fastapi import FastAPI\n"
            "from pydantic import BaseModel\n\n"
            "app = FastAPI(title='Resumed Demo')\n\n"
            "class Task(BaseModel):\n"
            "    id: int\n"
            "    title: str\n"
            "    completed: bool\n\n"
            "_ITEMS = [Task(id=1, title='Resume queue', completed=False), Task(id=2, title='Resume complete', completed=True)]\n\n"
            "@app.get('/api/tasks')\n"
            "async def list_items() -> dict:\n"
            "    return {'tasks': [item.model_dump() for item in _ITEMS]}\n",
            encoding="utf-8",
        )
        (app_dir / "__init__.py").write_text("from .tasks_api import app\n", encoding="utf-8")
        tests_dir = self.workspace_root / "tests_generated"
        tests_dir.mkdir(parents=True, exist_ok=True)
        (tests_dir / "test_tasks_demo.py").write_text(
            "from demo_app.tasks_api import app\n\n"
            "def test_task_contract() -> None:\n"
            "    assert app is not None\n",
            encoding="utf-8",
        )
        (self.workspace_root / "Dockerfile").write_text("FROM python:3.11-slim\n", encoding="utf-8")
        (self.workspace_root / "docker-compose.yml").write_text("services:\n  app:\n    image: python:3.11-slim\n", encoding="utf-8")
        (self.workspace_root / ".dockerignore").write_text("__pycache__/\n", encoding="utf-8")
        (self.workspace_root / "graph.json").write_text("{\"nodes\": [], \"edges\": []}\n", encoding="utf-8")

        if event_callback is not None:
            event_callback(
                OrchestrationEvent(
                    stage="state",
                    status="active",
                    state="implementation_loop",
                    message="Implementation agents are now generating backend and devops assets.",
                )
            )
            event_callback(
                OrchestrationEvent(
                    stage="agent",
                    status="completed",
                    state="implementation_loop",
                    role="backend",
                    message="Generated a FastAPI backend module with API contract coverage",
                    details={
                        "artifact": {
                            "route": "api:get:/api/tasks",
                            "service": "demo_app/tasks_api.py::class:TaskService",
                            "generation_mode": "llm",
                        },
                        "tests": [],
                        "patch_count": 2,
                        "file_paths": ["demo_app/tasks_api.py", "demo_app/__init__.py"],
                        "generation_mode": "llm",
                        "patch_diffs": [],
                    },
                )
            )
            event_callback(
                OrchestrationEvent(
                    stage="agent",
                    status="completed",
                    state="implementation_loop",
                    role="devops",
                    message="Prepared containerized runtime and verification artifacts",
                    details={
                        "artifact": {
                            "docker": "docker compose up app",
                            "ci": "docker compose run --rm test python -m pytest tests_generated",
                            "generation_mode": "llm",
                        },
                        "tests": [],
                        "patch_count": 3,
                        "file_paths": ["Dockerfile", "docker-compose.yml", ".dockerignore"],
                        "generation_mode": "llm",
                        "patch_diffs": [],
                    },
                )
            )
            event_callback(
                OrchestrationEvent(
                    stage="state",
                    status="active",
                    state="done",
                    message="Run completed successfully. Generated backend artifacts are ready to inspect.",
                )
            )
        return OrchestrationResult(
            requirement=requirement_text,
            final_state="done",
            states=["intake", "contract_generation", "test_red", "implementation_loop", "done"],
            graph_nodes=["requirement:root", "schema:task", "api:get:/api/tasks"],
            blast_radius=["schema:task", "api:get:/api/tasks"],
            artifacts={
                "architect": prior_artifacts.get("architect", {"generation_mode": "llm"}),
                "qa": prior_artifacts.get("qa", {"generation_mode": "llm"}),
                "backend": {"route": "api:get:/api/tasks", "generation_mode": "llm"},
                "devops": {"docker": "docker compose up app", "generation_mode": "llm"},
            },
            workspace_root=str(self.workspace_root),
            graph_path=str(self.workspace_root / "graph.json"),
            red_test_output=red_test_output or "red test output",
            verification_output="1 passed",
        )


def test_studio_lists_presets() -> None:
    manager = StudioRunManager()
    client = TestClient(create_app(manager))

    response = client.get("/api/studio/presets")

    assert response.status_code == 200
    payload = response.json()
    assert any(preset["name"] == "task_service" for preset in payload["presets"])
    assert any(preset["display_name"] == "Task Service" for preset in payload["presets"])


def test_studio_home_surfaces_full_demo_panels() -> None:
    client = TestClient(create_app(StudioRunManager()))

    response = client.get("/")

    assert response.status_code == 200
    assert "rollback" in response.text
    assert "工作台" in response.text
    assert 'data-panel="agent"' in response.text
    assert 'data-panel="cascade"' in response.text
    assert 'data-panel="graph"' in response.text
    assert 'data-panel="artifact"' in response.text
    assert "生成文件" in response.text


def test_studio_creates_run_and_serves_generated_backend_files(tmp_path) -> None:
    manager = StudioRunManager(
        repo_root=tmp_path,
        run_inline=True,
        orchestrator_factory=lambda workspace_root: _FakeOrchestrator(workspace_root),
    )
    client = TestClient(create_app(manager))

    response = client.post(
        "/api/studio/runs",
        json=_spec_payload(),
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "succeeded"
    assert payload["current_state"] == "done"
    assert any(event["stage"] == "agent" and event["role"] == "architect" for event in payload["events"])
    assert any(event["stage"] == "agent" and event["role"] == "backend" for event in payload["events"])
    assert any(file["path"] == "demo_app/tasks_api.py" for file in payload["files"])
    assert any(file["path"] == "tests_generated/test_tasks_demo.py" for file in payload["files"])
    assert "frontend" not in payload["result"]["artifacts"]

    file_response = client.get(
        f"/api/studio/runs/{payload['run_id']}/file",
        params={"path": "demo_app/tasks_api.py"},
    )

    assert file_response.status_code == 200
    assert "FastAPI" in file_response.text


def test_studio_rejects_unsupported_file_names(tmp_path) -> None:
    manager = StudioRunManager(repo_root=tmp_path, run_inline=True)
    client = TestClient(create_app(manager))

    response = client.post(
        "/api/studio/runs",
        json={"files": {"notes.txt": "unsupported"}},
    )

    assert response.status_code == 400
    assert "Unsupported file" in response.json()["detail"]


def test_studio_main_uses_process_argv_when_no_explicit_argv(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def _fake_run(target, *, host: str, port: int, reload: bool) -> None:
        captured["target"] = target
        captured["host"] = host
        captured["port"] = port
        captured["reload"] = reload

    import uvicorn

    monkeypatch.setattr(uvicorn, "run", _fake_run)
    monkeypatch.setattr(
        sys,
        "argv",
        ["codeingme-studio", "--host", "0.0.0.0", "--port", "9999"],
    )

    exit_code = studio.main()

    assert exit_code == 0
    assert captured == {
        "target": studio.app,
        "host": "0.0.0.0",
        "port": 9999,
        "reload": False,
    }


def test_studio_lists_persisted_run_history_after_restart(tmp_path) -> None:
    manager = StudioRunManager(
        repo_root=tmp_path,
        run_inline=True,
        orchestrator_factory=lambda workspace_root: _FakeOrchestrator(workspace_root),
    )
    client = TestClient(create_app(manager))

    created = client.post("/api/studio/runs", json=_spec_payload()).json()

    restarted_manager = StudioRunManager(
        repo_root=tmp_path,
        run_inline=True,
        orchestrator_factory=lambda workspace_root: _FakeOrchestrator(workspace_root),
    )
    restarted_client = TestClient(create_app(restarted_manager))

    response = restarted_client.get("/api/studio/runs")

    assert response.status_code == 200
    payload = response.json()
    assert any(run["run_id"] == created["run_id"] for run in payload["runs"])


def test_studio_resumes_failed_run_from_persisted_state(tmp_path) -> None:
    state: dict[str, int] = {}

    def _factory(workspace_root: Path):
        return _FlakyResumeOrchestrator(workspace_root, state)

    manager = StudioRunManager(repo_root=tmp_path, run_inline=True, orchestrator_factory=_factory)
    client = TestClient(create_app(manager))

    failed = client.post("/api/studio/runs", json=_spec_payload()).json()

    assert failed["status"] == "failed"
    assert failed["resume_supported"] is True
    assert failed["resume_from_state"] == "implementation_loop"

    resumed = client.post(f"/api/studio/runs/{failed['run_id']}/resume").json()

    assert resumed["status"] == "succeeded"
    assert resumed["current_state"] == "done"
    assert resumed["result"]["final_state"] == "done"
    assert state["run_calls"] == 1
    assert state["resume_calls"] == 1
