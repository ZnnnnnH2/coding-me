from __future__ import annotations

from dataclasses import dataclass, field

from .models import GraphEdgeType
from .slice import GraphSlice
from .store import GraphStore


@dataclass(slots=True)
class TopologyResult:
    ordered: list[str] = field(default_factory=list)
    batches: list[list[str]] = field(default_factory=list)
    cyclic: list[str] = field(default_factory=list)


class GraphQueryService:
    _impact_edge_types = {
        GraphEdgeType.CALLS_API,
        GraphEdgeType.DEPENDS_ON,
        GraphEdgeType.VERIFIES,
        GraphEdgeType.IMPLEMENTS,
    }
    _context_edge_types = _impact_edge_types | {
        GraphEdgeType.GENERATES,
        GraphEdgeType.READS,
        GraphEdgeType.WRITES,
    }
    _task_context_outgoing_edge_types = {
        GraphEdgeType.CALLS_API,
        GraphEdgeType.DEPENDS_ON,
        GraphEdgeType.IMPLEMENTS,
        GraphEdgeType.VERIFIES,
        GraphEdgeType.GENERATES,
    }

    def __init__(self, store: GraphStore) -> None:
        self.store = store

    def impacted_nodes(self, changed_node_id: str) -> set[str]:
        impacted = {changed_node_id}
        impacted.update(
            self.store.reverse_dependencies(
                changed_node_id,
                self._impact_edge_types,
            )
        )
        return impacted

    def slice_for(self, node_ids: set[str]) -> GraphSlice:
        return self.store.slice_from(node_ids)

    def adjacent_node_ids(
        self,
        node_id: str,
        *,
        edge_types: set[GraphEdgeType] | None = None,
        direction: str = "both",
    ) -> set[str]:
        neighbors: set[str] = set()
        for edge in self.store.edges():
            if edge_types is not None and edge.edge_type not in edge_types:
                continue
            if direction in {"both", "outgoing"} and edge.source == node_id:
                neighbors.add(edge.target)
            if direction in {"both", "incoming"} and edge.target == node_id:
                neighbors.add(edge.source)
        return neighbors

    def focused_node_ids(
        self,
        changed_node_id: str,
        *,
        max_hops: int = 1,
        edge_types: set[GraphEdgeType] | None = None,
    ) -> set[str]:
        selected_edge_types = edge_types or self._context_edge_types
        adjacency: dict[str, set[str]] = {}
        for edge in self.store.edges():
            if edge.edge_type not in selected_edge_types:
                continue
            adjacency.setdefault(edge.source, set()).add(edge.target)
            adjacency.setdefault(edge.target, set()).add(edge.source)

        seen = {changed_node_id}
        frontier = {changed_node_id}
        for _ in range(max_hops):
            next_frontier: set[str] = set()
            for node_id in frontier:
                for neighbor in adjacency.get(node_id, set()):
                    if neighbor not in seen:
                        seen.add(neighbor)
                        next_frontier.add(neighbor)
            frontier = next_frontier
            if not frontier:
                break
        return seen

    def slice_around(
        self,
        changed_node_id: str,
        *,
        max_hops: int = 1,
        edge_types: set[GraphEdgeType] | None = None,
    ) -> GraphSlice:
        return self.slice_for(
            self.focused_node_ids(
                changed_node_id,
                max_hops=max_hops,
                edge_types=edge_types,
            )
        )

    def task_context_node_ids(self, changed_node_id: str, focus_node_id: str) -> set[str]:
        selected = {changed_node_id, focus_node_id}
        selected.update(
            self.adjacent_node_ids(
                changed_node_id,
                edge_types=self._task_context_outgoing_edge_types,
                direction="outgoing",
            )
        )
        selected.update(
            self.adjacent_node_ids(
                focus_node_id,
                edge_types=self._task_context_outgoing_edge_types,
                direction="outgoing",
            )
        )
        return selected

    def task_context_slice(self, changed_node_id: str, focus_node_id: str) -> GraphSlice:
        return self.slice_for(self.task_context_node_ids(changed_node_id, focus_node_id))

    def dependency_map(self, node_ids: set[str]) -> dict[str, set[str]]:
        dependencies: dict[str, set[str]] = {node_id: set() for node_id in node_ids}
        for edge in self.store.edges():
            if edge.edge_type not in self._impact_edge_types:
                continue
            if edge.source not in node_ids or edge.target not in node_ids:
                continue
            dependencies[edge.source].add(edge.target)
        return dependencies

    def topological_order(self, node_ids: set[str]) -> list[str]:
        return self.topology(node_ids).ordered

    def topology(self, node_ids: set[str]) -> TopologyResult:
        outgoing: dict[str, set[str]] = {node_id: set() for node_id in node_ids}
        incoming_count = {node_id: 0 for node_id in node_ids}

        for edge in self.store.edges():
            if edge.edge_type not in self._impact_edge_types:
                continue
            if edge.source not in node_ids or edge.target not in node_ids:
                continue
            dependency = edge.target
            dependent = edge.source
            if dependent not in outgoing[dependency]:
                outgoing[dependency].add(dependent)
                incoming_count[dependent] += 1

        queue = sorted(node_id for node_id, count in incoming_count.items() if count == 0)
        ordered: list[str] = []
        batches: list[list[str]] = []
        while queue:
            batch = list(queue)
            batches.append(batch)
            queue = []
            for current in batch:
                ordered.append(current)
                for neighbor in sorted(outgoing[current]):
                    incoming_count[neighbor] -= 1
                    if incoming_count[neighbor] == 0:
                        queue.append(neighbor)
            queue.sort()

        cyclic = sorted(node_ids - set(ordered))
        if cyclic:
            ordered.extend(cyclic)
            batches.append(cyclic)
        return TopologyResult(ordered=ordered, batches=batches, cyclic=cyclic)
