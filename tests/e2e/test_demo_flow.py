from __future__ import annotations

from pathlib import Path

import pytest

from codeingme.agents.backend import BackendAgent
from codeingme.agents.base import AgentContext, AgentResult
from codeingme.demo_app import DemoAppBlueprint
from codeingme.orchestrator import CodeingmeOrchestrator
from codeingme.runtime import FilePatch, FilePatchOperation, FilePatchPlan, PatchApplier


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
    result = CodeingmeOrchestrator(workspace_root=tmp_path).run(blueprint.requirement_prompt())

    assert result.final_state == "done"
    assert "schema:task" in result.graph_nodes
    assert "ui:task_list" in result.blast_radius
    assert "schema:task" in result.cascade_order
    assert result.cascade_batches[0] == ["schema:task"]
    assert result.cascade_tasks[0].node_id == "schema:task"
    assert result.cascade_tasks[0].role == "backend"
    assert "demo_app/tasks_api.py::class:TaskService" in result.graph_nodes
    assert "demo_app/tasks_api.py::function:list_tasks" in result.graph_nodes
    assert "demo_app/tasks_api.py::function:list_tasks" in result.graph_sync_added
    assert "api:get:/api/tasks" in result.context_slice_nodes
    assert "ModuleNotFoundError" in result.red_test_output
    assert "2 passed" in result.verification_output
    assert Path(result.graph_path).exists()
    assert (tmp_path / "Dockerfile").exists()
    assert (tmp_path / "demo_app" / "tasks_api.py").exists()
    assert (tmp_path / "docker-compose.yml").exists()


def test_demo_flow_rolls_back_failed_implementation(tmp_path) -> None:
    blueprint = DemoAppBlueprint()
    orchestrator = CodeingmeOrchestrator(workspace_root=tmp_path)
    orchestrator.backend = BrokenBackendAgent()

    with pytest.raises(RuntimeError, match="Verification failed"):
        orchestrator.run(blueprint.requirement_prompt())

    assert not (tmp_path / "demo_app" / "tasks_api.py").exists()
    assert not (tmp_path / "demo_app" / "__init__.py").exists()
    assert not (tmp_path / "demo_app" / "static" / "task_list.html").exists()
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
    orchestrator._reset_workspace = lambda: None
    recording_applier = RecordingPatchApplier(tmp_path)
    orchestrator.patch_applier = recording_applier

    result = orchestrator.run(blueprint.requirement_prompt())

    implementation_plan = next(plan for plan in recording_applier.applied_plans if plan[0] == "implementation")

    assert result.final_state == "done"
    assert ("demo_app/tasks_api.py", FilePatchOperation.DIFF) in implementation_plan[1]


def test_demo_flow_supports_requirement_specific_bootstrap_specs(tmp_path) -> None:
    requirement = "Build a warehouse dispatch tasks web app with listing and completion state"

    result = CodeingmeOrchestrator(workspace_root=tmp_path).run(requirement)

    backend_source = (tmp_path / "demo_app" / "tasks_api.py").read_text(encoding="utf-8")
    qa_source = (tmp_path / "tests_generated" / "test_tasks_demo.py").read_text(encoding="utf-8")

    assert result.final_state == "done"
    assert "schema:warehousedispatchtask" in result.graph_nodes
    assert "api:get:/api/warehouse-dispatch-tasks" in result.graph_nodes
    assert result.cascade_tasks[0].node_id == "schema:warehousedispatchtask"
    assert "api:get:/api/warehouse-dispatch-tasks" in result.context_slice_nodes
    assert '@app.get("/api/warehouse-dispatch-tasks")' in backend_source
    assert '_get_json("/api/warehouse-dispatch-tasks")' in qa_source
