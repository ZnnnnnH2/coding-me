from __future__ import annotations

from dataclasses import dataclass, field

from codeingme.agents import AgentContext
from codeingme.agents.backend import BackendAgent
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
class _RecordingQAAgent(QAAgent):
    contexts: list[set[str]] = field(default_factory=list)

    def run(self, context: AgentContext, tests=None):
        self.contexts.append(context.graph_slice.node_ids())
        return super().run(context, tests)


def test_orchestrator_executes_cascade_batches_with_sliced_contexts(tmp_path) -> None:
    blueprint = DemoAppBlueprint()
    orchestrator = CodeingmeOrchestrator(workspace_root=tmp_path)
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
    orchestrator = CodeingmeOrchestrator(workspace_root=tmp_path)
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
