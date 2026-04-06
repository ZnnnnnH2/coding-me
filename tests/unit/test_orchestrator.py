from __future__ import annotations

from dataclasses import dataclass, field

from codeingme.agents import AgentContext
from codeingme.agents.backend import BackendAgent
from codeingme.agents.frontend import FrontendAgent
from codeingme.agents.qa import QAAgent
from codeingme.demo_app import DemoAppBlueprint
from codeingme.orchestrator import CodeingmeOrchestrator
from codeingme.runtime import ContainerTestConfig, FilePatch, FilePatchOperation, FilePatchPlan


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
class _RecordingFrontendAgent(FrontendAgent):
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
    orchestrator = CodeingmeOrchestrator(workspace_root=tmp_path)
    backend = _RecordingBackendAgent()
    frontend = _RecordingFrontendAgent()
    qa = _RecordingQAAgent()
    orchestrator.backend = backend
    orchestrator.frontend = frontend
    orchestrator.qa = qa

    result = orchestrator.run(blueprint.requirement_prompt())

    assert result.final_state == "done"
    assert len(backend.contexts) == 3
    assert len(frontend.contexts) == 2
    assert len(qa.contexts) == 2
    assert {"schema:task", "api:get:/api/tasks"} in backend.contexts
    assert {
        "schema:task",
        "api:get:/api/tasks",
        "demo_app/tasks_api.py::class:TaskService",
        "demo_app/tasks_api.py::function:list_tasks",
    } in backend.contexts
    assert frontend.contexts[-1] == {"schema:task", "ui:task_list", "api:get:/api/tasks"}
    assert qa.contexts[-1] == {
        "schema:task",
        "api:get:/api/tasks",
        "tests_generated/test_tasks_demo.py::function:_get_json",
        "tests_generated/test_tasks_demo.py::function:_get_text",
        "tests_generated/test_tasks_demo.py::function:test_tasks_contract",
        "tests_generated/test_tasks_demo.py::function:test_tasks_e2e",
    }
