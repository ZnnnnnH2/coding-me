"""覆盖图模型与查询能力的单元测试。"""

from __future__ import annotations

from codeingme.graph import (
    GraphEdge,
    GraphEdgeType,
    GraphNode,
    GraphQueryService,
    GraphStore,
    NodeKind,
    SourceLocation,
)


def test_reverse_dependency_blast_radius() -> None:
    store = GraphStore()
    store.upsert_node(GraphNode("schema:task", NodeKind.DATA_MODEL, "Task", "model"))
    store.upsert_node(GraphNode("api:get:/api/tasks", NodeKind.API_ROUTE, "GET /api/tasks", "route"))
    store.upsert_node(GraphNode("service:task_service", NodeKind.SERVICE, "TaskService", "service"))
    store.add_edge(GraphEdge("api:get:/api/tasks", "schema:task", GraphEdgeType.DEPENDS_ON))
    store.add_edge(GraphEdge("service:task_service", "api:get:/api/tasks", GraphEdgeType.IMPLEMENTS))

    impacted = GraphQueryService(store).impacted_nodes("schema:task")

    assert impacted == {"schema:task", "api:get:/api/tasks", "service:task_service"}


def test_graph_store_round_trips_json(tmp_path) -> None:
    store = GraphStore()
    store.upsert_node(
        GraphNode(
            "schema:task",
            NodeKind.DATA_MODEL,
            "Task",
            "id:int, title:str, completed:bool",
            source=SourceLocation("demo_app/tasks_api.py", 8, 26),
        )
    )
    store.upsert_node(GraphNode("api:get:/api/tasks", NodeKind.API_ROUTE, "GET /api/tasks", "route"))
    store.add_edge(GraphEdge("api:get:/api/tasks", "schema:task", GraphEdgeType.DEPENDS_ON))

    graph_path = tmp_path / "graph.json"
    store.save_json(graph_path)
    reloaded = GraphStore.load_json(graph_path)

    task_node = reloaded.get_node("schema:task")
    assert task_node is not None
    assert task_node.source is not None
    assert task_node.source.file_path == "demo_app/tasks_api.py"
    assert reloaded.edges() == [GraphEdge("api:get:/api/tasks", "schema:task", GraphEdgeType.DEPENDS_ON)]
