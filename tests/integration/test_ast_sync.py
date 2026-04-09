"""覆盖 AST 同步能力的集成测试。"""

from __future__ import annotations

from codeingme.ast_pipeline import GraphSynchronizer
from codeingme.graph import GraphEdge, GraphEdgeType, GraphNode, GraphStore, NodeKind, SourceLocation


def test_graph_sync_extracts_python_entities_with_source_locations() -> None:
    source = """
class TaskService:
    def list_tasks(self):
        return []
"""
    sync = GraphSynchronizer(GraphStore())

    result = sync.sync_source(source, file_path="demo_app/tasks_api.py")

    assert "demo_app/tasks_api.py::class:TaskService" in result.delta.added_nodes
    assert "demo_app/tasks_api.py::function:TaskService.list_tasks" in result.delta.added_nodes
    assert result.file_path == "demo_app/tasks_api.py"
    task_service = sync.store.get_node("demo_app/tasks_api.py::class:TaskService")
    assert task_service is not None
    assert task_service.kind is NodeKind.SERVICE
    assert task_service.source is not None
    assert task_service.source.file_path == "demo_app/tasks_api.py"
    assert task_service.source.start_line == 2


def test_graph_sync_removes_stale_nodes_for_same_file() -> None:
    initial_source = """
class TaskService:
    def list_tasks(self):
        return []
"""
    updated_source = """
class TaskService:
    def fetch_tasks(self):
        return []
"""
    sync = GraphSynchronizer(GraphStore())

    first = sync.sync_source(initial_source, file_path="demo_app/tasks_api.py")
    second = sync.sync_source(updated_source, file_path="demo_app/tasks_api.py")

    assert "demo_app/tasks_api.py::function:TaskService.list_tasks" in first.delta.added_nodes
    assert "demo_app/tasks_api.py::function:TaskService.fetch_tasks" in second.delta.added_nodes
    assert "demo_app/tasks_api.py::function:TaskService.list_tasks" in second.delta.removed_nodes
    assert sync.store.get_node("demo_app/tasks_api.py::function:TaskService.list_tasks") is None
    assert sync.store.get_node("demo_app/tasks_api.py::function:TaskService.fetch_tasks") is not None


def test_graph_sync_keeps_other_files_intact() -> None:
    left_source = """
class LeftService:
    pass
"""
    right_source = """
class RightService:
    pass
"""
    sync = GraphSynchronizer(GraphStore())

    sync.sync_source(left_source, file_path="left.py")
    sync.sync_source(right_source, file_path="right.py")
    sync.sync_source("class RightService:\n    def run(self):\n        return True\n", file_path="right.py")

    assert sync.store.get_node("left.py::class:LeftService") is not None
    assert sync.store.get_node("right.py::class:RightService") is not None
    assert sync.store.get_node("right.py::function:RightService.run") is not None


def test_graph_sync_does_not_remove_non_ast_nodes_for_same_file() -> None:
    source = """
class TaskService:
    def list_tasks(self):
        return []
"""
    store = GraphStore()
    store.upsert_node(
        GraphNode(
            "service:task_service",
            NodeKind.SERVICE,
            "TaskService",
            "semantic service node",
            source=SourceLocation(file_path="demo_app/tasks_api.py", start_line=1, end_line=10),
        )
    )
    sync = GraphSynchronizer(store)

    result = sync.sync_source(source, file_path="demo_app/tasks_api.py")

    assert "service:task_service" not in result.delta.removed_nodes
    assert sync.store.get_node("service:task_service") is not None
    assert sync.store.get_node("demo_app/tasks_api.py::class:TaskService") is not None


def test_graph_sync_extracts_semantic_edges_for_routes_and_tests() -> None:
    backend_source = """
from fastapi import FastAPI

app = FastAPI()


class TaskService:
    def list_tasks(self):
        return []


task_service = TaskService()


@app.get("/api/tasks")
def list_tasks():
    return {"tasks": task_service.list_tasks()}
"""
    test_source = """
from fastapi.testclient import TestClient

client = TestClient(None)


def test_tasks_e2e():
    response = client.get("/api/tasks")
    assert response.status_code == 200
"""
    store = GraphStore()
    store.upsert_node(GraphNode("api:get:/api/tasks", NodeKind.API_ROUTE, "GET /api/tasks", "contract"))
    sync = GraphSynchronizer(store)

    backend_result = sync.sync_source(backend_source, file_path="demo_app/tasks_api.py")
    test_result = sync.sync_source(test_source, file_path="tests_generated/test_tasks_demo.py")

    assert (
        "demo_app/tasks_api.py::class:TaskService",
        "api:get:/api/tasks",
        GraphEdgeType.IMPLEMENTS.value,
    ) in backend_result.delta.added_edges
    assert (
        "demo_app/tasks_api.py::function:list_tasks",
        "api:get:/api/tasks",
        GraphEdgeType.IMPLEMENTS.value,
    ) in backend_result.delta.added_edges
    assert (
        "tests_generated/test_tasks_demo.py::function:test_tasks_e2e",
        "api:get:/api/tasks",
        GraphEdgeType.VERIFIES.value,
    ) in test_result.delta.added_edges
    assert GraphEdge(
        "demo_app/tasks_api.py::function:list_tasks",
        "api:get:/api/tasks",
        GraphEdgeType.IMPLEMENTS,
    ) in sync.store.edges()
    assert GraphEdge(
        "demo_app/tasks_api.py::class:TaskService",
        "api:get:/api/tasks",
        GraphEdgeType.IMPLEMENTS,
    ) in sync.store.edges()


