"""覆盖级联规划逻辑的单元测试。"""

from __future__ import annotations

from codeingme.graph import GraphEdge, GraphEdgeType, GraphNode, GraphStore, NodeKind
from codeingme.orchestrator.cascade import CascadePlanner


def test_topological_order_prioritizes_dependencies() -> None:
    store = _demo_store()

    planner = CascadePlanner(store)
    ordered = planner.topological_order(planner.blast_radius("schema:task"))

    assert ordered.index("schema:task") < ordered.index("api:get:/api/tasks")
    assert ordered.index("api:get:/api/tasks") < ordered.index("ui:task_list")
    assert ordered.index("ui:task_list") < ordered.index("test:tasks_e2e")


def test_context_slice_focuses_changed_node_and_adjacent_nodes() -> None:
    store = _demo_store()
    store.upsert_node(GraphNode("ops:deployment", NodeKind.ARTIFACT, "Deployment", "far away"))
    store.add_edge(GraphEdge("ops:deployment", "requirement:root", GraphEdgeType.GENERATES))

    planner = CascadePlanner(store)
    focused = planner.context_slice("api:get:/api/tasks", max_hops=1)

    assert focused.node_ids() == {
        "api:get:/api/tasks",
        "schema:task",
        "ui:task_list",
        "service:task_service",
        "requirement:root",
    }


def test_task_context_slice_keeps_upstream_contracts_without_downstream_noise() -> None:
    planner = CascadePlanner(_demo_store())

    focused = planner.task_context_slice("schema:task", "ui:task_list")

    assert focused.node_ids() == {
        "schema:task",
        "ui:task_list",
        "api:get:/api/tasks",
    }


def test_execution_plan_builds_prioritized_agent_tasks() -> None:
    planner = CascadePlanner(_demo_store())

    plan = planner.execution_plan("schema:task")

    assert plan.ordered_node_ids == [
        "schema:task",
        "api:get:/api/tasks",
        "service:task_service",
        "ui:task_list",
        "test:tasks_e2e",
    ]
    assert plan.batches == [
        ["schema:task"],
        ["api:get:/api/tasks"],
        ["service:task_service", "ui:task_list"],
        ["test:tasks_e2e"],
    ]
    assert [(task.node_id, task.role, task.priority) for task in plan.tasks] == [
        ("schema:task", "backend", 0),
        ("api:get:/api/tasks", "backend", 1),
        ("service:task_service", "backend", 2),
        ("ui:task_list", "frontend", 2),
        ("test:tasks_e2e", "qa", 3),
    ]
    assert plan.tasks[3].context_node_ids == ["api:get:/api/tasks", "schema:task", "ui:task_list"]
    assert plan.tasks[4].dependencies == ["ui:task_list"]


def test_execution_plan_marks_cycles_without_dropping_nodes() -> None:
    store = GraphStore()
    store.upsert_node(GraphNode("schema:task", NodeKind.DATA_MODEL, "Task", "model"))
    store.upsert_node(GraphNode("api:get:/api/tasks", NodeKind.API_ROUTE, "GET /api/tasks", "route"))
    store.upsert_node(GraphNode("ui:task_list", NodeKind.UI_COMPONENT, "TaskList", "component"))
    store.add_edge(GraphEdge("api:get:/api/tasks", "schema:task", GraphEdgeType.DEPENDS_ON))
    store.add_edge(GraphEdge("ui:task_list", "api:get:/api/tasks", GraphEdgeType.CALLS_API))
    store.add_edge(GraphEdge("schema:task", "ui:task_list", GraphEdgeType.DEPENDS_ON))

    plan = CascadePlanner(store).execution_plan("schema:task")

    assert plan.cyclic_node_ids == ["api:get:/api/tasks", "schema:task", "ui:task_list"]
    assert [task.node_id for task in plan.tasks] == plan.ordered_node_ids
    assert all(task.cyclic for task in plan.tasks)


def _demo_store() -> GraphStore:
    store = GraphStore()
    store.upsert_node(GraphNode("requirement:root", NodeKind.REQUIREMENT, "Requirement", "root"))
    store.upsert_node(GraphNode("schema:task", NodeKind.DATA_MODEL, "Task", "model"))
    store.upsert_node(GraphNode("api:get:/api/tasks", NodeKind.API_ROUTE, "GET /api/tasks", "route"))
    store.upsert_node(GraphNode("service:task_service", NodeKind.SERVICE, "TaskService", "service"))
    store.upsert_node(GraphNode("ui:task_list", NodeKind.UI_COMPONENT, "TaskList", "component"))
    store.upsert_node(GraphNode("test:tasks_e2e", NodeKind.TEST_CASE, "tasks_e2e", "test"))
    store.add_edge(GraphEdge("requirement:root", "schema:task", GraphEdgeType.GENERATES))
    store.add_edge(GraphEdge("requirement:root", "api:get:/api/tasks", GraphEdgeType.GENERATES))
    store.add_edge(GraphEdge("api:get:/api/tasks", "schema:task", GraphEdgeType.DEPENDS_ON))
    store.add_edge(GraphEdge("service:task_service", "api:get:/api/tasks", GraphEdgeType.IMPLEMENTS))
    store.add_edge(GraphEdge("ui:task_list", "api:get:/api/tasks", GraphEdgeType.CALLS_API))
    store.add_edge(GraphEdge("test:tasks_e2e", "ui:task_list", GraphEdgeType.VERIFIES))
    return store
