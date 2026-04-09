"""覆盖级联规划逻辑的单元测试。"""

from __future__ import annotations

from codeingme.graph import GraphEdge, GraphEdgeType, GraphNode, GraphStore, NodeKind
from codeingme.orchestrator.cascade import CascadePlanner


def test_topological_order_prioritizes_dependencies() -> None:
    store = _demo_store()

    planner = CascadePlanner(store)
    ordered = planner.topological_order(planner.blast_radius("schema:task"))

    assert ordered.index("schema:task") < ordered.index("api:get:/api/tasks")
    assert ordered.index("api:get:/api/tasks") < ordered.index("service:task_service")
    assert ordered.index("api:get:/api/tasks") < ordered.index("test:tasks_contract")


def test_context_slice_focuses_changed_node_and_adjacent_nodes() -> None:
    store = _demo_store()
    store.upsert_node(GraphNode("ops:deployment", NodeKind.ARTIFACT, "Deployment", "far away"))
    store.add_edge(GraphEdge("ops:deployment", "requirement:root", GraphEdgeType.GENERATES))

    planner = CascadePlanner(store)
    focused = planner.context_slice("api:get:/api/tasks", max_hops=1)

    assert focused.node_ids() == {
        "api:get:/api/tasks",
        "schema:task",
        "service:task_service",
        "test:tasks_contract",
        "requirement:root",
    }


def test_task_context_slice_keeps_upstream_contracts_without_downstream_noise() -> None:
    planner = CascadePlanner(_demo_store())

    focused = planner.task_context_slice("schema:task", "test:tasks_contract")

    assert focused.node_ids() == {
        "schema:task",
        "api:get:/api/tasks",
        "test:tasks_contract",
    }


def test_execution_plan_builds_prioritized_agent_tasks() -> None:
    planner = CascadePlanner(_demo_store())

    plan = planner.execution_plan("schema:task")

    assert plan.ordered_node_ids == [
        "schema:task",
        "api:get:/api/tasks",
        "service:task_service",
        "test:tasks_contract",
    ]
    assert plan.batches == [
        ["schema:task"],
        ["api:get:/api/tasks"],
        ["service:task_service", "test:tasks_contract"],
    ]
    assert [(task.node_id, task.role, task.priority) for task in plan.tasks] == [
        ("schema:task", "backend", 0),
        ("api:get:/api/tasks", "backend", 1),
        ("service:task_service", "backend", 2),
        ("test:tasks_contract", "qa", 2),
    ]
    assert plan.tasks[3].context_node_ids == ["api:get:/api/tasks", "schema:task", "test:tasks_contract"]
    assert plan.tasks[3].dependencies == ["api:get:/api/tasks"]


def test_execution_plan_marks_cycles_without_dropping_nodes() -> None:
    store = GraphStore()
    store.upsert_node(GraphNode("schema:task", NodeKind.DATA_MODEL, "Task", "model"))
    store.upsert_node(GraphNode("api:get:/api/tasks", NodeKind.API_ROUTE, "GET /api/tasks", "route"))
    store.upsert_node(GraphNode("service:task_service", NodeKind.SERVICE, "TaskService", "service"))
    store.add_edge(GraphEdge("api:get:/api/tasks", "schema:task", GraphEdgeType.DEPENDS_ON))
    store.add_edge(GraphEdge("service:task_service", "api:get:/api/tasks", GraphEdgeType.IMPLEMENTS))
    store.add_edge(GraphEdge("schema:task", "service:task_service", GraphEdgeType.DEPENDS_ON))

    plan = CascadePlanner(store).execution_plan("schema:task")

    assert plan.cyclic_node_ids == ["api:get:/api/tasks", "schema:task", "service:task_service"]
    assert [task.node_id for task in plan.tasks] == plan.ordered_node_ids
    assert all(task.cyclic for task in plan.tasks)


def _demo_store() -> GraphStore:
    store = GraphStore()
    store.upsert_node(GraphNode("requirement:root", NodeKind.REQUIREMENT, "Requirement", "root"))
    store.upsert_node(GraphNode("schema:task", NodeKind.DATA_MODEL, "Task", "model"))
    store.upsert_node(GraphNode("api:get:/api/tasks", NodeKind.API_ROUTE, "GET /api/tasks", "route"))
    store.upsert_node(GraphNode("service:task_service", NodeKind.SERVICE, "TaskService", "service"))
    store.upsert_node(GraphNode("test:tasks_contract", NodeKind.TEST_CASE, "tasks_contract", "test"))
    store.add_edge(GraphEdge("requirement:root", "schema:task", GraphEdgeType.GENERATES))
    store.add_edge(GraphEdge("requirement:root", "api:get:/api/tasks", GraphEdgeType.GENERATES))
    store.add_edge(GraphEdge("api:get:/api/tasks", "schema:task", GraphEdgeType.DEPENDS_ON))
    store.add_edge(GraphEdge("service:task_service", "api:get:/api/tasks", GraphEdgeType.IMPLEMENTS))
    store.add_edge(GraphEdge("test:tasks_contract", "api:get:/api/tasks", GraphEdgeType.VERIFIES))
    return store