def test_graph_sync_extracts_async_route_functions() -> None:
    source = """
from fastapi import FastAPI

app = FastAPI()


@app.get("/api/tasks")
async def list_tasks():
    return {"tasks": []}
"""
    store = GraphStore()
    store.upsert_node(GraphNode("api:get:/api/tasks", NodeKind.API_ROUTE, "GET /api/tasks", "contract"))
    sync = GraphSynchronizer(store)

    result = sync.sync_source(source, file_path="demo_app/tasks_api.py")

    assert "demo_app/tasks_api.py::function:list_tasks" in result.delta.added_nodes
    assert (
        "demo_app/tasks_api.py::function:list_tasks",
        "api:get:/api/tasks",
        GraphEdgeType.IMPLEMENTS.value,
    ) in result.delta.added_edges


def test_graph_sync_extracts_verification_edges_from_in_process_helpers() -> None:
    source = """
def _get_json(path):
    return 200, {"tasks": []}


def test_tasks_e2e():
    status_code, payload = _get_json("/api/tasks")
    assert status_code == 200
"""
    store = GraphStore()
    store.upsert_node(GraphNode("api:get:/api/tasks", NodeKind.API_ROUTE, "GET /api/tasks", "contract"))
    sync = GraphSynchronizer(store)

    result = sync.sync_source(source, file_path="tests_generated/test_tasks_demo.py")

    assert (
        "tests_generated/test_tasks_demo.py::function:test_tasks_e2e",
        "api:get:/api/tasks",
        GraphEdgeType.VERIFIES.value,
    ) in result.delta.added_edges


def test_graph_sync_reports_missing_external_references_without_creating_dangling_edges() -> None:
    source = """
from fastapi import FastAPI

app = FastAPI()


@app.get("/api/tasks")
def list_tasks():
    return {"tasks": []}
"""
    sync = GraphSynchronizer(GraphStore())

    result = sync.sync_source(source, file_path="demo_app/tasks_api.py")

    assert result.referenced_node_ids == ["api:get:/api/tasks"]
    assert result.missing_references == ["api:get:/api/tasks"]
    assert GraphEdge(
        "demo_app/tasks_api.py::function:list_tasks",
        "api:get:/api/tasks",
        GraphEdgeType.IMPLEMENTS,
    ) not in sync.store.edges()


def test_graph_sync_removes_stale_edges_for_same_file() -> None:
    initial_source = """
from fastapi.testclient import TestClient

client = TestClient(None)


def test_tasks_e2e():
    response = client.get("/api/tasks")
    assert response.status_code == 200
"""
    updated_source = """
def test_tasks_e2e():
    assert True
"""
    store = GraphStore()
    store.upsert_node(GraphNode("api:get:/api/tasks", NodeKind.API_ROUTE, "GET /api/tasks", "contract"))
    sync = GraphSynchronizer(store)

    first = sync.sync_source(initial_source, file_path="tests_generated/test_tasks_demo.py")
    second = sync.sync_source(updated_source, file_path="tests_generated/test_tasks_demo.py")

    assert (
        "tests_generated/test_tasks_demo.py::function:test_tasks_e2e",
        "api:get:/api/tasks",
        GraphEdgeType.VERIFIES.value,
    ) in first.delta.added_edges
    assert (
        "tests_generated/test_tasks_demo.py::function:test_tasks_e2e",
        "api:get:/api/tasks",
        GraphEdgeType.VERIFIES.value,
    ) in second.delta.removed_edges
    assert GraphEdge(
        "tests_generated/test_tasks_demo.py::function:test_tasks_e2e",
        "api:get:/api/tasks",
        GraphEdgeType.VERIFIES,
    ) not in sync.store.edges()


def test_graph_sync_tracks_updated_nodes_when_source_changes_without_id_change() -> None:
    initial_source = """
def helper():
    return 1
"""
    updated_source = """


def helper():
    return 2
"""
    sync = GraphSynchronizer(GraphStore())

    sync.sync_source(initial_source, file_path="helpers.py")
    result = sync.sync_source(updated_source, file_path="helpers.py")

    assert result.delta.updated_nodes == ["helpers.py::function:helper"]
