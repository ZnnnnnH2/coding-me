"""定义图感知级联修复的规划逻辑。"""

from __future__ import annotations

from dataclasses import dataclass, field

from codeingme.graph import GraphQueryService, GraphSlice, GraphStore, NodeKind


@dataclass(slots=True)
class CascadeTask:
    node_id: str
    role: str
    priority: int
    dependencies: list[str] = field(default_factory=list)
    context_node_ids: list[str] = field(default_factory=list)
    cyclic: bool = False


@dataclass(slots=True)
class CascadePlan:
    changed_node_id: str
    ordered_node_ids: list[str] = field(default_factory=list)
    batches: list[list[str]] = field(default_factory=list)
    cyclic_node_ids: list[str] = field(default_factory=list)
    tasks: list[CascadeTask] = field(default_factory=list)


class CascadePlanner:
    def __init__(self, store: GraphStore) -> None:
        self.query = GraphQueryService(store)

    def blast_radius(self, changed_node_id: str) -> list[str]:
        impacted = self.query.impacted_nodes(changed_node_id)
        return sorted(impacted)

    def topological_order(self, node_ids: list[str]) -> list[str]:
        return self.query.topological_order(set(node_ids))

    def context_slice(self, changed_node_id: str, *, max_hops: int = 1) -> GraphSlice:
        return self.query.slice_around(changed_node_id, max_hops=max_hops)

    def task_context_slice(self, changed_node_id: str, node_id: str) -> GraphSlice:
        return self.query.task_context_slice(changed_node_id, node_id)

    def execution_plan(self, changed_node_id: str) -> CascadePlan:
        impacted = self.query.impacted_nodes(changed_node_id)
        topology = self.query.topology(impacted)
        dependency_map = self.query.dependency_map(impacted)
        batch_indexes = {
            node_id: index
            for index, batch in enumerate(topology.batches)
            for node_id in batch
        }
        tasks = [
            CascadeTask(
                node_id=node_id,
                role=self._role_for(node_id),
                priority=batch_indexes.get(node_id, len(topology.batches)),
                dependencies=sorted(dependency_map.get(node_id, set())),
                context_node_ids=sorted(
                    self.task_context_slice(changed_node_id, node_id).node_ids()
                ),
                cyclic=node_id in topology.cyclic,
            )
            for node_id in topology.ordered
        ]
        return CascadePlan(
            changed_node_id=changed_node_id,
            ordered_node_ids=topology.ordered,
            batches=topology.batches,
            cyclic_node_ids=topology.cyclic,
            tasks=tasks,
        )

    def _role_for(self, node_id: str) -> str:
        node = self.query.store.get_node(node_id)
        if node is None:
            return "backend"
        if node.kind in {NodeKind.DATA_MODEL, NodeKind.API_ROUTE, NodeKind.SERVICE}:
            return "backend"
        if node.kind is NodeKind.UI_COMPONENT:
            return "frontend"
        if node.kind is NodeKind.TEST_CASE:
            return "qa"
        if node.kind is NodeKind.REQUIREMENT:
            return "architect"
        if node.kind is NodeKind.ARTIFACT and node.source is not None:
            if node.source.file_path.endswith(".html"):
                return "frontend"
            if "/test" in node.source.file_path or node.source.file_path.startswith("tests"):
                return "qa"
        return "backend"
